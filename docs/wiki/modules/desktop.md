# Module: `desktop app`

`apps/desktop` is Hermes' native Electron chat surface — its own composer, transcript, and slash pipeline, **not** the browser dashboard and **not** an embed of `hermes --tui`. It spawns a headless `hermes serve` backend and drives it over the same `tui_gateway` JSON-RPC/WebSocket API every other client uses.

## Responsibilities

- Resolve, validate, spawn, and supervise a local `hermes serve` backend (or dial a remote/cloud gateway), and keep its socket pool alive across profile and connection switches.
- Render the chat experience: `@assistant-ui/react` transcript, composer, pane shell, file browser, previews, voice, settings, first-run onboarding.
- Own the machine-side half: windows, single-instance lock, native FS/git, updates, notifications, SSH/WSL plumbing.
- Curate the backend's command and toolset surface down to what a desktop user can actually act on.
- Expose a narrow, typed capability bridge to the renderer — the renderer never touches Node or Electron directly.

## Key Files

- [`apps/desktop/electron/main.ts`](../../../apps/desktop/electron/main.ts) — Electron orchestration entry (~15.2K lines): backend resolution ladder, spawn, `backendSupportsServe()`, window state, IPC, `app.requestSingleInstanceLock()` (`:14974`).
- [`apps/desktop/electron/backend-command.ts`](../../../apps/desktop/electron/backend-command.ts) — pure argv builders: `serveBackendArgs()`, `dashboardFallbackArgs()`, `sourceDeclaresServe()`.
- [`apps/desktop/electron/preload.ts`](../../../apps/desktop/electron/preload.ts) — the only native bridge: `contextBridge.exposeInMainWorld('hermesDesktop', …)`.
- [`apps/desktop/src/main.tsx`](../../../apps/desktop/src/main.tsx) — renderer root (`HashRouter` + `QueryClientProvider`) plus side-effect store imports.
- [`apps/desktop/src/hermes.ts`](../../../apps/desktop/src/hermes.ts) — `class HermesGateway extends JsonRpcGatewayClient` (`:237`) and the typed RPC facade.
- [`apps/desktop/src/store/gateway.ts`](../../../apps/desktop/src/store/gateway.ts) — gateway registry: primary + secondary socket pool, `requestGatewayForProfile()`, `requestGatewayForAgent()`.
- [`apps/desktop/src/lib/desktop-slash-commands.ts`](../../../apps/desktop/src/lib/desktop-slash-commands.ts) — the load-bearing curation table (see below).
- [`apps/desktop/src/lib/desktop-toolsets.ts`](../../../apps/desktop/src/lib/desktop-toolsets.ts) — `DESKTOP_HIDDEN_TOOLSETS` / `isDesktopToolsetVisible()` for the Settings toolset list.
- [`apps/desktop/src/app/session/hooks/use-prompt-actions/slash.ts`](../../../apps/desktop/src/app/session/hooks/use-prompt-actions/slash.ts) — `runSlash()` / `runExec()` dispatcher.
- [`apps/desktop/AGENTS.md`](../../../apps/desktop/AGENTS.md) + [`apps/desktop/DESIGN.md`](../../../apps/desktop/DESIGN.md) — the scoped engineering and design contracts. Read both before changing the app.

## Public API

There is no published package surface — `package.json` names the package `hermes`, `private: true`, `main: dist/electron-main.mjs`. The load-bearing internal seams are:

- **Transport** — `JsonRpcGatewayClient` and `JsonRpcGatewayError` from [`apps/shared/src/json-rpc-gateway.ts`](../../../apps/shared/src/json-rpc-gateway.ts); `buildHermesWebSocketUrl()`, `resolveGatewayWsUrl()`, `GatewayReauthRequiredError`, `isGatewayReauthRequired()` from [`apps/shared/src/websocket-url.ts`](../../../apps/shared/src/websocket-url.ts).
- **Request helpers** — `requestGateway(method, params)` is a `useCallback` created inside [`app/gateway/hooks/use-gateway-request.ts:106`](../../../apps/desktop/src/app/gateway/hooks/use-gateway-request.ts), **not** a module-level export; on a `not connected`/`connection closed` error it retries once through an OAuth-aware reconnect. Registry-scoped variants are the module-level `requestGatewayForProfile(profile, method, params)` (`store/gateway.ts:514`) and `requestGatewayForAgent(connectionId, profile, method, params)` (`:540`), the latter keyed by a composite `(connectionId, profile)` pool key.
- **Curation predicates** — `resolveDesktopCommand()`, `canonicalDesktopSlashCommand()`, `isDesktopSlashCommand()`, `isDesktopSlashSuggestion()`, `isDesktopSlashExtensionCommand()`, `isPickerCommand()`, `filterDesktopCommandsCatalog()`, `desktopSlashUnavailableMessage()`.
- **Plugin SDK** — [`apps/desktop/src/sdk/index.ts`](../../../apps/desktop/src/sdk/index.ts) (`host.state.*` read-only atoms, curated `host.*` verbs, `host.request` as the gateway door, `ui.*` design tokens); contribution registry at [`apps/desktop/src/contrib/registry.ts`](../../../apps/desktop/src/contrib/registry.ts).
- **Native bridge** — `window.hermesDesktop`, preload-injected and feature-detected at the call site (e.g. `registryBackendScopeKey`-scoped dials guard on `getConnectionFor` and tell the user to update rather than silently misrouting).

## Internal Structure

Three authorities, per `AGENTS.md`: Electron owns the machine, the renderer owns the experience, the backend owns the work. `src/` + `electron/` hold 1,599 `.ts`/`.tsx` files (993 excluding `*.test.*`).

| Dir | Owns |
|---|---|
| `src/app/` | Routes and page-level surfaces — `chat/`, `session/`, `gateway/`, `settings/`, `shell/`, `profiles/`, `cron/`, `skills/`, `messaging/`, `artifacts/`, `agents/`, `command-center/`, `command-palette/`, `overlays/`, `right-sidebar/`, `starmap/`, `learning/`, `pet-generate/`, `pet-overlay/`, `hud/`, `wake-indicator/`, `webhooks/`, `quick-entry/`, `contrib/`, `hooks/`. Route constants (`NEW_CHAT_ROUTE`, `SETTINGS_ROUTE`, …) in `src/app/routes.ts`. |
| `src/store/` | 188 files of feature-owned nanostores (`session.ts`, `gateway.ts`, `composer.ts`, `profile.ts`, `notifications.ts`, `active-work.ts`, …). Shared state lives here, never in a distant component. |
| `src/lib/` | 198 shared pure helpers + adapters (`desktop-fs.ts`, `desktop-git.ts`, `desktop-remote-auth.ts`, `chat-runtime.ts`, `gateway-rpc.ts`, and the two curation modules). |
| `src/components/` | Reusable UI incl. `assistant-ui/`, `pane-shell/`, and the boot/connect/install overlays. |
| `src/hooks/`, `src/themes/`, `src/i18n/`, `src/types/`, `src/debug/` | Generic hooks, theme engine, locale catalogs, shared types, dev-only render counters (aliased to a no-op in non-dev builds). |
| `src/sdk/`, `src/contrib/`, `src/plugins/` | Plugin SDK, contribution registry, bundled plugins (`kanban`, `hermes-bots`, `hello-runtime`). |
| `electron/` | 205 `.ts` files (about half tests) — focused modules beside `main.ts`: `backend-*` (probes, health, env, ownership, ready, start-failure), `connection-*`, `ssh-*`, `windows-*`, `wsl-*`, `update-*`, `window-*`. |
| `e2e/` | Playwright specs (`boot.spec.ts`, `chat.spec.ts`, `onboarding.spec.ts`, …) with `mock-server.ts` + `fixtures.ts`. |
| `scripts/` | Build/pack/diagnostic `.mjs` (`bundle-electron-main.mjs`, `stage-native-deps.mjs`, `test-desktop.mjs`, `diag-*.mjs`, `perf/`). |
| `release/` | Build output — `builder-debug.yml` is config; `linux-unpacked/` is generated, not source. |

Stack, verified in [`apps/desktop/package.json`](../../../apps/desktop/package.json): `electron` 40.10.2 (devDep), `react`/`react-dom` 19.2.7, `nanostores` 1.4.0 + `@nanostores/react` 1.1.0, `@assistant-ui/react` 0.14.24 (+ `@assistant-ui/core` 0.2.23), `@tanstack/react-query` 5.101.2, `react-router` 8.3.0, `@xterm/xterm` 6.0.0, `vite` 8.2.0, `vitest` 4.1.10, `@playwright/test` 1.58.2, `@hermes/shared` pinned as `file:../shared`.

### Process topology

```
Electron main (electron/main.ts)
  ├─ resolves a runtime → spawns `hermes serve --host 127.0.0.1 --port 0`
  │     └─ Python: cmd_dashboard(headless_backend=True) → JSON-RPC/WS only, no SPA
  ├─ preload.ts → contextBridge → window.hermesDesktop
  └─ renderer (src/) ── WebSocket JSON-RPC ──► the child's tui_gateway
```

`--port 0` lets the OS assign an ephemeral port which the child announces on stdout. The app also dials explicit remote/cloud gateways over the same protocol; in that mode the gateway host is the execution boundary — tools, terminal, and file ops run on the remote host, not on the machine showing the window.

## Dependencies

- **Uses:** `@hermes/shared` (transport, WS URL builders, billing/skin types), the `tui_gateway` JSON-RPC server, `hermes serve` (headless `cmd_dashboard`), Electron APIs, native `node-pty`.
- **No dependency on the dashboard frontend.** There is no build or runtime edge from `desktop` to `web/`: no `web/dist` reference in `vite.config.ts`, `package.json`, or `electron/main.ts`. Both apps consume `apps/shared/` (`web/package.json` pins it as `file:../apps/shared`, imported by `web/src/lib/api.ts` and `web/src/lib/gatewayClient.ts`) — shared *code*, not a shared UI.
- **Launched by:** the `hermes desktop` subcommand (aliases `gui`), defined in [`hermes_cli/subcommands/gui.py`](../../../hermes_cli/subcommands/gui.py), plus the packaged installers.
- **Related surfaces:** [tui.md](tui.md) (the dashboard's *embedded* TUI — a different surface that really does mount Ink over a PTY), [web-dashboard.md](web-dashboard.md) (the browser SPA), [gateway.md](gateway.md) (messaging gateway).

## Notable Patterns / Gotchas

### Backend spawn is `serve`, never `dashboard`

`electron/main.ts` builds argv `['--profile', profile, 'serve', '--host', '127.0.0.1', '--port', '0']`. The verified chain:

1. `serve_parser.set_defaults(func=cmd_dashboard, no_open=True, headless_backend=True)` — [`hermes_cli/subcommands/dashboard.py:170`](../../../hermes_cli/subcommands/dashboard.py).
2. `cmd_dashboard` reads `headless_backend` ([`hermes_cli/main.py:11028`](../../../hermes_cli/main.py)), **skips** `_build_web_ui()` and exports `HERMES_SERVE_HEADLESS=1` (`:11207-11210`).
3. `mount_spa()` ([`hermes_cli/web_server.py:17204`](../../../hermes_cli/web_server.py)) checks that flag and takes the no-frontend path even if a stray `web/dist` exists — only the JSON-RPC/WS/API surface is reachable.

`dashboard` and `serve` share `cmd_dashboard` but are independent surfaces; neither launches the other.

### The mid-upgrade `dashboard --no-open` fallback

`serve` is newer than the app, so a new Desktop against an un-upgraded managed install would crash on an unknown subcommand and brick every mid-upgrade user. `backendSupportsServe()` ([`electron/main.ts:2077`](../../../apps/desktop/electron/main.ts)) answers per resolved runtime, memoized in `_serveSupportCache` keyed by `command::root`:

- First it reads the *runtime's own* `hermes_cli/subcommands/dashboard.py` and runs `sourceDeclaresServe()` (`/add_parser\(\s*["']serve["']/`) — a cheap, decisive check.
- On an unreadable source it falls back to a bounded `execProbeSync(cmd, [...prefix, 'serve', '--help'])` sharing `PROBE_TIMEOUT_MS` with the other runtime probes.
- Only on a confirmed miss does `getBackendArgsForRuntime()` (`:2137`) rewrite argv via `dashboardFallbackArgs()` → `dashboard --no-open`, preserving every other argument.

Both forms produce the identical headless gateway; the fallback covers the *command* only and never launches the dashboard UI. Two traps: a false negative is cached for the process lifetime (hence the shared probe budget and timeout-only retry), and `.cmd`/`.bat` shims must carry `shell: true` or `execFileSync` throws `EINVAL` and gets mis-cached as "unsupported".

### Session-surface gating — implemented, with one residual env fallback

Capability that depends on *who is connected* resolves from the **session's source**, not from the backend process env. Verified today:

- `_gui_surface_toolsets()` ([`tui_gateway/server.py:4463`](../../../tui_gateway/server.py)) returns `{"project"}`, plus `"desktop_ui"` when the session platform is `desktop`.
- `_load_enabled_toolsets()` (`:4484`) folds those in on **both** exits: the coding-posture path (`:4510`) and the config-fallback path (`:4627`). Missing the coding arm is the bug that strips the desktop of pane tools inside a repo.
- Both toolsets are deliberately **off** `_HERMES_CORE_TOOLS` ([`toolsets.py:261`](../../../toolsets.py) and `:276`), so this resolver is the *only* gate that exposes them — no other platform pays their schema.
- The renderer supplies the identity: `desktopSessionCreateParams()` sends `source: 'desktop'` ([`use-session-actions/index.ts:205`](../../../apps/desktop/src/app/session/hooks/use-session-actions/index.ts)), which reaches the resolver as `platform_override=_session_source(session)` → `_resolve_agent_platform()` (`:3885`) → `_resolve_session_source()` (`:3872`), which honours an explicit source.

**The anti-pattern is not fully gone.** `_resolve_session_platform()` (`:3846`) still derives `"desktop"` vs `"tui"` from `HERMES_DESKTOP` / `HERMES_DESKTOP_TERMINAL` on the backend process whenever no explicit source is supplied — and `electron/main.ts` does set `HERMES_DESKTOP: '1'` in the spawn env (`:9958`, `:10334`). A remote/cloud gateway that never sees that variable keeps its pane tools *only* because the client tags the session; anything falling through to the env arm silently loses them.

### `check_fn` answers opt-in, never surface

[`tools/desktop_ui.py`](../../../tools/desktop_ui.py) is a pure emitter bridge — `set_emitter()`, `available()`, `emit()` — installed by `_wire_desktop_ui()` ([`tui_gateway/server.py:10237`](../../../tui_gateway/server.py)) and keyed off the turn's `HERMES_UI_SESSION_ID` so an event lands on the window that owns the turn. The eight `toolset="desktop_ui"` tools (`read_terminal`, `close_terminal`, `open_preview`, `read_preview`, `read_window_below`, `focus_pane`, `react_to_message`, `setup_mcp`) mostly register with **no** `check_fn` at all; `react_to_message` alone carries `check_fn=check_react_requirements` ([`tools/react_to_message_tool.py:119`](../../../tools/react_to_message_tool.py)), which reads the user's `display.message_reactions` toggle — correctly an opt-in, not a surface test. `check_fn` results are TTL-cached 30s in [`tools/registry.py`](../../../tools/registry.py) (`_CHECK_FN_TTL_SECONDS`, `_check_fn_cache`), process-wide for single-profile runs and profile-scoped under multiplex via `check_fn_cache_scope()` — one more reason a per-session answer does not belong there.

### Slash curation: one table, three predicates

[`src/lib/desktop-slash-commands.ts`](../../../apps/desktop/src/lib/desktop-slash-commands.ts) is the single source of truth:

- `DESKTOP_COMMAND_SPECS` (`:162`) — the table; each row carries a `DesktopCommandSurface` discriminated union (`action` | `picker` | `rpc` | `exec` | `unavailable`).
- `NO_DESKTOP_SURFACE` (`:337`) — a reason-keyed block-list (`terminal`, `messaging`, `settings`, `advanced`) flattened into `ALL_SPECS` (`:376`, the `NO_DESKTOP_SURFACE` spread at `:378`), so a flat name list replaces ~40 identical object literals.
- `isDesktopSlashCommand()` (`:443`) gates **execution**: true for any spec whose surface isn't `unavailable`, else delegates to the extension check.
- `isDesktopSlashSuggestion()` (`:454`) gates **discovery**: aliases and `hidden: true` specs stay out of the popover.
- `isDesktopSlashExtensionCommand()` (`:432`) is true for anything *not* a known Hermes built-in — i.e. skill and user quick commands.
- `filterDesktopCommandsCatalog()` (`:587`) applies the suggestion gate to `commands.catalog` and recounts `skill_count` from the surviving rows.

Both completion lanes in [`use-slash-completions.ts`](../../../apps/desktop/src/app/chat/composer/hooks/use-slash-completions.ts) use them: the empty-query catalog lane (`:145`) and the typed `complete.slash` lane (`:187`, filtered at `:208`, grouped `'Skills'` vs `'Commands'` at `:213`). Dispatch lives in [`slash.ts`](../../../apps/desktop/src/app/session/hooks/use-prompt-actions/slash.ts): `runSlash()` (`:1045`) resolves the spec, `rpc` surfaces call their dedicated gateway method directly, everything else goes through `runExec()` (`:246`) → `slash.exec` (`:362`) → `command.dispatch` (`:398`). `parseCommandDispatch()` ([`lib/chat-runtime.ts:309`](../../../apps/desktop/src/lib/chat-runtime.ts)) types the reply; a `skill` dispatch submits its `message` as an ordinary prompt.

**The rule:** curation hides *noise* (terminal-only, messaging-only, picker-owned, advanced built-ins) — never user-activated extensions. Tightening the table without letting `isDesktopSlashExtensionCommand` flow into **both** the suggestion and catalog-filter lanes re-introduces the "skills missing from the palette" bug. Test: from `apps/desktop`, `npx vitest run src/lib/desktop-slash-commands.test.ts`.

### Conventions actually in force

Feature-owned nanostores over component state (94 store files import `nanostores`; 161 files use `useStore`); `useStore` where a component renders from an atom, `$atom.get()` for non-rendering reads; persistence beside the atom that owns it; `interface` (not `type`) for public props — e.g. `SlashActionCtx` and `SlashCommandDeps` in `slash.ts`, `DesktopCommandSpec` in the curation module; table-driven dispatch over condition ladders — adding a command adds a row plus a handler keyed by id, never a new `else if`; void-form callbacks for pure side effects (`onState={st => void …}`).

### Test lanes

`vitest.config.ts` defines two projects: `ui` (jsdom, `src/**/*.test.{ts,tsx}`, 15s cold-start timeout) and `electron` (node, `electron/**/*.test.ts`, `scripts/**/*.test.{ts,mjs}`). Run `npm run test:ui`, `npm run test:desktop:platforms`, `npm run test:e2e` (Playwright), and `npm run test:desktop:all` → [`scripts/test-desktop.mjs`](../../../apps/desktop/scripts/test-desktop.mjs) for install/boot/update/packaging changes. Workspace deps install from the **repo root**; `scripts/assert-root-install.mjs` fails the build otherwise.
