# Module: `web dashboard`

`hermes dashboard` is a FastAPI/uvicorn server (`hermes_cli/web_server.py`) that serves a React SPA and embeds the real `hermes --tui` in a browser xterm.js terminal over a PTY-backed WebSocket. `hermes serve` is its headless twin: the same server with the SPA build and SPA mount switched off, for pure JSON-RPC/WS clients such as the desktop app.

## Responsibilities

- Serve the REST/WS API surface the dashboard and desktop clients consume (`/api/*`, `/api/ws`, `/api/pty`, `/api/pub`, `/api/events`, `/api/console`).
- Build and mount the Vite SPA bundle out of `web/` into `hermes_cli/web_dist/`, with content-hash-immutable asset caching and reverse-proxy path-prefix rewriting.
- Bridge a browser terminal to a real PTY child process (`hermes --tui`) with byte streaming, in-band resize escapes, and keep-alive reattach.
- Gate non-loopback binds behind a pluggable dashboard-auth provider framework (OAuth, password, OIDC, bearer-token).
- Keep the embedded chat a *thin* surface: the TUI owns the transcript/composer; React renders only the chrome around it.

## Key Files

| Path | Role |
|---|---|
| [`../../../hermes_cli/web_server.py`](../../../hermes_cli/web_server.py) | The `app = FastAPI(...)` object: 131 REST route decorators, 6 `@app.websocket` endpoints, 6 `@app.middleware` hooks, plus `start_server()` and `mount_spa()` |
| [`../../../hermes_cli/main.py`](../../../hermes_cli/main.py) | `cmd_dashboard()` (shared by `dashboard` and `serve`), `_build_web_ui()` / `_do_build_web_ui()` |
| [`../../../hermes_cli/subcommands/dashboard.py`](../../../hermes_cli/subcommands/dashboard.py) | argparse for both verbs; `serve_parser.set_defaults(func=cmd_dashboard, no_open=True, headless_backend=True)` |
| [`../../../hermes_cli/pty_bridge.py`](../../../hermes_cli/pty_bridge.py) | POSIX `PtyBridge` (`ptyprocess` + `fcntl` + `termios`) |
| [`../../../hermes_cli/win_pty_bridge.py`](../../../hermes_cli/win_pty_bridge.py) | `WinPtyBridge`, the native-Windows ConPTY drop-in (`winpty.PtyProcess` / pywinpty) |
| [`../../../hermes_cli/pty_session.py`](../../../hermes_cli/pty_session.py) | `PtySessionRegistry` — keep-alive PTYs that outlive their socket, ring-buffer replay on reattach |
| [`../../../hermes_cli/web_deps.py`](../../../hermes_cli/web_deps.py) | Late-binding dependency seam so extracted routers reach `web_server` state without an import cycle |
| [`../../../hermes_cli/web_routers/`](../../../hermes_cli/web_routers/) | Extracted `APIRouter` modules (see below) |
| [`../../../hermes_cli/dashboard_auth/`](../../../hermes_cli/dashboard_auth/) | Auth-gate framework: ABC, registry, middleware, routes, cookies, WS tickets |
| [`../../../plugins/dashboard_auth/`](../../../plugins/dashboard_auth/) | Bundled provider plugins: `basic`, `drain`, `nous`, `self_hosted` |
| [`../../../web/`](../../../web/) | The SPA source tree (`web/src/`, `web/public/`, `web/package.json`, `web/vite.config.ts`) |
| `hermes_cli/web_dist/` | Build artifact: `index.html`, `assets/`, `fonts/`, `fonts-terminal/`, `favicon.ico` |

## Public API

CLI entry points (both resolve to `cmd_dashboard` in `hermes_cli/main.py`):

- `hermes dashboard [--host] [--port] [--no-open] [--insecure] [--skip-build]` — builds the SPA, mounts it, opens a browser.
- `hermes serve [--host] [--port] [--ssh-session-token-file] [--ssh-owner-nonce]` — headless; no build, no SPA.
- `hermes dashboard register` — nested subparser that registers a self-hosted dashboard OAuth client with Nous Portal.

`start_server()` signature (keyword args, `hermes_cli/web_server.py`): `host="127.0.0.1"`, `port=9119`, `open_browser=True`, `allow_public=False`, `initial_profile=""`, `headless=False`, `ssh_session_token=None`, `ssh_owner_nonce=None`.

Representative REST routes (all read off the decorators; not exhaustive):

| Group | Routes |
|---|---|
| Health/status | `/api/health`, `/api/status`, `/api/system/stats`, `/api/ssh/ownership`, `/api/actions/{name}/status` |
| Config/env | `/api/config`, `/api/config/defaults`, `/api/config/schema`, `/api/env`, `/api/env/reveal`, `/api/egress/status` |
| Models/providers | `/api/model/info`, `/api/model/options`, `/api/model/set`, `/api/model/moa`, `/api/providers/oauth`, `/api/providers/custom-endpoints`, `/api/providers/validate` |
| Files | `/api/files`, `/api/files/read`, `/api/files/upload`, `/api/fs/list`, `/api/fs/read-text`, `/api/fs/write-text`, `/api/fs/git-root` |
| Chat/media | `/api/media`, `/api/chat/image-upload`, `/api/audio/transcribe`, `/api/audio/speak` |
| Ops | `/api/logs`, `/api/ops/dump`, `/api/ops/prompt-size`, `/api/ops/config-migrate`, `/api/hermes/update`, `/api/gateway/restart`, `/api/gateway/drain` |
| Curator/learning | `/api/curator`, `/api/curator/run`, `/api/learning/graph`, `/api/learning/node` |
| Auth gate | `/login`, `/auth/login`, `/auth/callback`, `/auth/logout`, `/auth/password-login`, `/auth/native/token`, `/api/auth/me`, `/api/auth/providers`, `/api/auth/ws-ticket` |
| WebSockets | `/api/pty`, `/api/ws`, `/api/pub`, `/api/events`, `/api/console`, `/api/audio/speak-stream` |

## Internal Structure

### `hermes_cli/web_routers/` — extracted routers

Each module exposes `router = APIRouter()` (plus the extra routers named below) and is mounted with `app.include_router(...)` at the exact point in `web_server.py` module execution where the routes were originally registered, so route-matching order is unchanged. `hermes_cli/web_deps.py` is the late-binding seam for shared `web_server` state (`_SESSION_TOKEN`, `DASHBOARD_HEALTH`, config helpers).

- `cron.py` — `/api/cron/jobs*`, `/api/cron/delivery-targets`, `/api/cron/fire`, `/api/cron/blueprints`.
- `git.py` — `/api/git/status`, `/api/git/worktrees`, `/api/git/branches`, the `/api/git/review/*` PR-workflow cluster.
- `mcp.py` — `/api/mcp/servers*` CRUD + `/api/mcp/servers/{name}/test`, `/api/mcp/oauth/*` flows and callback.
- `profiles.py` — `router` (`/api/profiles*`, soul/model/export/import) and `sessions_router` (`/api/profiles/sessions`, `/api/profiles/sessions/sidebar`, `/api/profiles/projects/tree`).
- `sessions.py` — `list_router` (`/api/sessions`), `search_router` (`/api/sessions/search`), `manage_router` (`/api/sessions/bulk-delete`, …).
- `skills.py` — `hub_router` (`/api/skills/hub/install|uninstall|update|sources`).
- `tools.py` — `router` (`/api/tools/toolsets`, per-platform toolset config/models).

### `hermes_cli/dashboard_auth/` + `plugins/dashboard_auth/`

`base.py` defines the `DashboardAuthProvider(ABC)` contract: `start_login`, `complete_login`, `verify_session`, `refresh_session`, `revoke_session`, plus three capability flags — `supports_password` (adds `complete_password_login`), `supports_token` (adds `verify_token`, consumed by the route-agnostic `token_auth.py` seam), `supports_session`. `registry.py` holds `register_provider` / `get_provider` / `list_providers`; `middleware.py` is the auth gate; `routes.py` is the login/callback/token router; `cookies.py`, `login_page.py`, `public_paths.py`, `prefix.py`, `native_flow.py`, `audit.py`, `ws_tickets.py` are the supporting modules.

Bundled providers (each a `plugins/dashboard_auth/<name>/` dir with `__init__.py` + `plugin.yaml`, registering via `ctx.register_dashboard_auth_provider(provider)`):

- `nous` — `NousDashboardAuthProvider`, Portal OAuth authorization-code + PKCE; registers only when a `client_id` is configured.
- `self_hosted` — `SelfHostedOIDCProvider`, a standards-compliant OIDC relying party (Keycloak, Authentik, Authelia, …).
- `basic` — `BasicAuthProvider`, zero-infrastructure username/password with stateless HMAC-signed sessions.
- `drain` — `DrainSecretProvider`, service-to-service shared bearer secret for the drain-control endpoint (`supports_token = True`).

The gate engages on a non-loopback bind; `start_server()` sets `app.state.auth_required = should_require_auth(host)` and fails closed when no provider is registered. `--insecure` no longer bypasses the gate — passing it on a public bind only logs a warning that it is a no-op.

### Ephemeral `_SESSION_TOKEN` and the WebSocket query-param credential

`_resolve_session_token()` returns `os.environ["HERMES_DASHBOARD_SESSION_TOKEN"]` when the desktop shell injected one, else `secrets.token_urlsafe(32)`. It is process-lifetime (dies with the server), stored in the module global `_SESSION_TOKEN`, and named on REST requests by the header `_SESSION_HEADER_NAME = "X-Hermes-Session-Token"`. `mount_spa()` injects it into `index.html` as `window.__HERMES_DASHBOARD_EMBEDDED_CHAT__` / `__HERMES_BASE_PATH__` / `__HERMES_AUTH_REQUIRED__` alongside `window.__HERMES_SESSION_TOKEN__` — but only in loopback mode; when `app.state.auth_required` is set the token is deliberately **not** injected and the SPA switches to cookie identity via `/api/auth/me`.

Browsers cannot set an `Authorization` header on a WebSocket upgrade, so the credential rides as a query parameter. `_ws_auth_reason()` accepts, per mode:

- loopback — `?token=<_SESSION_TOKEN>`, constant-time compared (`hmac.compare_digest`).
- gated — `?ticket=<single-use>` minted by `POST /api/auth/ws-ticket` (30s TTL, in-memory store in `ws_tickets.py`), or `?internal=<process-credential>` for WS clients the server spawns itself (the embedded-TUI PTY child dialing `/api/ws` and `/api/pub`); the internal credential is multi-use, never expires, and is never injected into any HTML so browser XSS cannot read it. The legacy `?token=` path is unconditionally rejected once the gate is engaged.

Rejections close with distinct codes so the log and the browser banner agree: `4401` bad credential, `4403` host/origin mismatch, `4408` peer not allowed, `4404` embedded chat disabled.

### `hermes_cli/pty_bridge.py` — the PTY bridge

`PtyBridge.spawn(argv, cwd=, env=, cols=, rows=)` wraps `ptyprocess.PtyProcess.spawn(...)` and exposes `read(timeout)` (64 KiB `os.read` on the master fd, `None` at EOF), `write(data)` (loops through short writes), `resize(cols, rows)`, `close()`, `is_alive()`, `pid`. `resize()` packs `struct.pack("HHHH", rows, cols, 0, 0)` and applies it with `fcntl.ioctl(self._fd, termios.TIOCSWINSZ, winsize)`; dimensions are first clamped by `_clamp_dimension()` to `[1, 2000]` cols / `[1, 1000]` rows because a broken probe (WSL2 has reported `columns=131072`) would otherwise raise `struct.error` and leave the TUI laid out for a one-row screen. `close()` signals the whole foreground process group `SIGHUP → SIGTERM → SIGKILL` with a 0.5s grace so helper children (the Python slash worker) are not stranded.

Platform branching lives in `web_server.py`, not in the handler: `if sys.platform.startswith("win")` imports `WinPtyBridge` from `win_pty_bridge.py` (pywinpty/ConPTY), else imports `PtyBridge` from `pty_bridge.py`. Both expose the identical `spawn/read/write/resize/close/is_available` surface, so `pty_ws` needs no platform guards. `pty_bridge.py` itself is POSIX-only — it imports `fcntl`, `termios` and `ptyprocess` at module top and sets `_PTY_AVAILABLE = not sys.platform.startswith("win")`. If *neither* bridge imports, `_PTY_BRIDGE_AVAILABLE` is `False` and `/api/pty` writes a red ANSI banner ("the embedded terminal requires a POSIX PTY… Install Hermes inside WSL2") and closes with code `1011`; the rest of the dashboard keeps working.

`/api/pty` also carries keep-alive semantics: `?attach=<token>` binds the socket to a `PtySessionRegistry` entry (`ttl=30*60`, `max_sessions=16`, `buffer_cap=1 MiB`) whose single drain task ring-buffers output while detached and replays it plus a forced redraw on reattach; without `?attach=`, `_legacy_pump()` runs the original 1:1 socket↔PTY pump that kills the child on disconnect. Inbound frames are matched against `_RESIZE_RE = re.compile(rb"\x1b\[RESIZE:(\d+);(\d+)\]")` and consumed locally — the resize escape never reaches the child's stdin.

### The SPA: `web/`

React 19 + TypeScript 6 on Vite 8, Tailwind CSS 4 via `@tailwindcss/vite`, `react-router` 8, `@nous-research/ui`, tested with vitest 4. Scripts: `dev` (`vite`), `build` (`tsc -b && vite build`), `typecheck`, `lint`/`lint:fix`/`fix`, `preview`, `test`, `check`. `@hermes/shared` is a `file:../apps/shared` workspace link shared with the desktop app. `web/vite.config.ts` sets `build.outDir: "../hermes_cli/web_dist"`, which is why a `cd web && npm run build` lands the artifact the server mounts.

| Dir | Owns |
|---|---|
| `web/src/pages/` | One lazy-loaded page per route: `AnalyticsPage`, `ChannelsPage`, `ChatPage`, `ConfigPage`, `CronPage`, `DocsPage`, `EnvPage`, `FilesPage`, `LogsPage`, `McpPage`, `ModelsPage`, `PairingPage`, `PluginsPage`, `ProfileBuilderPage`, `ProfilesPage`, `SessionsPage`, `SkillsPage`, `SystemPage`, `WebhooksPage` |
| `web/src/components/` | Shared chrome: `ChatSidebar`, `ChatSessionList`, `ModelPickerDialog`, `ModelInfoCard`, `ReasoningPicker`, `SlashPopover`, `ToolsetConfigDrawer`, `SkillEditorDialog`, `HermesConsoleModal`, `AuthWidget`, `ProfileSwitcher`, `ProfileScopeBanner`, `ThemeSwitcher`, `LanguageSwitcher`, `Markdown`, `MemoryPressureBanner`, `ScheduleBuilder`, `OAuthLoginModal`, `PlatformsCard`, `SidebarFooter`, `SidebarStatusStrip`, dialogs |
| `web/src/contexts/` | `ProfileProvider`, `PageHeaderProvider`, `SystemActions` + their context/hook modules (`useProfileScope`, `usePageHeader`, `useSystemActions`) |
| `web/src/hooks/` | `useModalBehavior`, `useSidebarStatus` |
| `web/src/lib/` | Pure, unit-tested helpers: `api.ts` (fetch wrapper + `buildWsUrl`), `gatewayClient.ts`, the `pty-*` family (`pty-reconnect`, `pty-resume-sanitizer`, `pty-scroll`, `pty-composition`, `pty-keyboard-shortcuts`, `pty-mobile-input`, `pty-resume-loading`), `chat-activation`, `dashboard-flags`, `dashboard-auth-reload`, `events-reconnect`, `slashExec`, `schedule`, `session-*`, `model-*`, `format`, `fuzzy`, `clipboard` |
| `web/src/i18n/` | `context.tsx` (`I18nProvider`/`useI18n`), `define-locale.ts`, `types.ts`, and one module per locale (`en`, `de`, `es`, `fr`, `ja`, `ko`, `zh`, `zh-hant`, …) |
| `web/src/plugins/` | The dashboard plugin SDK: `registry.ts`, `PluginPage.tsx`, `slots.ts`, `sdk.d.ts`, `usePlugins.ts` — third-party tabs register against these slots |
| `web/src/themes/` | `context.tsx` (`ThemeProvider`/`useTheme`), `presets.ts` (built-in palettes), `fonts.ts`, `types.ts` |
| `web/public/` | `favicon.ico`, `fonts/`, `fonts-terminal/` — copied verbatim into the dist |

### `ChatPage.tsx` — the xterm.js mount

Imports `Terminal` from `@xterm/xterm` plus four addons, all declared in `web/package.json`: `FitAddon` (`@xterm/addon-fit`), `Unicode11Addon` (`@xterm/addon-unicode11`, for modern wide-character/CJK widths), `WebLinksAddon` (`@xterm/addon-web-links`), and `WebglAddon` (`@xterm/addon-webgl`, the GPU renderer). The socket URL comes from `api.buildWsUrl("/api/pty", params)`, and the page reads `window.__HERMES_SESSION_TOKEN__` for the loopback credential. Reconnect/sanitize policy is delegated to the tested helpers in `web/src/lib/pty-reconnect.ts` and `web/src/lib/pty-resume-sanitizer.ts` rather than living inline.

## Dependencies

- Python: `fastapi` + `uvicorn` (the server); `ptyprocess` (`sys_platform != 'win32'`, POSIX PTY) and `pywinpty` (`sys_platform == 'win32'`, ConPTY) from `pyproject.toml`; plus the stdlib `secrets`/`hmac`/`fcntl`/`termios`/`struct`/`select`/`signal`.
- Node: React 19, Vite 8, TypeScript 6, Tailwind 4, `@xterm/xterm` 6 with the four addons above, `@nous-research/ui`, `@observablehq/plot`, `@react-three/fiber` + `three`, `gsap`, `motion`, `lucide-react`, `qrcode`.
- Runtime coupling: the PTY child is the same `hermes --tui` binary the CLI launches, so every TUI feature ships in the browser for free; the JSON-RPC sidecar is reached through `HERMES_TUI_SIDECAR_URL` set in the PTY env.

## Notable Patterns / Gotchas

- **`dashboard` and `serve` are independent surfaces that share one implementation.** Both parse into `cmd_dashboard`; `serve` differs only by `headless_backend=True`, which makes `cmd_dashboard` export `HERMES_SERVE_HEADLESS=1` and skip `_build_web_ui()`, while `dashboard` pops that env var. `mount_spa()` reads it first: under `serve` it installs a `no_frontend` catch-all returning JSON 404 even if a stale `web_dist/` exists, so only the JSON-RPC/WS/API surface is reachable. Neither verb launches the other. The **only** coupling is a backward-compat fallback in the desktop app: `backendSupportsServe()` in `apps/desktop/electron/main.ts` probes the resolved runtime and, when it predates `serve`, `dashboardFallbackArgs()` in `apps/desktop/electron/backend-command.ts` rewrites the argv to the legacy `dashboard --no-open`.
- **The primary chat experience belongs to the embedded TUI, not to React.** Do not re-implement the transcript, composer, or slash-command flow in the SPA. Structured React UI *around* the terminal is allowed — `ChatSidebar`, `ModelPickerDialog`, and the `ToolCallBlock` renderer in `web/src/pages/SessionsPage.tsx` are the pattern — provided it is not a second chat surface, keeps its state independent of the PTY child's session, and fails non-destructively so the terminal pane survives its own crashes.
- **Immutable assets vs. `no-store` entry point.** Hashed `/assets/*` files are served through `_ImmutableAssetFiles` with `_IMMUTABLE_ASSET_CACHE_CONTROL` (`max-age=31536000, immutable`); `index.html` is always `no-store` so it references current hashes. `serve_css()` intercepts `*.css` to rewrite absolute `url(/fonts/…)` paths when a proxy sends `X-Forwarded-Prefix`, normalised by `hermes_cli/dashboard_auth/prefix.py`.
- **`/api/*` misses are 404 JSON, not SPA fallback.** `serve_spa()` special-cases `api` / `api/…` before falling through to `index.html`; returning HTML 200 there makes JSON clients die on `Unexpected token '<'`.
- **Path-traversal guard.** `serve_spa()` only returns a `FileResponse` when `file_path.resolve().is_relative_to(WEB_DIST.resolve())`, which blocks url-encoded `%2e%2e` escapes.
- **Single-builder lock.** `_build_web_ui()` in `hermes_cli/main.py` serialises concurrent boots under an exclusive `fcntl.flock` on `.web_ui_build.lock` at the repo root; losers serve the existing dist or block for the builder. On Windows there is no `flock`, so it falls through to an unserialized build.
- **Theme-flash shim.** `_render_active_theme_bootstrap_css()` injects a critical-CSS `<style id="hermes-theme-bootstrap">` for *user* themes only (built-ins live in `web/src/themes/presets.ts` and need no shim), so the first paint uses the configured palette instead of flashing the default teal.
- **`--insecure` is a no-op.** It no longer disables the auth gate; a non-loopback bind without a registered provider fails closed.
- **Kanban contributes a tab, not a core page.** [`../../../plugins/kanban/dashboard/`](../../../plugins/kanban/dashboard/) registers `/kanban` through the dashboard plugin SDK (`manifest.json` declares `tab.path`, `entry: dist/index.js`, `css: dist/style.css`, `api: plugin_api.py`). See [delegation-kanban.md](delegation-kanban.md) for the board itself.

## See Also

- [tui.md](tui.md) — the `hermes --tui` / `tui_gateway` pair the dashboard embeds over `/api/pty` + `/api/ws`.
- [desktop.md](desktop.md) — the Electron client that spawns `hermes serve` and owns the `dashboard --no-open` legacy fallback.
- [cli.md](cli.md) — `cmd_dashboard`'s host module and the argparse tree in `hermes_cli/subcommands/dashboard.py`.
