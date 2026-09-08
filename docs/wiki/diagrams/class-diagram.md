# Class Diagram

The type-level map of Hermes: the engine classes, the tool and environment seams,
the gateway/persistence layer, and the ABCs that define every extension point. Read
it alongside [architecture.md](../architecture.md); member lists are abbreviated to
what a reader needs, and every class and member here was read from source.

Mermaid renders generics as `~T~`. Inheritance is `<|`, composition `*--`,
association `-->`.

## Core Types — Agent Engine

```mermaid
classDiagram
    class AIAgent {
        +str model
        +str provider
        +int max_iterations
        +List~str~ enabled_toolsets
        +str session_id
        +chat(message, stream_callback) str
        +run_conversation(user_message, system_message, conversation_history, task_id) dict
        +interrupt(message, hard_cancel) None
        +get_rate_limit_state() Any
        +get_activity_summary() dict
    }
    class IterationBudget {
        +int max_total
        +consume() bool
        +refund() None
        +used() int
        +remaining() int
    }
    class ProviderTransport {
        <<abstract>>
        +build_kwargs(params) dict
        +stream(request) Iterator
    }
    class ChatCompletionsTransport {
        +build_kwargs(params) dict
    }
    class ProviderProfile {
        <<dataclass>>
        +str name
        +str api_mode
        +str base_url
        +str auth_type
        +bool supports_vision
        +bool supports_prompt_cache_key
        +tuple fallback_models
    }
    class MemoryManager {
        +add_provider(provider) None
        +build_system_prompt() str
        +prefetch_all(query, session_id) str
        +sync_all(turn_messages) None
        +get_all_tool_schemas() List~dict~
    }

    AIAgent *-- IterationBudget : owns
    AIAgent --> ProviderTransport : calls model
    AIAgent --> MemoryManager : prompt + recall
    ProviderTransport <|-- ChatCompletionsTransport
    ProviderTransport ..> ProviderProfile : keyed by api_mode
```

`AIAgent` is the only class every surface constructs. `IterationBudget` is a
property-backed counter (`consume()` / `refund()`), which is how `execute_code`
turns give their iterations back. Note what the transport does *not* own: client
construction, credential refresh, prompt caching, interrupts, and retries all stay
on `AIAgent` — a new per-provider quirk belongs on the `ProviderProfile`, not in a
transport branch.

## Core Types — Tools & Environments

```mermaid
classDiagram
    class ToolRegistry {
        +register(name, toolset, schema, handler, check_fn, requires_env, is_async, description, emoji, dynamic_schema_overrides) None
        +deregister(name) None
        +get_definitions(tool_names, quiet) List~dict~
        +dispatch(name, args, scope) str
        +get_entry(name) ToolEntry
        +get_tool_names_for_toolset(toolset) List~str~
        +get_schema(name) dict
        +get_emoji(name, default) str
        +register_toolset_alias(alias, toolset) None
    }
    class ToolEntry {
        <<slots>>
        +str name
        +str toolset
        +dict schema
        +Callable handler
        +Callable check_fn
        +bool is_async
        +int max_result_size_chars
        +Callable dynamic_schema_overrides
    }
    class BaseEnvironment {
        <<abstract>>
        +execute(command, timeout) str
        +_run_bash(command) ProcessHandle
        +cleanup() None
    }
    class ProcessHandle {
        <<protocol>>
        +poll() int
        +kill() None
        +wait(timeout) int
        +stdout IO~str~
    }
    class LocalEnvironment
    class DockerEnvironment
    class SSHEnvironment
    class ModalEnvironment
    class DaytonaEnvironment
    class SingularityEnvironment
    class VercelSandboxEnvironment

    ToolRegistry o-- ToolEntry : owns per name
    ToolRegistry ..> ToolEntry : get_definitions filters by check_fn
    BaseEnvironment <|-- LocalEnvironment
    BaseEnvironment <|-- DockerEnvironment
    BaseEnvironment <|-- SSHEnvironment
    BaseEnvironment <|-- ModalEnvironment
    BaseEnvironment <|-- DaytonaEnvironment
    BaseEnvironment <|-- SingularityEnvironment
    BaseEnvironment <|-- VercelSandboxEnvironment
    BaseEnvironment ..> ProcessHandle : _run_bash returns
```

A subclass implements exactly two methods — `_run_bash()` returning a
`ProcessHandle`, and `cleanup()`; the base owns `execute()` with snapshot sourcing,
CWD persistence, interrupt, and timeout. `ToolEntry` uses `__slots__`, so an
attribute typo on a registration raises rather than silently creating a field.

## Core Types — Gateway & Persistence

```mermaid
classDiagram
    class GatewayRunner {
        +start() None
        +_dispatch_busy_slash_command(event) Any
        +_create_adapter(platform) BasePlatformAdapter
    }
    class GatewayAuthorizationMixin
    class GatewaySlashCommandsMixin
    class GatewayKanbanWatchersMixin
    class BasePlatformAdapter {
        <<abstract>>
        +_active_sessions Dict~str, Event~
        +_pending_messages Dict~str, MessageEvent~
        +connect(is_reconnect) bool
        +disconnect() None
        +send(...) SendResult
        +handle_message(event) None
    }
    class MessageEvent {
        <<dataclass>>
        +str text
        +str session_key
        +str platform
    }
    class SendResult {
        <<dataclass>>
    }
    class SessionDB {
        +create_session(session_id, source) str
        +get_session(session_id) dict
        +get_messages(session_id) List~dict~
    }
    class SessionSearchMixin
    class SessionSchemaMixin
    class SessionPortabilityMixin

    GatewayRunner --|> GatewayAuthorizationMixin
    GatewayRunner --|> GatewaySlashCommandsMixin
    GatewayRunner --|> GatewayKanbanWatchersMixin
    GatewayRunner o-- BasePlatformAdapter : creates per platform
    BasePlatformAdapter ..> MessageEvent : receives
    BasePlatformAdapter ..> SendResult : returns
    SessionDB --|> SessionSearchMixin
    SessionDB --|> SessionSchemaMixin
    SessionDB --|> SessionPortabilityMixin
    GatewayRunner --> SessionDB : persists turns
```

`BasePlatformAdapter` is an ABC whose abstract contract is `connect()`,
`disconnect()`, and `send()` — that trio is the whole obligation of a new platform.
`SessionDB` is assembled from three mixins living in separate top-level modules
(`hermes_state_search.py`, `hermes_state_schema.py`, `hermes_state_portability.py`),
so search and schema-repair code is not in the 13K-line main file.

## Extension Points (ABCs and registries)

```mermaid
classDiagram
    class MemoryProvider {
        <<abstract>>
        +name() str
        +is_available() bool
        +initialize(session_id) None
        +get_tool_schemas() List~dict~
        +prefetch(query, session_id) str
        +sync_turn(turn_messages) None
        +handle_tool_call(tool_name, args) str
        +on_pre_compress(messages) str
        +shutdown() None
    }
    class CronScheduler {
        <<abstract>>
        +name() str
        +is_available() bool
        +start() None
        +register_job(job) None
        +fire_due() Any
        +claim_fire(job_id, force) dict
        +reconcile() None
    }
    class InProcessCronScheduler
    class PluginContext {
        +register_tool(...) None
        +register_cli_command(...) None
        +register_memory_provider(provider) None
        +register_web_search_provider(provider) None
        +register_browser_provider(provider) None
        +register_image_gen_provider(provider) None
        +register_context_engine(engine) Registration
    }
    class PluginManager {
        +discover_and_load() None
    }
    class PluginManifest {
        <<dataclass>>
    }
    class HermesACPAgent {
        +initialize(...) Any
        +new_session(...) Any
        +prompt(...) Any
        +cancel(session_id) None
        +set_session_model(...) Any
    }
    class SkinConfig {
        <<dataclass>>
        +str name
        +str description
        +Dict colors
        +Dict spinner
        +Dict branding
    }

    MemoryProvider <|-- HonchoMemoryProvider
    MemoryProvider <|-- SupermemoryMemoryProvider
    CronScheduler <|-- InProcessCronScheduler
    PluginManager o-- PluginManifest : collects
    PluginManager --> PluginContext : hands to register()
    PluginContext ..> ToolRegistry : register_tool feeds
    HermesACPAgent --> AIAgent : drives

    class HonchoMemoryProvider
    class SupermemoryMemoryProvider
    class AIAgent
    class ToolRegistry
```

`MemoryProvider` splits its contract deliberately: four `@abstractmethod`s are the
bare identity/availability/tool surface, while the lifecycle hooks
(`prefetch`, `sync_turn`, `on_pre_compress`, `on_delegation`) arrive as overridable
no-ops so a provider implements only what it can actually do. `post_setup` is *not*
on the ABC at all — the setup wizard probes it with `hasattr`, which is why it works
without breaking older providers.

## Notes

What the diagrams cannot express:

- **Import-time registration is the real constructor.** `ToolRegistry` is populated
  by side effect of importing `tools/*.py`, gated by an AST scan for a top-level
  `registry.register()` call. A tool module that never calls it at module level is
  never imported. Likewise `ProviderProfile`s register on load of
  `plugins/model-providers/*`, and `discover_plugins()` runs as a side effect of
  importing `model_tools.py`.
- **`check_fn` results are TTL-cached process-wide** (~30 s, with a last-good grace
  window), keyed partly by profile scope. One process serves many sessions, which is
  exactly why a per-session answer must not be encoded in a `check_fn`.
- **Threading model is mixed.** `AIAgent`'s loop is synchronous; the gateway is
  `asyncio` with per-session `asyncio.Event` objects in `_active_sessions`; the ACP
  adapter is `asyncio` over stdio and must keep stdout reserved for JSON-RPC.
- **Singletons and process globals.** `ToolRegistry` is one instance per process;
  `model_tools._last_resolved_tool_names` is a module global that
  `tools/delegate_tool.py` saves and restores around a subagent run, so readers may
  observe a stale value during a child agent.
- **Profile scoping is layered, not copied.** `ToolRegistry` keeps process-global
  built-ins plus per-scope overlays keyed by `hermes_home_key()`, merged at read
  time; a plugin shadowing a built-in name is rejected unless it passes an explicit
  override policy.
- **`SkinConfig` is pure data.** Adding a theme needs no code — user YAML in
  `~/.hermes/skins/` is checked before built-ins, and missing keys inherit from
  `default`.
