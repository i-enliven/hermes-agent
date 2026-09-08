# Module: `plugins`

Hermes is extended through plugins, not by growing the core. Four **separate** discovery systems coexist — general plugins, memory providers, model providers, and per-capability ABC families — each with its own scanner, source set, and precedence rule. Confusing them is the classic onboarding trap.

## Responsibilities

- Discover, load, and tear down plugin code from user/project/pip/bundled sources without letting any one plugin break startup.
- Expose a single host-owned facade (`PluginContext`) through which plugins register tools, hooks, CLI subcommands, slash commands, and provider implementations.
- Keep the four discovery systems isolated so a dropped-in directory cannot silently shadow a shipped memory provider or a first-party model provider.

## Key Files

- [`hermes_cli/plugins.py`](../../../hermes_cli/plugins.py) — `PluginManager`, `PluginContext`, `discover_plugins()`, `VALID_HOOKS`, manifest parsing (v1 + v2), the inter-plugin event bus.
- [`hermes_cli/lifecycle.py`](../../../hermes_cli/lifecycle.py) — `invoke_hook()` / `has_hook()` dispatch: first-party observers first, then plugin hooks.
- [`agent/memory_provider.py`](../../../agent/memory_provider.py) — the `MemoryProvider` ABC.
- [`agent/memory_manager.py`](../../../agent/memory_manager.py) — `MemoryManager`, the memory orchestrator fan-out.
- [`plugins/memory/__init__.py`](../../../plugins/memory/__init__.py) — memory-provider discovery (bundled-first).
- [`providers/__init__.py`](../../../providers/__init__.py) — lazy model-provider discovery + `register_provider()`.
- [`providers/base.py`](../../../providers/base.py) — the `ProviderProfile` dataclass (`name`, `aliases`, …) that every model-provider plugin registers.
- [`plugins/plugin_storage.py`](../../../plugins/plugin_storage.py) — the per-plugin durable-state convention (the plugin install dir is *not* a scratch dir — `hermes plugins remove` deletes it).
- [`plugins/plugin_utils.py`](../../../plugins/plugin_utils.py) — shared concurrency helpers; targets the lazy process-wide singleton footgun.
- [`hermes_cli/plugins_cmd.py`](../../../hermes_cli/plugins_cmd.py) — the `hermes plugins` subcommand surface.

## Public API

- `discover_plugins(force=False)` — idempotent load entry; joins any in-flight background scan first. `start_background_plugin_discovery()` overlaps the ~150ms scan with CLI startup.
- `PluginContext.register_tool(name, toolset, schema, handler, check_fn=..., override=False)` — delegates to `tools.registry.register()`; `override=True` against a built-in additionally requires `plugins.entries.<id>.allow_tool_override: true`.
- `PluginContext.register_hook(name, callback)` — name must be in `VALID_HOOKS` (`hermes_cli/plugins.py`).
- `PluginContext.register_cli_command(name, help, setup_fn, handler_fn=...)` — wires `hermes <plugin> ...` into the argparse tree; `register_command()` does the same for slash commands.
- Other `PluginContext` registrars: `register_memory_provider`, `register_context_engine`, `register_image_gen_provider`, `register_web_search_provider`, `register_browser_provider`, `register_video_gen_provider`, `register_dashboard_auth_provider`, `register_tts_provider`, `register_transcription_provider`, `register_secret_source`, `register_platform`, `register_middleware`, `register_skill`, `register_auxiliary_task`, `register_approval_transport`, `register_slack_action_handler`, `register_system_prompt_section`, `register_redaction_patterns`.
- `invoke_hook(name, **kwargs)` / `has_hook(name)` — every fire site gates on `has_hook()` so an unsubscribed hot path pays one dict probe.
- Non-registration `ctx` surface: `llm` (host-owned `agent.plugin_llm.PluginLlm` facade — no plugin brings its own provider keys), `state` (`PluginState`, quota-bounded JSON at `<hermes home>/plugin-data/<namespace>/state.json`), `platform_actions`, `profile_name`, `get_config()` / `set_config()` (plugin-relative keys only — reserved roots `model`, `plugins`, `security`, `settings` are rejected), `call_mcp()`, `has_plugin()`, `has_capability()`, `on_unload()`, `spawn_task()`, `emit()` / `subscribe()`.

## Internal Structure

The four systems at a glance — same four source *kinds*, different entry points, different precedence, different activation:

| System | Entry point | Precedence on name collision | Activation |
|---|---|---|---|
| General plugins | `PluginManager.discover_and_load()` | **later source wins** (bundled → user → project → entry point) | name in `plugins.enabled` (opt-in); `backend` / `platform` bundled auto-load |
| Memory providers | `plugins/memory/__init__.py::load_memory_provider()` | **bundled wins** (bundled → user → project → entry point) | `memory.provider` in `config.yaml`; exactly one |
| Model providers | `providers/__init__.py::_discover_providers()` | **last writer wins**, entry points scanned first so first-party always beats pip | selected by model/provider name at request time |
| ABC families | per-family `register_*` on `ctx` + a module-level registry | registry-scoped (`scope=` per hermes home) | config key per family (e.g. the active image-gen / web / browser provider) |

### 1. General plugins — `hermes_cli/plugins.py`

`PluginManager.discover_and_load()` collects manifests from four sources in this order, and **later sources win** on key collision (user overrides bundled, project overrides user):

1. Bundled `<repo>/plugins/<name>/` — scanned with `skip_names={"memory", "context_engine", "platforms", "model-providers"}` because those have their own systems; `plugins/platforms/` is scanned one level below instead.
2. User `~/.hermes/plugins/` (profile-scoped via `get_hermes_home()`).
3. Project `./.hermes/plugins/` — only when `HERMES_ENABLE_PROJECT_PLUGINS` is truthy.
4. Pip entry points in the `hermes_agent.plugins` group.

A directory plugin needs `plugin.yaml` **and** an `__init__.py` exposing `register(ctx)` (`getattr(module, "register", None)` at load). A real manifest, [`plugins/memory/honcho/plugin.yaml`](../../../plugins/memory/honcho/plugin.yaml): `name`, `version`, `description`, `pip_dependencies`, `hooks`. [`plugins/platforms/irc/plugin.yaml`](../../../plugins/platforms/irc/plugin.yaml) adds `kind: platform`, `label`, and `requires_env` / `optional_env` entries that the config UI renders. Manifest v2 (#64165) adds `manifest_version`, `api_version`, `requires_plugins` (topological load order via `resolve_plugin_load_order()`), `config_schema`, `python_dependencies` (validated, never auto-installed); unknown fields warn and load anyway.

`kind` (from `_VALID_PLUGIN_KINDS`) decides which system owns the plugin:

- `standalone` (default) — has its own hooks/tools; loads only when named in `plugins.enabled`.
- `backend` — pluggable backend for an existing core tool (e.g. `plugins/image_gen/fal/plugin.yaml` declares `kind: backend` plus `requires_env: [FAL_KEY]`). Bundled backends auto-load; user-installed ones stay gated.
- `exclusive` — a category with exactly one active provider (memory). The category's own discovery loads it; the general scanner skips it.
- `platform` — a gateway messaging adapter (`plugins/platforms/irc/plugin.yaml`). Bundled ones auto-load so every shipped platform works out of the box; user-installed ones stay gated as untrusted code.
- `model-provider` — routed to `providers/__init__.py`; recorded, never imported, by the general manager.

Hooks are the strings in `VALID_HOOKS` — including `pre_tool_call`, `post_tool_call`, `pre_llm_call`, `post_llm_call`, `on_session_start`, `on_session_end`. Their real fire sites:

| Hook | Fires from |
|---|---|
| `pre_tool_call` | `model_tools.py` via `_dispatch_pre_tool_call_hooks()` (single-fire contract) |
| `post_tool_call` | `model_tools.py`, behind a `has_hook("post_tool_call")` gate |
| `pre_llm_call` | [`agent/turn_context.py`](../../../agent/turn_context.py) |
| `post_llm_call`, `on_session_end` | [`agent/turn_finalizer.py`](../../../agent/turn_finalizer.py) |
| `on_session_start` | [`agent/conversation_loop.py`](../../../agent/conversation_loop.py) |

`VALID_HOOKS` holds far more than six (kanban observers, approval lifecycle, stream observers, gateway boundary events); the table lists the six core-loop hooks only.

### 2. Memory providers — bundled-first, the reverse precedence

[`agent/memory_provider.py`](../../../agent/memory_provider.py) defines `MemoryProvider(ABC)`. Abstract: `name`, `is_available()`, `initialize()`, `get_tool_schemas()`. Overridable no-ops include `prefetch(query, *, session_id="")`, `queue_prefetch()`, `sync_turn()`, `shutdown()`, `handle_tool_call()`, `on_turn_start()`, `on_session_end()`, `on_session_switch()`, `on_pre_compress()`, `on_delegation()`, `get_config_schema()`, `save_config()`, `on_memory_write()`, `backup_paths()`. `post_setup(hermes_home, config)` is **not** on the ABC — the setup wizard probes `hasattr(provider, "post_setup")` in [`hermes_cli/memory_setup.py`](../../../hermes_cli/memory_setup.py), and providers such as `plugins/memory/honcho/__init__.py` implement it.

[`plugins/memory/__init__.py`](../../../plugins/memory/__init__.py) scans the same four source kinds (bundled `plugins/memory/<name>/`, `$HERMES_HOME/plugins/<name>/`, `./.hermes/plugins/<name>/` gated on `HERMES_ENABLE_PROJECT_PLUGINS`, `hermes_agent.memory_providers` entry points) but **bundled wins, then user, then project, then entry point** — first-seen-wins via a `seen` set. The module states the reason outright: a provider is activated *by name*, so a directory dropped into the working tree that could shadow a shipped one would silently redirect the agent's memory. "Changing this order is a breaking change, not a cleanup." Exactly one provider is active, selected by `memory.provider` in `config.yaml`.

[`agent/memory_manager.py`](../../../agent/memory_manager.py) is the orchestrator: `add_provider()`, `prefetch_all()`, `sync_all()`, `build_system_prompt()`, `get_all_tool_schemas()`, `handle_tool_call()`, `on_turn_start()`, `on_session_end()`, `on_pre_compress()`, with background sync through a `ThreadPoolExecutor`.

In-tree inventory (`plugins/memory/`): `byterover`, `hindsight`, `holographic`, `honcho`, `mem0`, `openviking`, `retaindb`, `supermemory`.

### 3. Model providers — lazy, last-writer-wins

[`providers/__init__.py`](../../../providers/__init__.py) owns a **lazy, separate** discovery system: nothing runs until the first `get_provider_profile()` or `list_providers()` call triggers `_discover_providers()`. It is *not* driven by the general `PluginManager`. Import order (later wins, because `register_provider(profile)` is last-writer-wins on `_REGISTRY`):

0. `hermes_agent.plugins` entry points — deliberately **first / lowest precedence**, so a pip package cannot hijack a first-party name such as `openrouter`. Targets are invoked only when zero-arg (`_requires_arguments()`); a `register(ctx)` target belongs to the `PluginManager`, not here.
1. Bundled `plugins/model-providers/<name>/` (36 dirs: `openrouter`, `anthropic`, `gemini`, `nvidia`, …).
2. User `$HERMES_HOME/plugins/model-providers/<name>/` — overrides any bundled profile of the same name.
3. Legacy single-file `providers/<name>.py` (except `base.py`) via `pkgutil.iter_modules`.

The general manager records `kind: model-provider` manifests for introspection but does **not** import them (that would double-instantiate `ProviderProfile`). When `kind:` is absent, `_detect_kind_from_source()` coerces by an import-free scan of the first 8192 chars: `register_memory_provider` or `MemoryProvider` → `exclusive`; `register_provider` **and** `ProviderProfile` → `model-provider`.

See [providers-transports.md](providers-transports.md) for the transport layer behind these profiles.

### 4. Other ABC + orchestrator families

Each is a category directory under `plugins/` with its own ABC and registry; none is loaded by the general scanner's top-level sweep.

| Family | ABC | Orchestrated by |
|---|---|---|
| `plugins/context_engine/` | `ContextEngine` — [`agent/context_engine.py`](../../../agent/context_engine.py) | `plugins/context_engine/__init__.py::load_context_engine()`, called from [`agent/agent_init.py`](../../../agent/agent_init.py) |
| `plugins/image_gen/` (`fal`, `openai`, `krea`, …) | `ImageGenProvider` — [`agent/image_gen_provider.py`](../../../agent/image_gen_provider.py) | [`agent/image_gen_registry.py`](../../../agent/image_gen_registry.py), fed by `ctx.register_image_gen_provider()` |
| `plugins/web/` (`tavily`, `exa`, `brave_free`, …) | `WebSearchProvider` — [`agent/web_search_provider.py`](../../../agent/web_search_provider.py) | [`agent/web_search_registry.py`](../../../agent/web_search_registry.py) |
| `plugins/browser/` (`browser_use`, `browserbase`, …) | `BrowserProvider` — [`agent/browser_provider.py`](../../../agent/browser_provider.py) | [`agent/browser_registry.py`](../../../agent/browser_registry.py) |
| `plugins/video_gen/` (`fal`, `xai`, …) | `VideoGenProvider` — [`agent/video_gen_provider.py`](../../../agent/video_gen_provider.py) | [`agent/video_gen_registry.py`](../../../agent/video_gen_registry.py) |
| `plugins/dashboard_auth/` (`basic`, `nous`, …) | `DashboardAuthProvider` — [`hermes_cli/dashboard_auth/base.py`](../../../hermes_cli/dashboard_auth/base.py) | [`hermes_cli/dashboard_auth/registry.py`](../../../hermes_cli/dashboard_auth/registry.py) |
| `plugins/cron_providers/` (`chronos`) | `CronScheduler` — [`cron/scheduler_provider.py`](../../../cron/scheduler_provider.py) | `plugins/cron_providers/__init__.py::load_cron_scheduler()` (bundled takes precedence) |
| `plugins/platforms/` (`irc`, `discord`, `feishu`, …) | gateway adapter classes | `ctx.register_platform()` + [`gateway/platform_registry.py`](../../../gateway/platform_registry.py) |
| `plugins/observability/` (`langfuse`, `nemo_relay`) | none — pure hook subscribers | `ctx.register_hook()` through the general manager |
| `plugins/kanban/` (`dashboard`, `systemd`) | none — dashboard assets + a systemd unit | dispatcher in the gateway |

See [delegation-kanban.md](delegation-kanban.md) for the board and dispatcher behind `plugins/kanban/`.

## Dependencies

- **Used by:** `model_tools.py` (tool dispatch + hook firing), `run_agent.py`, `agent/turn_context.py`, `agent/turn_finalizer.py`, `agent/conversation_loop.py`, `hermes_cli/main.py` (argparse wiring), `hermes_cli/memory_setup.py`, `agent/agent_init.py`.
- **Uses:** `tools/registry.py` (tool registration), `hermes_constants.get_hermes_home()` (profile scoping), `hermes_cli/config.py` (`plugins.enabled` / `plugins.disabled` / `plugins.entries.*`), `hermes_cli/plugin_capabilities.py` (capability consent), `registration_lifecycle.replacement_coordinator` (reload-safe registration ledger).

## Notable Patterns / Gotchas

- **Discovery timing pitfall (verified).** `discover_plugins()` runs as a side effect of importing `model_tools.py` (wrapped in `try/except` that only logs at debug). Any code path that reads plugin state without importing `model_tools.py` first must call `discover_plugins()` explicitly — it is idempotent, and it joins an in-flight background scan rather than racing it.
- **`_discovered` is set before the sweep** as a re-entrancy guard (a plugin's `register()` can transitively re-trigger discovery) and is **reset on exception**, so a failed scan is never cached as "discovered with an empty registry".
- **Opt-in by default.** `_get_enabled_plugins()` returns `None` when `plugins.enabled` is missing, which callers treat as "nothing enabled"; `HERMES_SAFE_MODE=1` skips discovery entirely.
- **Plugins MUST NOT modify core files** (`run_agent.py`, `cli.py`, `gateway/run.py`, `hermes_cli/main.py`). If a plugin needs a capability the framework lacks, widen the generic plugin surface (new hook, new `ctx` method) — never special-case it in core. PR #5295 removed 95 lines of hardcoded honcho argparse from `main.py` for exactly this reason.
- **No new in-tree memory providers; no new third-party-product plugins.** Both policies (May/June 2026) require shipping as a standalone plugin repo installed into `~/.hermes/plugins/` or via a pip entry point. The directories already in the tree — `plugins/memory/*`, `plugins/observability/`, `plugins/kanban/` — are existing precedent, not an invitation to add more.
- **Native plugin compatibility is additive**, not version-gated: no `PLUGIN_API_VERSION`, no manifest-wide `api:` match. `PluginContext` methods are never removed or renamed; new hook data arrives as keyword fields and callbacks are signature-inspected (a legacy callback gets only the fields it declares; a `**kwargs` callback gets the full payload); new provider methods ship with default implementations; deprecations need a once-per-process warning plus at least two subsequent minor releases before removal. Canonical text: [`website/docs/developer-guide/plugins/index.md`](../../../website/docs/developer-guide/plugins/index.md).
- **Trust gates are per-capability, not ambient.** Overriding a built-in tool needs `plugins.entries.<id>.allow_tool_override: true`; MCP access needs `plugins.entries.<id>.mcp_allowlist`; both fail closed.
- **The inter-plugin event bus is bounded, not best-effort-unbounded.** `ctx.emit()` queues rather than recursing: `_EVENT_EMIT_DEPTH_CAP = 8` stops mutually-emitting plugins, `_EVENT_PENDING_CAP = 64` caps queued + running events, and a full budget drops the new event with a warning so a blocked subscriber cannot back-pressure the emitter. The `hermes:` namespace is reserved to core.
- **Plugin-authored system-prompt bytes are capped.** `register_system_prompt_section()` accepts only the `after_memory` position, 4000 chars per section, 32 sections, 8000 chars total — because those bytes are charged on every turn and would break the per-conversation prompt cache if they could grow unbounded.
- **Debugging a plugin that "isn't showing up":** `HERMES_PLUGINS_DEBUG=1` tees discovery to stderr at DEBUG (which dirs were scanned, which manifests parsed, what each `register(ctx)` did, full tracebacks). `HERMES_SAFE_MODE=1` and `HERMES_BUNDLED_PLUGINS` (packaged/Nix installs) both change what the sweep sees.
- **Registration is ledger-owned.** Every `ctx.register_*()` returns a `PluginRegistration` whose release closure is recorded per `(hermes_home, plugin_id)`, so a force-reload unwinds global registries in reverse order and one profile's unload cannot clear another profile's registrations.
