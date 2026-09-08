# Module: `tools & toolsets`

This is the pipeline that turns a Python function into something a model can call. Remove it and nothing the agent does is reachable: no schema is sent to the provider, no tool call is dispatched, and no platform has a tool list.

## Responsibilities

- Auto-discover self-registering tool modules under `tools/` via an AST scan for top-level `registry.register(...)` calls, with on-disk memoization of the verdicts.
- Hold one process-wide `ToolRegistry` singleton mapping tool name → `ToolEntry` (schema, handler, toolset, `check_fn`, emoji, result budget).
- Gate availability per-tool through `check_fn` probes, TTL-cached with transient-failure suppression so a flaky Docker probe does not silently strip the terminal toolset mid-session.
- Resolve which tool names a session gets: `TOOLSETS` composition in [toolsets.py](../../../toolsets.py) → `resolve_toolset()` → `get_tool_definitions()` filtering.
- Emit OpenAI-format `{"type": "function", "function": {...}}` schemas, then post-process them (dynamic rebuilds, cross-reference stripping, sanitization, Tool Search deferral).
- Dispatch a model tool call: arg coercion → middleware → `pre_tool_call` plugin hooks → edit approval → `registry.dispatch()` → `post_tool_call` hooks.
- Normalize and bound every handler result and error body so no tool can bloat model context.
- Provide the terminal backend seam (`BaseEnvironment`) shared by local, Docker, SSH, Modal, Daytona, Singularity and Vercel Sandbox.

## Key Files

- [`tools/registry.py`](../../../tools/registry.py) — `ToolRegistry`, `ToolEntry`, `discover_builtin_tools()`, the `check_fn` TTL cache, `tool_error()` / `tool_result()`.
- [`model_tools.py`](../../../model_tools.py) — discovery entry point, `get_tool_definitions()`, `handle_function_call()`, `_last_resolved_tool_names`.
- [`toolsets.py`](../../../toolsets.py) — `_HERMES_CORE_TOOLS`, `TOOLSETS`, `resolve_toolset()`, `bundle_non_core_tools()`.
- [`toolset_distributions.py`](../../../toolset_distributions.py) — probabilistic toolset mixes consumed by [batch_runner.py](../../../batch_runner.py) for trajectory generation.
- [`tools/environments/base.py`](../../../tools/environments/base.py) — `BaseEnvironment` ABC, `execute()`, session snapshot, CWD tracking.
- [`tools/terminal_tool.py`](../../../tools/terminal_tool.py) — `_create_environment()` backend factory + `check_terminal_requirements()`.
- [`tools/mcp_tool.py`](../../../tools/mcp_tool.py) — registers external MCP tools under `mcp-<server>` toolsets.
- [`tools/tool_search.py`](../../../tools/tool_search.py) — progressive-disclosure bridge (`tool_search` / `tool_describe` / `tool_call`).
- [`tools/schema_sanitizer.py`](../../../tools/schema_sanitizer.py) — normalizes schemas for strict backends (llama.cpp GBNF).

## Public API

What other modules import:

```python
# tools/registry.py
registry.register(name, toolset, schema, handler, check_fn=None, requires_env=None,
                   is_async=False, description="", emoji="",
                   max_result_size_chars=None, dynamic_schema_overrides=None,
                   override=False, scope=None)
registry.dispatch(name, args, *, scope=None, **kwargs) -> str | dict
registry.get_definitions(tool_names: Set[str], quiet=False) -> List[dict]
registry.get_tool_names_for_toolset(toolset) -> List[str]
registry.deregister(name) / registry.get_schema(name) / registry.get_emoji(name)
discover_builtin_tools(tools_dir=None) -> List[str]
invalidate_check_fn_cache() / get_cached_check_fn_result(fn) / check_fn_cache_scope()
tool_error(message, **extra) -> str ; tool_result(data=None, **kwargs) -> str

# model_tools.py
get_tool_definitions(enabled_toolsets=None, disabled_toolsets=None,
                      quiet_mode=False, skip_tool_search_assembly=False) -> List[dict]
handle_function_call(function_name, function_args, task_id=None, tool_call_id=None,
                     session_id=None, turn_id=None, api_request_id=None,
                     user_task=None, enabled_tools=None, skip_pre_tool_call_hook=False,
                     enabled_toolsets=None, disabled_toolsets=None) -> str
coerce_tool_args(tool_name, args) -> Dict[str, Any]

# toolsets.py
get_toolset(name, *, include_registry=True)
resolve_toolset(name, visited=None, *, include_registry=True) -> List[str]
resolve_multiple_toolsets(names) / get_all_toolsets() / validate_toolset(name)
bundle_non_core_tools(toolset_name) -> Set[str]
```

## Tool Families

`tools/` holds ~123 modules; registrations cluster into these toolsets (see `TOOLSETS` in [toolsets.py](../../../toolsets.py)). One line each — the toolset name is the unit of enable/disable, not the file.

| Toolset | Notable modules | What it gives the model |
|---|---|---|
| `terminal` | [`terminal_tool.py`](../../../tools/terminal_tool.py) | `terminal` + `process`, backed by the `BaseEnvironment` seam |
| `file` | [`file_tools.py`](../../../tools/file_tools.py) | `read_file`, `write_file`, `patch`, `search_files` |
| `web` | [`web_tools.py`](../../../tools/web_tools.py), [`x_search_tool.py`](../../../tools/x_search_tool.py) | `web_search`, `web_extract` (`x_search` is a separate, default-off toolset) |
| `browser` | [`browser_tool.py`](../../../tools/browser_tool.py), [`browser_cdp_tool.py`](../../../tools/browser_cdp_tool.py) | navigate/snapshot/click family; `browser_exec` replaces them under the `browser-use` backend |
| `vision` / `image_gen` / `video` | [`vision_tools.py`](../../../tools/vision_tools.py), [`image_generation_tool.py`](../../../tools/image_generation_tool.py) | `vision_analyze`, `image_generate`, FLUX 3 video tools |
| `computer_use` | [`computer_use_tool.py`](../../../tools/computer_use_tool.py) | desktop control via `cua-driver`, gated by `check_computer_use_requirements` |
| `mcp-*` | [`mcp_tool.py`](../../../tools/mcp_tool.py) | one toolset per configured MCP server; `deregister`/re-register on `tools/list_changed` |
| `memory` / `session_search` | [`memory_tool.py`](../../../tools/memory_tool.py) | agent-curated memory; `session_search` queries the FTS5 session store |
| `todo` | [`todo_tool.py`](../../../tools/todo_tool.py) | plan tracking; intercepted by the agent loop, not the registry |
| `clarify` | [`clarify_tool.py`](../../../tools/clarify_tool.py) | multiple-choice / open-ended questions back to the user |
| `cronjob` | [`cronjob_tools.py`](../../../tools/cronjob_tools.py) | schedules jobs into `cron/` |
| `jupyter` / `code_execution` | [`jupyter_tool.py`](../../../tools/jupyter_tool.py), [`code_execution_tool.py`](../../../tools/code_execution_tool.py) | persistent kernel vs. RPC-scripted tool batches |
| `desktop_ui` / `project` | [`read_terminal_tool.py`](../../../tools/read_terminal_tool.py), [`project_tools.py`](../../../tools/project_tools.py) | GUI-only affordances; session-scoped gate, see Gotchas |
| `homeassistant` | [`homeassistant_tool.py`](../../../tools/homeassistant_tool.py) | smart-home control, gated on `HASS_TOKEN` |
| `delegation` / `kanban` | [`delegate_tool.py`](../../../tools/delegate_tool.py), [`kanban_tools.py`](../../../tools/kanban_tools.py) | see Cross-references |

## Internal Structure

**Registration.** Each `tools/*.py` file calls `registry.register(...)` at module level. [`tools/registry.py:discover_builtin_tools`](../../../tools/registry.py) walks `tools/*.py`, and for each file [`_module_registers_tools`](../../../tools/registry.py) parses the AST looking only at module-body statements for a `registry.register(...)` expression — so a helper that calls `register()` inside a function is not picked up. Verdicts are memoized on disk keyed by `(mtime_ns, size)` at `get_hermes_home()/cache/tool_discovery_cache.json`. `__init__.py`, `registry.py` and `mcp_tool.py` are excluded from the scan. `model_tools.py` calls `discover_builtin_tools()` at import time (line 230), then `discover_plugins()`.

**Toolset membership is the real gate.** Registration only populates the registry; a tool reaches the model only if its name is reachable through a toolset. [`_compute_tool_definitions`](../../../model_tools.py) builds the name set from `enabled_toolsets` via `resolve_toolset()`, or from every toolset when `enabled_toolsets is None`, then subtracts `disabled_toolsets`, then asks `registry.get_definitions()`. The rule itself is stated in the docstring of `get_tool_definitions` — the public entry point that calls `_compute_tool_definitions` ([`model_tools.py:332`](../../../model_tools.py)): *"All tools must be part of a toolset to be accessible."*

**Availability gating.** `registry.get_definitions()` runs each entry's `check_fn` through [`_check_fn_cached`](../../../tools/registry.py): 30 s TTL, exceptions swallowed to `False`, and a 60 s "last-good" grace window that serves the previous `True` when a fresh probe flakes. The cache key is `(fn, profile_scope)` where the scope comes from `check_fn_cache_scope()`; an unresolved multiplex profile identity returns `CHECK_FN_CACHE_BYPASS` and skips both cache layers rather than aliasing profiles. Real probes: `check_terminal_requirements` (probes the configured backend), `check_browser_requirements`, `_check_ha_available` (`HASS_TOKEN` set), `_check_kanban_mode`, `check_computer_use_requirements` (`cua-driver` installed).

**Schema post-processing.** After `registry.get_definitions()` returns, `_compute_tool_definitions` rebuilds `execute_code` against the actually-available sandbox tools, swaps in the dynamic discord schemas, strips the `browser_navigate` "prefer web_search" sentence when web tools are absent, drops `browser_exec` when the session has no `terminal`, runs `sanitize_tool_schemas()`, and finally applies the Tool Search deferral assembly.

**Dispatch path.** [`handle_function_call`](../../../model_tools.py) → `coerce_tool_args()` → Tool Search bridge unwrap → `apply_tool_request_middleware()` → `_AGENT_LOOP_TOOLS` guard → `_dispatch_pre_tool_call_hooks()` (block/approve/modify) → `maybe_require_edit_approval()` → `registry.dispatch()`. `dispatch()` bridges `is_async` handlers through `_run_async()`, normalizes the result via `_normalize_handler_result()` (a string, or the `{"_multimodal": True}` envelope), and converts exceptions to `tool_error(_sanitize_tool_error(...))`. `_emit_post_tool_call_hook()` fires on every exit path including blocked ones, and no-ops cheaply when no plugin registered `post_tool_call`.

**Profile scoping.** `ToolRegistry` keeps `self._tools` (process-global built-ins) plus `self._scoped_tools[scope]` overlays keyed by `hermes_home_key()`; `_merged_tools()` layers a profile's plugin tools on top. A plugin registering a name that shadows a global tool is rejected unless it passes `override=True` *and* has the `allow_tool_override` operator opt-in; `deregister()` is gated by the same policy so the gate cannot be bypassed by delete-then-register.

**Platform → tool list.** `_get_platform_tools()` in [hermes_cli/tools_config.py](../../../hermes_cli/tools_config.py) reads `platform_toolsets[<platform>]` from config, falling back to the platform's `default_toolset` (or `hermes-<platform>` for plugin platforms, which `resolve_toolset()` synthesizes as `_HERMES_CORE_TOOLS` plus registry entries in that toolset).

## Dependencies

- **Used by:** [run_agent.py](../../../run_agent.py), [cli.py](../../../cli.py), [batch_runner.py](../../../batch_runner.py), [agent/tool_executor.py](../../../agent/tool_executor.py), [acp_adapter/server.py](../../../acp_adapter/server.py), [hermes_cli/banner.py](../../../hermes_cli/banner.py), [hermes_cli/doctor.py](../../../hermes_cli/doctor.py), [hermes_cli/tools_config.py](../../../hermes_cli/tools_config.py), plus ~78 `tools/*.py` modules importing `tools.registry`. `toolsets` is additionally imported by [gateway/platforms/api_server.py](../../../gateway/platforms/api_server.py), [agent/subagent_lifecycle.py](../../../agent/subagent_lifecycle.py) and [tools/tool_search.py](../../../tools/tool_search.py).
- **Uses:** stdlib `ast`, `functools`, `importlib`, `threading`; `hermes_constants` (`hermes_home_key`, `get_hermes_home`); `hermes_cli.plugins` (`discover_plugins`, `_dispatch_pre_tool_call_hooks`); `hermes_cli.middleware`; `agent.secret_scope` (multiplex detection); `acp_adapter.edit_approval`; `utils.atomic_json_write`. Terminal backends shell out to `docker` / `ssh` / `singularity` and share [`file_sync.py`](../../../tools/environments/file_sync.py) for bidirectional workspace sync.

## Notable Patterns / Gotchas

- **The Footprint Ladder.** Every model tool ships on every API call, so a new *core* tool is the most expensive contribution in the repo. Prefer, in order: extend existing code → CLI command + skill → service-gated tool (`check_fn`) → plugin → MCP server in the catalog → new core tool. See `AGENTS.md` "The Footprint Ladder".
- **Auto-discovery ≠ exposure.** Dropping a `tools/foo.py` with a `registry.register()` call makes the registry aware of it and nothing else. It stays invisible until its name appears in a toolset in [toolsets.py](../../../toolsets.py) — a deliberate, manual step.
- **Surface capability is session-scoped, never env-keyed.** The desktop pane tools live in the `desktop_ui` toolset and the Project tools in `project`, both deliberately kept *out* of `_HERMES_CORE_TOOLS`. They are folded in by [`tui_gateway/server.py:_gui_surface_toolsets`](../../../tui_gateway/server.py), which keys on the session's `source` (`platform == "desktop"`), not on `HERMES_DESKTOP=1` — that env var exists only on locally-spawned backends and would silently strip the tools from URL/cloud gateways. `check_fn` answers reachability or user opt-in, never "which client is attached", because its result is TTL-cached process-wide while one gateway serves many sessions.
- **No cross-toolset references in static schemas.** `browser_navigate`'s description says "prefer web_search or web_extract", which makes models hallucinate those tools when the API key is missing — so the sentence is removed at `_compute_tool_definitions` time against `available_tool_names`, not baked into the static schema.
- **`_last_resolved_tool_names` is a process-global** in `model_tools.py`, rewritten on every `get_tool_definitions()` call including cache hits. `tools/delegate_tool.py` saves and restores it around subagent runs; anything reading it may be stale during a child agent.
- **Two cache layers, one generation counter.** `get_tool_definitions()` memoizes only when `quiet_mode=True` (the non-quiet path prints), capped at `_TOOL_DEFS_CACHE_MAX = 8`, keyed on `registry._generation` + config `(mtime_ns, size)` + profile scope + kanban/delegation flags. `resolve_toolset()` has its own memo keyed on the same generation. Mutating the registry invalidates both implicitly.
- **`dynamic_schema_overrides` is the escape hatch for config-dependent descriptions.** `ToolEntry` accepts a zero-arg callable merged shallowly on top of the base schema at `get_definitions()` time — used by `delegate_task` so the model is told the user's *current* `delegation.max_concurrent_children` / `max_spawn_depth` rather than the value frozen at import time. It runs on every definitions pass, so the `get_tool_definitions()` memo (keyed on config mtime) is what keeps it cheap.
- **Legacy `*_tools` names still resolve.** `_LEGACY_TOOLSET_MAP` in `model_tools.py` maps old suffixed names (`web_tools`, `terminal_tools`, `browser_tools`, …) to tool-name lists, and `_compute_tool_definitions` falls through to it when `validate_toolset()` fails — so stale config keeps working without becoming a real toolset.
- **Backends implement two methods, not one.** Subclasses of [`BaseEnvironment`](../../../tools/environments/base.py) supply `_run_bash()` (returning a `ProcessHandle`) and `cleanup()`; the base owns `execute()` — snapshot sourcing, CWD persistence via in-band markers, interrupt and timeout handling. `SSHEnvironment` adds ControlMaster socket reuse and a `FileSyncManager`; `DockerEnvironment` and `LocalEnvironment` set `_profile_scoped_passthrough = True` so profile secrets never persist into the session snapshot.

## Cross-references

- `tools/delegate_tool.py` (toolset `delegation`) and the 14 `toolset="kanban"` registrations in `tools/kanban_tools.py` — see [modules/delegation-kanban.md](delegation-kanban.md).
- `tools/skills_hub.py` and the `skills` toolset (`skills_list`, `skill_view`, `skill_manage`) — see [modules/skills-curator.md](skills-curator.md).
