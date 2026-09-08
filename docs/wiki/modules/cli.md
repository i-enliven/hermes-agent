# Module: `cli`

The user-facing command layer: the `hermes` process entry point, the argparse subcommand tree, the slash-command registry that every surface derives from, the interactive `HermesCLI` REPL, and the config/skin/curses primitives underneath them.

## Responsibilities

- Turn `argv` into a running agent: profile selection → `.env` load → argparse dispatch → `cmd_*` handler.
- Own the **single source of truth** for slash commands (`COMMAND_REGISTRY`) so CLI, gateway, Telegram, Slack, Discord, and autocomplete all derive from one list.
- Provide the interactive REPL (`HermesCLI`) — prompt_toolkit input area, activity feed, slash dispatch, session loop.
- Serve three distinct config loaders and keep their precedence rules straight.
- Render user-facing chrome: skins, banners, curses menus.

## Key Files

| Path | Role |
|---|---|
| [`../../hermes`](../../../hermes) | 11-line Python launcher (not a shell script): `from hermes_cli.main import main; main()`. The installed console script is `hermes = "hermes_cli.main:main"` in [`pyproject.toml`](../../../pyproject.toml) `[project.scripts]`. |
| [`hermes_cli/main.py`](../../../hermes_cli/main.py) | ~13.7K LOC. `main()`, all `cmd_*` handlers, inline subparsers, profile override. |
| [`hermes_cli/_parser.py`](../../../hermes_cli/_parser.py) | `build_top_level_parser()` → `(parser, subparsers, chat_parser)`; top-level `--version`, `-z/--oneshot`, `--usage-file`, `--model`, `--provider`. |
| [`hermes_cli/subcommands/`](../../../hermes_cli/subcommands) | 45 builder modules plus `__init__.py` and `_shared.py`. Each owns a `build_<group>_parser(subparsers, ...)`; most take the handler as a keyword (`*, cmd_<x>: Callable`). |
| [`hermes_cli/commands.py`](../../../hermes_cli/commands.py) | `CommandDef`, `COMMAND_REGISTRY`, `resolve_command()`, derived consumers, `SlashCommandCompleter`. |
| [`../../cli.py`](../../../cli.py) | ~20.4K LOC god-file. `HermesCLI`, `load_cli_config()`, `save_config_value()`, REPL. |
| [`hermes_cli/config.py`](../../../hermes_cli/config.py) + [`config_defaults.py`](../../../hermes_cli/config_defaults.py) | `load_config()` / merge engine; pure-data `DEFAULT_CONFIG` + `OPTIONAL_ENV_VARS`. |
| [`hermes_cli/skin_engine.py`](../../../hermes_cli/skin_engine.py) | `SkinConfig`, `_BUILTIN_SKINS`, active-skin cache. |
| [`hermes_cli/curses_ui.py`](../../../hermes_cli/curses_ui.py) | Mandatory interactive-menu primitives. |

## Public API

**Startup chain in [`hermes_cli/main.py`](../../../hermes_cli/main.py), order is load-bearing:**

1. `import hermes_bootstrap` (guarded `ModuleNotFoundError`) — Windows UTF-8 stdio; see [`hermes_bootstrap.py`](../../../hermes_bootstrap.py).
2. `suppress_platform_ver_console()` from `hermes_cli._subprocess_compat`.
3. `from hermes_cli import _startup_fast` — stdlib-only fast-path helpers ([`_startup_fast.py`](../../../hermes_cli/_startup_fast.py), 222 LOC; a guard test subprocess-imports it and fails if any heavy module leaks into `sys.modules`).
4. `_early_recovery_mod.recover_if_needed()` in [`_early_recovery.py`](../../../hermes_cli/_early_recovery.py) — stdlib-only venv self-heal that must run **before** any third-party import, or a corrupted venv crashes the import of `main.py` itself.
5. `_apply_profile_override()` — **called at module scope** (line ~692), before `hermes_cli.config` / `env_loader` are imported. Verified: it sets `os.environ["HERMES_HOME"]` and strips `-p/--profile` from `sys.argv`. Resolution order: explicit flag → trust `HERMES_HOME` whose parent is named `profiles` → `active_profile` file (skipped under `HERMES_S6_SUPERVISED_CHILD`) → `resolve_profile_env()`. Guards: value-flag skip table, `mcp add --args` passthrough detection, profile-name regex, `sudo`/`SUDO_USER` home fallback.
6. `main()` (line ~12176): `_set_process_title()` → `_advertise_agent_env()` → Windows stdio → quarantined-`.exe` sweep → stale-bytecode sweep → `_recover_from_interrupted_install()` (skipped when `"update"` in `argv`) → three fast-launch short-circuits (`_try_termux_fast_tui_launch`, `_try_termux_fast_cli_launch`, `_try_fast_chat_launch`) → `build_top_level_parser()` → subparser registration → dispatch.

**Provider discovery is lazy, not startup work.** `_discover_providers()` lives in [`providers/__init__.py`](../../../providers/__init__.py) behind a `_discovered` flag and is triggered by `get_provider_profile()` / `list_providers()`. Scan order: pip entry points → bundled `plugins/model-providers/` → `$HERMES_HOME/plugins/model-providers/` → legacy `providers/<name>.py`; `register_provider()` is last-writer-wins.

**`hermes_cli/subcommands/` inventory** (verb → module → purpose; all verified against the module's `subparsers.add_parser(...)` call):

| Verb | File | Purpose |
|---|---|---|
| `acp` | `acp.py` | Run as an ACP (Agent Client Protocol) server for editor integration |
| `approvals` | `approvals.py` | Approval-prompt tools; mine history into allowlist proposals |
| `auth` | `auth.py` | Manage pooled provider credentials |
| `backup` / `import` | `backup.py` / `import_cmd.py` | Zip the Hermes home / restore from one |
| `claw` | `claw.py` | OpenClaw migration tools |
| `config` | `config.py` | `show`/`edit`/`get`/`set`/`unset`/`path`/`env-path`/`check`/`migrate` |
| `console` | `console.py` | Open the safe Hermes command console |
| `cron` | `cron.py` | Job CRUD, `tick`, `runs`, `status` |
| `dashboard`, `serve` | `dashboard.py` | Web UI dashboard / headless backend server |
| `debug`, `dump`, `doctor` | `debug.py`, `dump.py`, `doctor.py` | Support uploads, setup summary, health check |
| `gateway`, `proxy` | `gateway.py` | Messaging-gateway service control / inbound OAuth reverse proxy |
| `desktop` (alias `gui`) | `gui.py` | Build and launch the Electron desktop app |
| `hooks` | `hooks.py` | Inspect and manage shell-script hooks |
| `import-agent` | `import_agent.py` | Import a Claude Code or Codex CLI setup |
| `insights` | `insights.py` | Usage insights and analytics |
| `login`, `logout`, `logs` | `login.py`, `logout.py`, `logs.py` | Provider auth + log browsing |
| `mcp` | `mcp.py` | Manage MCP servers; run Hermes as an MCP server |
| `memory` | `memory.py` | Configure an external memory provider |
| `model` | `model.py` | Select default model and provider |
| `monitoring` | `monitoring.py` | Gateway health and diagnostics export |
| `pairing` | `pairing.py` | DM pairing codes for user authorization |
| `pause`, `resume` | `pause.py` | Emergency stop / lift of cron, kanban, gateway turns |
| `peer` | `peer.py` | Bot-to-bot DMs across peer gateways |
| `plugins` | `plugins.py` | Manage and validate plugins |
| `profile` | `profile.py` | Manage isolated profiles |
| `prompt-size` | `prompt_size.py` | Byte breakdown of system prompt + tool schemas |
| `security` | `security.py` | OSV.dev supply-chain audit |
| `setup` | `setup.py` | Interactive setup wizard |
| `skills`, `sync` | `skills.py`, `sync.py` | Skills Hub install/manage; cross-device skill sync |
| `skin` | `skin.py` | `list`/`use`/tweak skins |
| `slack`, `whatsapp` | `slack.py`, `whatsapp.py` | Platform setup helpers |
| `status`, `verify`, `version` | `status.py`, `verify.py`, `version.py` | Component status / project run-recipe smoke test / version |
| `tools` | `tools.py` | Per-platform tool enablement (curses UI) |
| `uninstall`, `update` | `uninstall.py`, `update.py` | Self-management |
| `webhook` | `webhook.py` | Dynamic webhook subscriptions |

Verbs registered **inline in `main()`** instead of a builder module (grep `subparsers.add_parser` there): `moa`, `fallback`, `secrets`, `egress`, `migrate`, `whatsapp-cloud`, plus parsers supplied by `hermes_cli/kanban.py::build_parser`, `hermes_cli/projects_cmd.py`, `hermes_cli/portal_cli.py`, `hermes_cli/send_cmd.py`, and the optional `agent.lsp.cli`.

**Slash-command registry** — [`hermes_cli/commands.py`](../../../hermes_cli/commands.py):

- `CommandDef` (frozen dataclass, line ~87): `name`, `description`, `category`, `aliases`, `args_hint`, `subcommands`, `cli_only`, `gateway_only`, `gateway_config_gate`, `busy_policy`, `busy_handler`, `execute`. `VALID_BUSY_POLICIES = {"dispatch", "reject", "interrupt_then_dispatch"}`.
- `COMMAND_REGISTRY: list[CommandDef]` (line ~142) is the only place a command is declared.
- `resolve_command(name)` (line ~412) → `CommandDef | None` via `_COMMAND_LOOKUP`; accepts names with or without the leading slash and resolves aliases.
- Derived at import time: `COMMANDS`, `COMMANDS_BY_CATEGORY`, `SUBCOMMANDS` (all skip `gateway_only` entries), `GATEWAY_KNOWN_COMMANDS` (frozenset, includes config-gated commands so the runner can dispatch them), `ACTIVE_SESSION_BYPASS_COMMANDS`.
- Derived on demand, each filtered through `_resolve_config_gates()` + `_is_gateway_available()`: `gateway_help_lines()`, `telegram_bot_commands()`, `telegram_menu_commands()`, `discord_skill_commands()`, `discord_skill_commands_by_category()`, `slack_native_slashes()`, `slack_subcommand_map()`.
- `SlashCommandCompleter(Completer)` and `SlashCommandAutoSuggest(AutoSuggest)` — `prompt_toolkit` is imported under `try/except ImportError` so the gateway and test envs can import this module without it.

**`cli.py` surface:**

- `load_cli_config()` (line ~411) — prefers `{HERMES_HOME}/config.yaml`, falls back to the project `cli-config.yaml`; `HERMES_IGNORE_USER_CONFIG=1` skips the user file but still loads `.env`.
- `save_config_value(key_path, value)` (line ~4733) — **always** targets `HERMES_HOME/config.yaml`, resolving `HERMES_HOME` live rather than via the import-time `_hermes_home` constant, and deliberately never falls back to the shipped `cli-config.yaml` (that mismatch was the "wake-word toggle reverts on restart" bug).
- `class HermesCLI(CLIAgentSetupMixin, CLICommandsMixin, CLIBillingMixin)` (line ~4844).
- `process_command(command)` (line ~11317) — lowercases only for matching, resolves the alias through `resolve_command()`, fires the `pre_command` plugin hook, then dispatches on `canonical`. Returns `True` to continue, `False` to exit.
- `chat()` (line ~15390) and `run()` (line ~16666); the REPL's background `process_loop()` and `spinner_loop()` are nested functions inside the run path (~line 19098), draining `self._pending_input`.
- The input area wires `SlashCommandCompleter(...)` inside `ThreadedCompleter` (so the `rg`/`fd`-shelling completer never blocks the render loop) plus `SlashCommandAutoSuggest(AutoSuggestFromHistory(...))`.
- `show_help()` renders from `COMMANDS_BY_CATEGORY`.
- Busy-path inline dispatchers `_should_handle_{model,steer,background}_command_inline()` consult `resolve_command()` so `/steer` and `/background` bypass `_pending_input` while the agent is running.

**Mixin split** (god-file decomposition Phase 4 — methods lifted verbatim, resolved through the MRO; `cli.py`-internal helpers are imported *lazily inside* each handler to avoid a cycle):

| Mixin | File | LOC | Cluster |
|---|---|---|---|
| `CLIAgentSetupMixin` | [`cli_agent_setup_mixin.py`](../../../hermes_cli/cli_agent_setup_mixin.py) | 932 | credential resolution, per-turn agent config, first-use construction, resume recap |
| `CLICommandsMixin` | [`cli_commands_mixin.py`](../../../hermes_cli/cli_commands_mixin.py) | 3,805 | the `_handle_*_command` slash-command handlers |
| `CLIBillingMixin` | [`cli_billing_mixin.py`](../../../hermes_cli/cli_billing_mixin.py) | 1,566 | Nous billing / subscription handlers |

## Dependencies

**Three config loaders — pick the right one or a key will be invisible on one surface:**

| Loader | Location | Behaviour |
|---|---|---|
| `load_cli_config()` | [`../../cli.py`](../../../cli.py) | CLI mode. Merges CLI-specific inline defaults + user YAML; project `cli-config.yaml` fallback. |
| `load_config()` / `load_config_readonly()` | [`hermes_cli/config.py`](../../../hermes_cli/config.py) | `hermes tools`, `hermes setup`, most subcommands. `_load_config_impl()` deep-copies `DEFAULT_CONFIG`, `_deep_merge`s the user file, then overlays the managed scope. Cached on `(mtime_ns, size)`; the readonly variant skips the defensive deepcopy and **must not be mutated**. |
| `_load_gateway_config()` | [`gateway/run.py`](../../../gateway/run.py) (~line 3492) | Gateway runtime. Reads raw YAML via `read_raw_config()`/`yaml.safe_load()` for speed, then re-applies the managed overlay and replays the normalisation `load_config()` would have done. [`gateway/config.py`](../../../gateway/config.py) does its own `yaml.safe_load()` for platform/session policy. |

- `DEFAULT_CONFIG` and `OPTIONAL_ENV_VARS` live in the pure-data leaf [`hermes_cli/config_defaults.py`](../../../hermes_cli/config_defaults.py), which must not import `hermes_cli.config`. `REQUIRED_ENV_VARS` is `{}` in `config.py`.
- `hermes_constants.get_hermes_home()` / `display_hermes_home()` for every path; `hermes_cli/config.py::get_config_path()` for the config file.
- Cross-referenced subsystems (one line each): plugin discovery and `PluginContext`/`PluginManager`/`fire_pre_command_hook` → [`plugins.md`](./plugins.md); the FastAPI dashboard, `_SESSION_TOKEN` auth, `/api/pty`, and [`hermes_cli/web_routers/`](../../../hermes_cli/web_routers) → [`web-dashboard.md`](./web-dashboard.md); `hermes_cli/curator.py` verbs → [`skills-curator.md`](./skills-curator.md); `hermes_cli/kanban.py::build_parser` and the dispatcher → [`delegation-kanban.md`](./delegation-kanban.md).

## Internal Structure

```
hermes (launcher) → hermes_cli.main:main
   ├─ _startup_fast / _early_recovery   (stdlib-only, pre-import)
   ├─ _apply_profile_override()         (module scope, sets HERMES_HOME)
   ├─ _parser.build_top_level_parser()
   ├─ subcommands/<group>.py::build_*_parser(subparsers, cmd_*=...)   ← handlers injected
   └─ cmd_* handlers + inline verbs     (still in main.py)

cli.py: HermesCLI ─ CLIAgentSetupMixin + CLICommandsMixin + CLIBillingMixin
   └─ process_command() ── resolve_command() ── hermes_cli/commands.py::COMMAND_REGISTRY
```

`subcommands/__init__.py` documents the contract: builders never import `main` (cycle), shared argparse helpers live in `_shared.py`, and `main.py` re-exports them for back-compat.

## Notable Patterns / Gotchas

- **`.env` is for secrets only.** API keys, tokens, passwords. Every behavioural setting — timeouts, thresholds, feature flags, display prefs — belongs in `config.yaml`. Bridging a config key to an internal env var is allowed (`config.py` keeps a `"cwd" → "TERMINAL_CWD"` bridge table), but user-facing docs must point at `config.yaml`.
- **`MESSAGING_CWD` and a `.env` `TERMINAL_CWD` are deprecated.** `warn_deprecated_cwd_env_vars()` in [`hermes_cli/config.py`](../../../hermes_cli/config.py) (~line 2189) prints a migration hint to stderr when either is present and `terminal.cwd` is not explicitly set. The canonical setting is `terminal.cwd` in `config.yaml`; the gateway still reads `MESSAGING_CWD` as a fallback for placeholder resolution.
- **Adding a slash command = one registry entry + one handler.** Add a `CommandDef` to `COMMAND_REGISTRY`, then an `elif canonical == "...":` branch in `process_command()` (and a gateway handler in `gateway/run.py` if it is not `cli_only`). Adding an alias touches only the `aliases` tuple — dispatch, `/help`, the Telegram BotCommand menu, the Slack subcommand map, and autocomplete all follow.
- **`gateway_config_gate` promotes a `cli_only` command into the gateway** when the config dotpath is truthy. `GATEWAY_KNOWN_COMMANDS` always contains gated commands so the runner can dispatch them; help text and menus show them only when the gate is open.
- **All interactive menu pickers must use [`hermes_cli/curses_ui.py`](../../../hermes_cli/curses_ui.py)** — `curses_checklist()`, `curses_radiolist()`, `curses_single_select()` over the `RadioItem` type. [`hermes_cli/tools_config.py`](../../../hermes_cli/tools_config.py) is the exemplar (imports `curses_radiolist` and `curses_checklist`). Numbered-list fallbacks exist for non-tty hosts; do not hand-roll a menu.
- **Config version bumps:** `_deep_merge()` in `config.py` recurses into dict-valued keys, so adding a key to an existing section needs **no** `_config_version` bump — a missing key already resolves from `DEFAULT_CONFIG` at read time and new defaults are deliberately *not* materialised to disk. Bump `_config_version` (the key at the tail of `DEFAULT_CONFIG`) only when existing user config must be migrated or transformed. `check_config_version()` reads the **raw on-disk** file, because `load_config()` would mask a legacy file by merging the default version in.
- **Skins are pure data.** `_BUILTIN_SKINS` currently ships `default`, `ares`, `mono`, `slate`, `daylight`, `warm-lightmode`, `poseidon`, `sisyphus`, `charizard`. `load_skin()` checks user YAML in `~/.hermes/skins/<name>.yaml` **first**, then built-ins, then falls back to `default` with a warning; `list_skins()` skips a user skin that shadows a built-in name. Missing keys inherit from `default` via `_build_skin_config()`. `init_skin_from_config()` reads `display.skin`; `get_active_skin()` caches, `set_active_skin()` invalidates.
- **Import-order hazards in `main.py` are load-bearing.** Anything that reads `HERMES_HOME` must be imported *after* `_apply_profile_override()`; anything third-party must be imported *after* `_early_recovery.recover_if_needed()`. The fast-launch short-circuits run before the argparse tree is built, so a new global flag must also be understood by `_try_fast_chat_launch()`.
- **`SlashCommandCompleter` must stay off the UI thread** — it shells out to `rg`/`fd` with a ~2s timeout; it is always wrapped in `prompt_toolkit`'s `ThreadedCompleter`.
