# Module: `tui`

The full-screen terminal UI: a Node/Ink (React) renderer plus a Python JSON-RPC host that owns sessions, tools, model calls and slash-command logic. The two processes speak newline-delimited JSON-RPC over stdio; the dashboard's `/chat` tab embeds the *same* Ink binary through a PTY rather than reimplementing it.

## Responsibilities

- Own the screen in TypeScript (transcript, composer, overlays, prompts, theming) and the agent in Python (`AIAgent`, tools, `SessionDB`).
- Serve one method/event protocol over two transports — stdio (Ink child) and WebSocket (desktop / web / iOS clients) — through the same dispatcher.
- Keep per-session state, agent builds, approvals, clarify/sudo/secret bridges, and the persistent `HermesCLI` slash worker alive across a long conversation.
- Publish the JSON-RPC surface the desktop app and the dashboard sidebar consume, so no second chat surface is ever built.

## Key Files

| Path | Role |
|---|---|
| [`tui_gateway/entry.py`](../../../tui_gateway/entry.py) | stdio host `main()`: emits the `gateway.ready` frame, then `while True: sys.stdin.readline()` → `dispatch()`. Also starts background MCP discovery and installs the dashboard sidecar tee. |
| [`tui_gateway/server.py`](../../../tui_gateway/server.py) | ~15.3K LOC. `_methods` table, `@method` decorator, `_LONG_HANDLERS` + worker pool, `dispatch()`/`handle_request()`, `_emit()`/`_event_frame()`/`_broadcast_global_event()`, `_block()` request/response bridges, `_SlashWorker`, session registry. |
| [`tui_gateway/transport.py`](../../../tui_gateway/transport.py) | `Transport` protocol, `StdioTransport` (peer-gone errno table → `False` → clean exit), `TeeTransport` (stdio primary + best-effort sidecar), `bind_transport`/`current_transport` ContextVar. |
| [`tui_gateway/ws.py`](../../../tui_gateway/ws.py) | WebSocket transport; reuses `server.dispatch` verbatim, emits `gateway.ready` on accept, coalesces `*.delta` frames on a ~30 fps timer. Mounted by `web_server.py` at `/api/ws`. |
| [`tui_gateway/method_ctx.py`](../../../tui_gateway/method_ctx.py) | `HandlerRegistry` — the seam that lets `methods_*.py` hold handler bodies while `install()` rebinds their `__globals__` onto `server`'s namespace. |
| [`ui-tui/src/entry.tsx`](../../../ui-tui/src/entry.tsx) | Node entry: no-TTY bail-out, terminal-mode reset, `GatewayClient` spawn, graceful-exit + memory monitor, lazy `ink.render(<App gw={gw} />)`. |
| [`ui-tui/src/app.tsx`](../../../ui-tui/src/app.tsx) | Thin composition root — `useMainApp(gw)` → `GatewayProvider` → `AppLayout`. No controller logic here. |
| [`ui-tui/src/gatewayClient.ts`](../../../ui-tui/src/gatewayClient.ts) | `GatewayClient`: `spawn(python, ['-m','tui_gateway.entry'])`, stdio framing via `node:readline`, `start()`/`request()`/`kill()`, attach mode (`HERMES_TUI_GATEWAY_URL`), sidecar WS. |
| [`ui-tui/src/gatewayTypes.ts`](../../../ui-tui/src/gatewayTypes.ts) | The `GatewayEvent` discriminated union — the authoritative client-side event catalogue. |
| [`ui-tui/src/theme.ts`](../../../ui-tui/src/theme.ts) + [`src/lib/themeBoot.ts`](../../../ui-tui/src/lib/themeBoot.ts) | Skin/`Theme` resolution fed by the `gateway.ready`/`skin.changed` payloads. |
| [`ui-tui/src/config/env.ts`](../../../ui-tui/src/config/env.ts) | Boot flags read from the launcher's env: `DASHBOARD_TUI_MODE` (`HERMES_TUI_DASHBOARD`), `STARTUP_RESUME_ID`/`STARTUP_QUERY`/`STARTUP_IMAGE`, `MOUSE_TRACKING`, `TERMUX_TUI_MODE`, `SHOW_FPS`. |
| [`ui-tui/scripts/build.mjs`](../../../ui-tui/scripts/build.mjs) | esbuild bundle of `src/entry.tsx` → self-contained `dist/entry.js` (no runtime `node_modules`), inlining the Ink source rather than its prebuilt bundle. |
| [`ui-tui/packages/hermes-ink/`](../../../ui-tui/packages/hermes-ink) | Vendored Ink fork (`@hermes/ink`). |

### `methods_*.py` split

Each module defines handlers under a local `HandlerRegistry` and is installed by `server.py` at import end; the grouping below is the real `@method(...)` distribution.

| Module | Owns |
|---|---|
| [`methods_session.py`](../../../tui_gateway/methods_session.py) | `session.*` lifecycle (`create`, `resume`, `list`, `active_list`, `most_recent`, `interrupt`, `steer`, `branch`, `undo`, `compress`, `history`, `usage`, `status`, `title`, `save`, `close`, `delete`, `activate`, `redirect`, `set_hidden`, `cwd.set`, `context_breakdown`, `workspace.move`), plus `subagent.*`, `spawn_tree.*`, `delegation.*`, `handoff.*`, `billing.*`, `subscription.*`, `pet.*`, `usage.bars`, `terminal.resize`, `verification.status`, `llm.oneshot`, `message.react`, `project.facts`. |
| [`methods_prompt.py`](../../../tui_gateway/methods_prompt.py) | `prompt.submit`, `prompt.background`, and every blocking-prompt responder: `approval.respond`/`approval.pending`/`approval.received`, `clarify.respond`, `sudo.respond`, `secret.respond`, `mcp.setup.respond`, `preview.read.respond`, `terminal.read.respond`, `window.read.respond`, plus attachment ingress (`file.attach`, `image.attach`, `image.attach_bytes`, `image.detach`, `pdf.attach`, `clipboard.paste`, `input.detect_drop`). |
| [`methods_tools.py`](../../../tui_gateway/methods_tools.py) | `slash.exec`, `command.dispatch`, `command.resolve`, `commands.catalog`, `tools.*`, `toolsets.list`, `skills.manage`/`skills.reload`, `plugins.manage`/`plugins.list`, `mcp.catalog`/`mcp.servers.*`, `process.*`, `shell.exec`, `cli.exec`, `cron.manage`, `browser.manage`, `reload.env`/`reload.mcp`, `rollback.*`, `learning.*`, `insights.get`, `agents.list`, `config.show`, `system.battery`. |
| [`methods_complete.py`](../../../tui_gateway/methods_complete.py) | `complete.slash`, `complete.path`, `model.options`, `model.save_key`, `model.disconnect`, `paste.collapse`. |
| [`methods_config.py`](../../../tui_gateway/methods_config.py) | `config.get`, `setup.status`, `setup.runtime_check`, `projects.tree`, `projects.discover_repos`, `projects.record_repos`, `projects.project_sessions`. |
| [`methods_profiles.py`](../../../tui_gateway/methods_profiles.py) | `profiles.list`/`create`/`configure`/`describe`/`get_asset`/`set_asset`. |
| [`methods_images.py`](../../../tui_gateway/methods_images.py) | `image.generate`. |

`server.py` itself still declares ten `@method`s directly: `config.set` plus the `voice.*` (`record`, `toggle`, `tts`) and `wake.*` (`start`, `stop`, `pause`, `resume`, `status`, `feed`) surfaces.

### Support modules

| Path | Role |
|---|---|
| [`event_publisher.py`](../../../tui_gateway/event_publisher.py) | `WsPublisherTransport` — mirrors every PTY-side emit to the dashboard `/api/pub` so the sidebar sees events three processes removed. |
| [`host_supervisor.py`](../../../tui_gateway/host_supervisor.py) | Supervises the `compute_host` child when `dashboard.turn_isolation` is on. |
| [`compute_host.py`](../../../tui_gateway/compute_host.py) | The long-lived child that owns live `AIAgent` objects off the serving process' GIL. |
| [`slash_worker.py`](../../../tui_gateway/slash_worker.py) | The persistent `HermesCLI` child (`-m tui_gateway.slash_worker --session-key …`), JSON-lines `{id,command}` ⇄ `{id,ok,output|error}`, with an orphan watchdog. |
| [`slash_fuzzy.py`](../../../tui_gateway/slash_fuzzy.py) | Tiered description-aware scoring for the slash menu (mirrored client-side in `ui-tui/src/app/slash/fuzzyScore.ts`). |
| [`synthetic_turn.py`](../../../tui_gateway/synthetic_turn.py) | GIL-holding synthetic turn driver for the turn-isolation certification harness. |
| [`turn_marker.py`](../../../tui_gateway/turn_marker.py) | Durable interrupted-turn markers read by `session.resume` to auto-continue. |
| [`render.py`](../../../tui_gateway/render.py) | Optional Python-side renderer bridge; returns `None` when `agent.rich_output` is absent so the TUI falls back to `markdown.tsx`. |
| [`project_tree.py`](../../../tui_gateway/project_tree.py) | Pure project → repo → lane → session tree builder behind `projects.tree`. |
| [`git_probe.py`](../../../tui_gateway/git_probe.py) | Single-flight, cached `git` working-tree/repo-root probing for the gateway. |
| [`mcp_rpc_helpers.py`](../../../tui_gateway/mcp_rpc_helpers.py) | Call-time helpers for `mcp.servers.*` (kept out of `methods_tools` because handlers are rebound onto `server` globals). |
| [`mcp_oauth_sessions.py`](../../../tui_gateway/mcp_oauth_sessions.py) | Session-backed `mcp.servers.oauth.start`/`poll` flows. |
| [`loop_noise.py`](../../../tui_gateway/loop_noise.py) | Collapses benign event-loop teardown tracebacks from forced client disconnects. |
| [`_stdin_recovery.py`](../../../tui_gateway/_stdin_recovery.py) | `handle_spurious_eof()` — POSIX-only repair for a child flipping `O_NONBLOCK` on the shared stdin file description. |

## Public API

**Inbound methods** are the `@method("name")` keys of `server._methods` (~154 across `server.py` + the seven `methods_*.py`). **Outbound events** are `{"jsonrpc":"2.0","method":"event","params":{"type":…,"session_id":…,"payload":…}}` frames built by `_event_frame()` and sent via `_emit()`. Verified event `type` values emitted from `tui_gateway/`:

`gateway.ready`, `skin.changed`, `session.info`, `session.usage`, `message.start`, `message.delta`, `message.interim`, `message.complete`, `thinking.delta`, `reasoning.delta`, `reasoning.available`, `status.update`, `reaction`, `error`, `notification.show`, `notification.clear`, `tool.start`, `tool.generating`, `tool.complete`, `tool.output_risk`, `approval.request`, `clarify.request`, `sudo.request`, `secret.request`, `terminal.read.request`, `preview.read.request`, `window.read.request`, `mcp.setup.request`, `secret.expire`, `sudo.expire`, `clarify.expire`, `terminal.read.expire`, `preview.read.expire`, `window.read.expire`, `mcp.setup.expire`, `browser.progress`, `voice.status`, `voice.transcript`, `voice.interrupted`, `wake.detected`, `moa.phase`, `moa.reference`, `moa.aggregating`, `subagent.start`, `subagent.text`, `subagent.thinking`, `subagent.tool`, `subagent.progress`, `subagent.spawn_requested`, `subagent.complete`.

The seven `.expire` names are spelled literally nowhere in `tui_gateway/`: `server.py:3512` derives each as `f"{event.removesuffix('.request')}.expire"` for whichever of the seven `.request` events above is still unanswered when its wait ends, so a client sees an expiry exactly when the matching request times out. The `subagent.*` names are relayed rather than produced here: all seven originate in [`tools/delegate_tool.py`](../../../tools/delegate_tool.py), where `_relay()` (defined at `:1293`, forwarding to `parent_cb` at `:1301`) emits `subagent.start`, `subagent.complete`, `subagent.text`, `subagent.thinking`, `subagent.tool`, and `subagent.progress` (`:1319`, `:1323`, `:1332`, `:1357`, `:1410`, `:1414`), while `child_progress_cb()` emits `subagent.spawn_requested` (`:1936`) and re-emits the `start`/`text`/`complete` trio on the non-relay path (`:2545`, `:2653`, `:3124`). `tools/delegation_live_log.py` only *compares* these names (`:247`, `:249`, `:251`) — a consumer, not an emitter. `server.py:6044` forwards whatever arrives via `_emit(event_type, sid, payload)`. Do not read `subagent.interrupt` and `subagent.steer` as members of this set: those two are inbound `@method` registrations (`methods_session.py:3231`, `:3242`), not outbound events.

`gatewayClient.ts` additionally synthesises three client-only frames — `gateway.stderr`, `gateway.start_timeout`, `gateway.protocol_error` — and `gatewayTypes.ts` declares further variants (`subagent.spawn_requested`, `background.complete`, `review.summary`, `billing.step_up.verification`, `dashboard.new_session_requested`, `moa.progress`, `secret.expire`/`sudo.expire`) that the union admits without a literal `_emit("…")` site in `tui_gateway/`.

`GatewayClient`'s client surface is three members: `start()`, `request<T>(method, params)`, `kill(reason)`, plus the `'event'`/`'exit'` emitter.

**Env knobs on the launch path** (all read from the launcher's env by the Node process or the gateway child):

| Variable | Effect |
|---|---|
| `HERMES_TUI=1` | Forces the TUI over the classic REPL (below an explicit `--cli`/`--tui` and the TTY gate). |
| `HERMES_TUI_DIR` | Prebuilt bundle root; its `dist/entry.js` is run directly, skipping `npm install`/`build`. Incompatible with `--dev`. |
| `HERMES_TUI_FORCE_BUILD=1` | Ignores the freshness check and always re-runs `npm run build`. |
| `HERMES_NODE`, `HERMES_PYTHON`/`PYTHON`, `HERMES_PYTHON_SRC_ROOT` | Override the resolved Node binary, the interpreter for `-m tui_gateway.entry`, and the Python source root the child is spawned in. |
| `HERMES_TUI_GATEWAY_URL` | Attach mode: connect to an already-running gateway over WS instead of spawning one. |
| `HERMES_TUI_SIDECAR_URL` | Set by the dashboard `/api/pty` handler; enables the `TeeTransport` event mirror. |
| `HERMES_TUI_STARTUP_TIMEOUT_MS` / `HERMES_TUI_RPC_TIMEOUT_MS` | `gateway.ready` wait budget (floor 5000, default 15000) and per-request timeout (floor 30000, default 120000). |
| `HERMES_TUI_GATEWAY_NO_FLUSH=1` | Skips `flush()` after each frame — requires `-u`/`PYTHONUNBUFFERED=1`. |
| `HERMES_TUI_GATEWAY_SHUTDOWN_GRACE_S` | Orderly-shutdown grace before `os._exit(0)` (default `1.0`). |
| `HERMES_TUI_DASHBOARD=1` | Marks the dashboard-embedded child; `entry.tsx` adds `SIGINT` to `ignoredSignals` so Ctrl+C cannot kill the embedded TUI. |
| `HERMES_TUI_RESUME`, `HERMES_TUI_QUERY`, `HERMES_TUI_IMAGE`, `HERMES_TUI_ACTIVE_SESSION_FILE` | Hand-off of the resume target, first prompt, attached image, and the active-session sidecar the launcher writes. |


## Internal Structure

**Process model.** `hermes` decides TUI-vs-REPL in `_resolve_use_tui()` ([`hermes_cli/main.py`](../../../hermes_cli/main.py)): `--cli` → `--tui` → no-TTY bail → `HERMES_TUI=1` → `display.interface`. `_launch_tui()` builds the env, then `_make_tui_argv()` resolves the Node argv — a prebuilt `dist/entry.js` (`HERMES_TUI_DIR`, else the wheel-bundled `hermes_cli/tui_dist/entry.js`) run as `node --expose-gc`, or `--dev` which runs `tsx src/entry.tsx` and refuses `HERMES_TUI_DIR`. The Node process is run with `subprocess.call()` (not `exec`), so Python stays alive to print the resume summary and to relaunch `hermes update` on exit code `42`. The Node process then spawns *its* child: `python -m tui_gateway.entry` with `stdio: ['pipe','pipe','pipe']`.

**Framing.** One JSON object per line in both directions. Requests carry `id`/`method`/`params`; responses settle by `id`; server-push frames carry `method: "event"`. `dispatch()` normalises, then runs fast handlers inline on the reader thread and routes only `_LONG_HANDLERS` (blocking portal/Stripe/`git ls-files` calls) to a small thread pool with a `contextvars.copy_context()` snapshot so the worker writes to the right transport.

**Event routing precedence** in `write_json`: session-scoped transport (by `params.session_id`) → ContextVar-bound transport from the current request → module stdio transport. Global broadcasts use the `_live_transports` registry instead, since a background thread has neither.

**Client layout.** `src/app/` holds the controllers (`useMainApp.ts`, `turnController.ts`, `createGatewayEventHandler.ts`, `createSlashHandler.ts`, `useSessionLifecycle.ts`, `submissionCore.ts`) over nanostores (`turnStore.ts`, `uiStore.ts`, `overlayStore.ts`, `delegationStore.ts`). `src/components/` are the renderers — `appLayout.tsx`, `appChrome.tsx`, `appOverlays.tsx`, `messageLine.tsx`, `streamingAssistant.tsx`, `streamingMarkdown.tsx`, `markdown.tsx`, `thinking.tsx` (`Spinner`, `Thinking`, `ToolTrail`), `textInput.tsx`, `prompts.tsx` (`ApprovalPrompt`, `ClarifyPrompt`, `ConfirmPrompt`), `maskedPrompt.tsx`, `branding.tsx` (`Banner`, `SessionPanel`, `Panel`), `activeSessionSwitcher.tsx`, `modelPicker.tsx`, `todoPanel.tsx`, `queuedMessages.tsx`, `overlay*.tsx`, `themed.tsx`. `src/hooks/` = `useCompletion.ts`, `useGitBranch.ts`, `useInputHistory.ts`, `useQueue.ts`, `useVirtualHistory.ts`. `src/domain/` holds pure logic (`messages.ts`, `roles.ts`, `slash.ts`, `blockLayout.ts`, `attachments.ts`, `usage.ts`, `viewport.ts`, …), `src/protocol/` = `interpolation.ts` + `paste.ts`, `src/lib/` the helpers (`rpc.ts`, `fuzzy.ts`, `resizeCoalescer.ts`, `terminalModes.ts`, `gracefulExit.ts`, `memoryMonitor.ts`, …), `src/sdk/` the widget host (`host.tsx`, `registry.ts`, `userWidgets.ts`, `apps/`), `src/content/` the data (`faces.ts`, `verbs.ts`, `fortunes.ts`, `hotkeys.ts`, `charms.ts`).

**`@hermes/ink`.** A forked Ink published as a local `file:` package: `Box`, `Text`, `ScrollBox`, `Ansi`, `RawAnsi`, `Link`, `AlternateScreen`, `NoSelect`, plus hooks (`useInput`, `useApp`, `useStdin`, `useStdout`, `useStderr`, `useSelection`, `useTerminalViewport`, `useTerminalTitle`, …) and `render`/`createRoot`/`measureElement`. It carries Hermes's mouse-tracking, cursor, hit-test, cache-eviction and stdin-recovery patches; `ink-text-input@6` is overridden to consume it via `package.json` `overrides`.

**Slash flow.** `createSlashHandler.ts` resolves in tiers: local registry (`src/app/slash/registry.ts` ← `commands/{core,session,ops,subscription,topup,wake,setup,debug}.ts`, ~64 commands incl. `/quit`, `/help`, `/clear`, `/model`, `/sessions`, `/skin`, `/status`, `/steer`, `/stop`, `/undo`, `/retry`, `/compress`, `/branch`, `/copy`, `/paste`, `/image`, `/tools`, `/skills`, `/plugins`, `/voice`, `/wake`, `/widgets-reload`) → registered widget apps → backend `commands.catalog` alias/fuzzy resolution → `slash.exec` (the persistent `_SlashWorker`) → `command.dispatch` fallback on rejection. Dispatch results are typed `exec|plugin|alias|skill|send|prefill`; `prefill` refills the composer instead of submitting.

**Dashboard embedding.** `hermes dashboard`'s `/chat` tab mounts [`web/src/pages/ChatPage.tsx`](../../../web/src/pages/ChatPage.tsx), which builds an xterm.js `Terminal` with `WebglAddon` (wide hosts only), `FitAddon`, `Unicode11Addon` and `WebLinksAddon`, and opens `/api/pty?token=…`. Keystrokes go `onData` → WS → PTY master; PTY bytes come back to `term.write()`. Resizes are sent as the private escape `\x1b[RESIZE:<cols>;<rows>]`, matched by `_RESIZE_RE` in [`hermes_cli/web_server.py`](../../../hermes_cli/web_server.py), consumed server-side (never written to the PTY) and applied by `PtyBridge.resize()` via `fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)` in [`hermes_cli/pty_bridge.py`](../../../hermes_cli/pty_bridge.py). Auth is the ephemeral `_SESSION_TOKEN` (`web_server.py`), passed as a query param because browsers cannot set `Authorization` on a WS upgrade; the bridge is POSIX-only (`ptyprocess` + `fcntl` + `termios`), so native Windows closes the socket with a WSL banner. When the tab passes a `channel`, the endpoint injects `HERMES_TUI_SIDECAR_URL` and `HERMES_TUI_DASHBOARD=1` into the child, and `entry._install_sidecar_publisher()` wraps the stdio transport in a `TeeTransport` so events also reach `/api/pub` → `/api/events` → the sidebar (`ChatSidebar.tsx`, `ChatSessionList.tsx`).

## Dependencies

- **Used by:** the `hermes` launcher (`--tui`, `display.interface: tui`), the dashboard `/chat` tab, the Electron desktop app (over `/api/ws`), ACP/IDE clients.
- **Uses:** `run_agent.py` (`AIAgent`), `model_tools.py` (`handle_function_call`), `hermes_state.py` (`SessionDB`), `hermes_cli/` (`HermesCLI` for the slash worker, `skin_engine`, `config`), `cli` module, `hermes_bootstrap.harden_import_path()`, `tools.environments.local.build_subprocess_env`.
- **Node deps:** `@hermes/ink` (local `file:` package), `@hermes/shared` (`file:../apps/shared`), `react` 19, `nanostores`/`@nanostores/react`, `ink-text-input`, `undici`, `unicode-animations`; dev (in `ui-tui/package.json`): `tsx`, `esbuild`, `typescript`, `vitest`, `prettier` — ESLint itself is a workspace-root dev dep consumed via `eslint.config.mjs`.

## Notable Patterns / Gotchas

- **Do not reimplement the primary chat experience in React.** The transcript, the composer (including slash behaviour) and the PTY terminal belong to the embedded `hermes --tui`; anything added in Ink shows up in the dashboard for free. Structured React *around* the TUI is fine as long as it is not a second chat surface and its failures stay non-destructive to the terminal pane — the verified examples are the dashboard's own [`web/src/components/ChatSidebar.tsx`](../../../web/src/components/ChatSidebar.tsx) and [`web/src/components/ChatSessionList.tsx`](../../../web/src/components/ChatSessionList.tsx), which read the sidecar event stream and never own a transcript.
- **One dispatcher, two transports.** `ws.py` calls `server.dispatch` unchanged, so a new RPC or event is automatically available to stdio, WS and the desktop. Adding a second dispatch path is the classic way to fork the protocol.
- **`False` from `Transport.write()` means "peer gone", nothing else.** `entry.py` exits `0` on it, so `StdioTransport` deliberately re-raises non-`_PEER_GONE_ERRNOS` `OSError`s and every `UnicodeEncodeError` so real bugs reach the crash log instead of masquerading as a clean disconnect.
- **Skipping flush needs an unbuffered interpreter.** `HERMES_TUI_GATEWAY_NO_FLUSH=1` only works under `-u`/`PYTHONUNBUFFERED=1`, otherwise frames sit in the pipe buffer and the client hangs waiting for `gateway.ready`.
- **`methods_*.py` handlers are not ordinary imports.** They are re-executed with `types.FunctionType` against `server`'s globals, so `global X` inside a handler still mutates `server` state — and a module-level helper defined in `methods_tools` is unreachable from a rebound body (hence `mcp_rpc_helpers.py`).
- **`_LONG_HANDLERS` is a latency contract.** Anything that does a blocking HTTP/`git` round-trip belongs there; anything that must preserve ordering against the token stream must not.
- **Blocking prompts are `_block()` round-trips, not exceptions.** `_block()` ([`tui_gateway/server.py:3472`](../../../tui_gateway/server.py)) mints an 8-hex `request_id` (`:3473`), parks a `threading.Event` in `_pending` under it (`:3474`, `:3476`), emits the `*.request` frame, then blocks on `ev.wait(timeout)` (`:3487`): `None` waits forever (a `clarify_timeout <= 0` config releases only on a real answer or `session.interrupt`), `0` returns at once, `> 0` is bounded; the default is 300s. On timeout it emits the matching `<kind>.expire`, the name derived as `f"{event.removesuffix('.request')}.expire"` (`:3502-3513`) — but **only for the seven kinds** `secret`, `sudo`, `clarify`, `terminal.read`, `preview.read`, `window.read`, `mcp.setup`. That set is not arbitrary: it is exactly the seven handlers that pass `allow_expired=True` into `_respond()` (`methods_prompt.py:1406`, `:1415`, `:1423`, `:1432`, `:1441`, `:1446`, `:1451`), whose parameter defaults to `False` (`server.py:11712`), so a late reply to any other kind still draws the 4009 `no pending <key> request` (`:11719`). Carry the rule, not the list: *an `.expire` exists exactly where a late reply is tolerated*. The two sets agree today but are maintained independently — the guard set is a literal in `server.py`, the opt-in a per-handler argument in `methods_prompt.py`, and `allow_expired` appears in no test — so a new blocking kind added to only one of them silently loses its expiry signal while CI stays green.
- **`approval` is the one blocking prompt that does not follow this shape**: `approval.request` is emitted by `_emit_approval_request()` (`server.py:1971-1979`) against its own pending registry (`_pending_approval_request_payload()` `:1959`, replayed into a status frame at `:8782`), never through `_block()`; it has no `approval.expire`, and `approval.respond` (`methods_prompt.py:1486-1506`) resolves through `resolve_gateway_approval()` instead of `_respond()`. Read that asymmetry as load-bearing before "fixing" it: `resolve_gateway_approval()` (`tools/approval.py:2634`) returns the number of approvals resolved, and its own docstring states "0 means nothing was pending" — so a late or duplicate `approval.respond` is already idempotent, answering `resolved: 0` rather than erroring. There is no stale-entry failure for an expiry frame to prevent, which is precisely the failure the seven `.expire` notifications exist to avert on the `_respond()` path. And do not trust the comment at `server.py:3497` — it reads "All four blocking bridges — secret, sudo, clarify, terminal.read" above a guard set that now holds seven; the count is stale, the code is the contract.
- **Spurious EOF kills gateways.** A child inheriting fd 0 and setting `O_NONBLOCK` flips it on the *shared* open file description; both `entry.py` and `slash_worker.py` must consult `handle_spurious_eof()` before treating an empty `readline()` as a disconnect.
- **Dev loop:** from `ui-tui/`, the real `package.json` scripts are `dev` (build Ink, then `tsx --watch src/entry.tsx`), `start`, `build` (`scripts/build.mjs` → `dist/entry.js`), `build:ink`, `visual`, `typecheck`, `lint`, `lint:fix`, `fmt`, `fix`, `check`, `test`, `test:watch`. `--dev` is incompatible with `HERMES_TUI_DIR`.

## See Also

- [`web-dashboard.md`](web-dashboard.md) — the `/api/pty`, `/api/ws`, `/api/pub` endpoints and the sidebar that consumes the sidecar tee.
- [`desktop.md`](desktop.md) — the Electron client that reuses `tui_gateway` over `/api/ws` with its own composer.
- [`cli.md`](cli.md) — `_resolve_use_tui()`/`_launch_tui()` and the `COMMAND_REGISTRY` the TUI's slash registry mirrors.
