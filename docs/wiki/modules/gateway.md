# Module: `gateway`

The always-on messaging runtime (~108K LOC across `gateway/` + `gateway/relay/`). If it disappeared, every platform bot (Telegram, Discord, Slack, Signal, WhatsApp, …) stops receiving and answering, cron deliveries lose their router, and per-conversation session/transcript continuity is gone.

## Responsibilities
- Boot and supervise all connected platform adapters (`GatewayRunner.start()`), including per-profile adapter maps under `gateway.multiplex_profiles`.
- Normalize inbound platform updates into `MessageEvent` + `SessionSource` and route them through authorization → slash-command dispatch → agent run.
- Key conversations to durable sessions via `build_session_key()` / `SessionStore` (SQLite `SessionDB`, JSONL fallback).
- Bind per-turn session identity into `ContextVar`s (`gateway/session_context.py`) instead of `os.environ`.
- Deliver outbound content: `DeliveryRouter` (cron/`send_message`), streaming edits (`stream_consumer.py`), streaming TTS audio.
- Enforce authorization gates (`authz_mixin.py`), slash-access control (`slash_access.py`), DM pairing (`pairing.py`).
- Provide the adapter contract (`BasePlatformAdapter`) + plugin platform registry (`platform_registry.py`).
- Fire lifecycle hooks (`gateway/hooks.py`) and survive ops events: planned restart, drain, scale-to-zero, wedged loop.

## Key Files
- [`gateway/run.py`](../../../gateway/run.py) — `GatewayRunner`, `start_gateway()`, `main()`, the message pipeline and busy-command resolver (30.7K lines).
- [`gateway/session.py`](../../../gateway/session.py) — `SessionSource`, `SessionStore`, `build_session_key()`, context-prompt builder.
- [`gateway/session_state.py`](../../../gateway/session_state.py) — `SessionState`/`TurnState`/`ConversationState` + `legacy_dict_property` views.
- [`gateway/session_context.py`](../../../gateway/session_context.py) — `set_session_vars()` / `get_session_env()` / `reset_session_vars()` (ContextVar task-local state).
- [`gateway/session_stall.py`](../../../gateway/session_stall.py) — idle/stall notification predicates.
- [`gateway/delivery.py`](../../../gateway/delivery.py) — `DeliveryTarget.parse()`, `DeliveryRouter.deliver()`, dead-target skipping.
- [`gateway/delivery_ledger.py`](../../../gateway/delivery_ledger.py) — durable obligation rows in `state.db` around a final response (`record_obligation`/`mark_delivered`/`sweep_recoverable`).
- [`gateway/mirror.py`](../../../gateway/mirror.py) — `mirror_to_session()` cross-platform transcript append.
- [`gateway/media_policy.py`](../../../gateway/media_policy.py) — `apply_media_policy_env()` config→env bridge for media path validation.
- [`gateway/platforms/base.py`](../../../gateway/platforms/base.py) — `BasePlatformAdapter`, `MessageEvent`, `SendResult`, `merge_pending_message_event()`.
- [`gateway/platform_registry.py`](../../../gateway/platform_registry.py) — `PlatformEntry`, `PlatformRegistry`, deferred loaders, `platform_registry` singleton.
- [`gateway/hooks.py`](../../../gateway/hooks.py) — `HookRegistry.discover_and_load()` / `emit()` / `emit_collect()`.
- [`gateway/authz_mixin.py`](../../../gateway/authz_mixin.py) — `_auth_env()`, `_platform_gate_env()`, `_is_user_authorized()`, `_get_unauthorized_dm_behavior()`.
- [`gateway/profile_routing.py`](../../../gateway/profile_routing.py) — `ProfileRoute`, `parse_profile_routes()`, `match_profile_route()`.
- [`gateway/status.py`](../../../gateway/status.py) — `acquire_scoped_lock()` / `release_scoped_lock()`, PID + runtime-status files.
- [`gateway/stream_events.py`](../../../gateway/stream_events.py) — `MessageChunk`, `MessageStop`, `Commentary`, `ToolCallChunk`, `LongToolHint`, `GatewayNotice`.
- [`gateway/stream_dispatch.py`](../../../gateway/stream_dispatch.py) — `GatewayEventDispatcher` (adapter → sink routing).
- [`gateway/stream_consumer.py`](../../../gateway/stream_consumer.py) — `GatewayStreamConsumer.run()` (streaming edit loop).
- [`gateway/streaming_tts_consumer.py`](../../../gateway/streaming_tts_consumer.py) — `StreamingTTSConsumer` (LLM deltas → PCM).
- [`gateway/turn_lease.py`](../../../gateway/turn_lease.py) — `SessionTurnLeaseRegistry`, serializes load-history→run→flush per resolved `session_id`.
- [`gateway/relay/`](../../../gateway/relay/) — EXPERIMENTAL "Gateway Gateway" relay: `RelayAdapter`, `CapabilityDescriptor`, `WebSocketRelayTransport`, HMAC token/signature helpers.
- [`gateway/restart.py`](../../../gateway/restart.py) — `GATEWAY_SERVICE_RESTART_EXIT_CODE = 75`, `GATEWAY_FATAL_CONFIG_EXIT_CODE = 78`, drain-timeout parsers.
- [`gateway/restart_loop_guard.py`](../../../gateway/restart_loop_guard.py) — `check_and_record()` / `is_restart_loop_tripped()` boot-chain breaker.
- [`gateway/shutdown_watchdog.py`](../../../gateway/shutdown_watchdog.py) — `start_loop_liveness_watchdog()` out-of-loop backstop for a frozen asyncio loop.
- [`gateway/shutdown_flush.py`](../../../gateway/shutdown_flush.py) — `flush_pending_to_file()`, transcript spool/drain/recover.
- [`gateway/scale_to_zero.py`](../../../gateway/scale_to_zero.py) — `should_arm()` / `is_idle()` / `suspend_self()` idle quiesce.
- [`gateway/drain_control.py`](../../../gateway/drain_control.py) — `write_drain_request()` / `drain_requested()` file-marker contract (no HTTP control channel).
- [`gateway/systemd_notify.py`](../../../gateway/systemd_notify.py) — `SystemdWatchdog`, `notify()`, `ready()`.
- [`gateway/cgroup_cleanup.py`](../../../gateway/cgroup_cleanup.py) — `ExecStopPost=` orphan reaper.
- [`gateway/memory_monitor.py`](../../../gateway/memory_monitor.py) — `start_memory_monitoring()` RSS trend logging.

## Public API
```python
# gateway/run.py
class GatewayRunner(GatewayAuthorizationMixin, GatewayKanbanWatchersMixin, GatewaySlashCommandsMixin):
    def __init__(self, config: Optional[GatewayConfig] = None): ...
    async def start(self) -> bool: ...
    async def _handle_message(self, event: MessageEvent) -> Optional[str]: ...
async def start_gateway(config: Optional[GatewayConfig] = None, replace: bool = False,
                         verbosity: Optional[int] = 0) -> bool: ...
def main(): ...

# gateway/session.py
def build_session_key(source: SessionSource, group_sessions_per_user: bool = True,
                      thread_sessions_per_user: bool = False,
                      profile: Optional[str] = None) -> str: ...
class SessionStore:
    def __init__(self, sessions_dir: Path, config: GatewayConfig,
                  has_active_processes_fn=None): ...

# gateway/delivery.py
@dataclass class DeliveryTarget:
    @classmethod def parse(cls, target: str, origin: Optional[SessionSource] = None) -> "DeliveryTarget": ...
class DeliveryRouter:
    async def deliver(self, content: str, targets: List[DeliveryTarget], job_id=None,
                      job_name=None, metadata=None) -> Dict[str, Any]: ...

# gateway/mirror.py
def mirror_to_session(platform: str, chat_id: str, message_text: str, source_label: str = "cli",
                      thread_id=None, user_id=None, role: str = "assistant") -> bool: ...

# gateway/status.py
def acquire_scoped_lock(scope: str, identity: str,
                        metadata: Optional[dict] = None) -> tuple[bool, Optional[dict]]: ...
def release_scoped_lock(scope: str, identity: str) -> None: ...

# gateway/platforms/base.py
class BasePlatformAdapter(ABC):
    @abstractmethod async def connect(self, *, is_reconnect: bool = False) -> bool: ...
    @abstractmethod async def disconnect(self) -> None: ...
    @abstractmethod async def send(self, chat_id: str, content: str, reply_to=None,
                                   metadata=None) -> SendResult: ...
    async def handle_message(self, event: MessageEvent) -> None: ...
    def set_message_handler(self, handler: MessageHandler) -> None: ...
```

## Internal Structure
**Process model.** One asyncio process. `main()` → `asyncio.run(start_gateway(config))` → `GatewayRunner.start()` connects adapters; each adapter registers `runner._primary_message_handler()` via `set_message_handler()`. Under multiplexing the primary handler is profile-scoped (`_make_profile_message_handler`) and secondary-profile adapters live in `_profile_adapters`, not `self.adapters`.

**Session keying.** `build_session_key()` is the single source of truth: `agent:<ns>:<platform>:<chat_type>:…`, where `<ns>` is `main` for the default profile (byte-identical to legacy keys) and the profile name otherwise. DM keys carry `chat_id`/`thread_id`; group keys add per-user isolation when `group_sessions_per_user`, and threads stay *shared* unless `thread_sessions_per_user`. Slack prefixes `scope_id` (workspace). `handle_message()` drops an internally-routed event whose derived key ≠ `metadata["gateway_session_key"]`.

**Adapter lifecycle.** `connect(is_reconnect=…)` → inbound listener builds `MessageEvent` via `build_source()` → `handle_message()` → `_start_session_processing()` installs `_active_sessions[key]` (interrupt `Event`) *and* the `_session_tasks` owner mapping synchronously, then spawns `_process_message_background()`. On-entry `_heal_stale_session_lock()` clears a guard whose owner task already exited. `disconnect()` releases the guard and pops `_pending_messages`.

**The two message guards.** (1) Adapter level — `BasePlatformAdapter.handle_message()` checks `session_key in self._active_sessions`; if busy, plain text is merged into `self._pending_messages[session_key]` via `merge_pending_message_event()` (or the busy-text debounce buffer) and drained as a follow-up turn by `_process_message_background()`. (2) Runner level — `GatewayRunner._handle_message()` resolves the alias through `resolve_command()`, then dispatches on `canonical` (`/new`, `/status`, `/context`, `/help`, …); when a turn is already running it routes through `_dispatch_busy_slash_command()`, which reads the `CommandDef`'s declared `busy_policy`/`busy_handler` (`dispatch` / `interrupt_then_dispatch` / `reject`) instead of a per-command if-chain. A command that must reach the runner while the agent is blocked on `Event.wait` (`/approve`, `/deny`) or must cancel the run (`/stop`, `/new`, `/reset`) must bypass **both** guards: `should_bypass_active_session()` / `is_interrupt_then_dispatch()` gate it in the adapter, which calls `self._message_handler(event)` **inline** and sends the reply itself. `_process_message_background()` is forbidden for this — it owns session lifecycle, so its cleanup races the still-running task.

**Delivery path.** Final response → `delivery_ledger.record_obligation()` → adapter `send()`/`edit_message()` → `mark_delivered()`/`mark_failed()`; `sweep_recoverable()` replays un-ACKed rows after a crash. Streaming goes `stream_events` → `GatewayEventDispatcher` → `GatewayStreamConsumer` (edit loop; honors `REQUIRES_EDIT_FINALIZE`) or `StreamingTTSConsumer`. Out-of-band sends (`DeliveryRouter.deliver`, `send_message`) optionally mirror via `mirror_to_session()`.

**Hooks.** `HookRegistry` discovers `~/.hermes/hooks/*` (`HOOK.yaml` + `handler.py`) and fires `gateway:startup`, `session:start|end|reset`, `agent:start|step|end`, `command:*`. `emit()` discards returns; `emit_collect()` honors `{"decision": "deny"|"handled"|"rewrite"}` for `command:<name>` policies. `gateway/builtin_hooks/` ships **only** an `__init__.py` docstring — no built-in hook exists; `HookRegistry._register_builtin_hooks()` is an empty reserved extension point.

## Platform Inventory
Built-in (`gateway/platforms/`) — transport libs read from each adapter's imports:

| Adapter | Transport (as imported) | File |
|---|---|---|
| `SignalAdapter` | `httpx` (signal-cli/REST) | [`signal.py`](../../../gateway/platforms/signal.py) |
| `WebhookAdapter` | `aiohttp` | [`webhook.py`](../../../gateway/platforms/webhook.py) |
| `APIServerAdapter` | `aiohttp` | [`api_server.py`](../../../gateway/platforms/api_server.py) |
| `MSGraphWebhookAdapter` | `aiohttp` | [`msgraph_webhook.py`](../../../gateway/platforms/msgraph_webhook.py) |
| `WeixinAdapter` | `aiohttp`, `cryptography` | [`weixin.py`](../../../gateway/platforms/weixin.py) |
| `WhatsAppCloudAdapter` | `aiohttp`, `httpx` (+ `WhatsAppBehaviorMixin`) | [`whatsapp_cloud.py`](../../../gateway/platforms/whatsapp_cloud.py) |
| `BlueBubblesAdapter` | `httpx` | [`bluebubbles.py`](../../../gateway/platforms/bluebubbles.py) |
| `QQAdapter` | `aiohttp`, `httpx` | [`qqbot/adapter.py`](../../../gateway/platforms/qqbot/adapter.py) |
| `YuanbaoAdapter` | `httpx` + protobuf/ws helpers | [`yuanbao.py`](../../../gateway/platforms/yuanbao.py) |

Plugin (`plugins/platforms/<name>/adapter.py`): `TelegramAdapter` (`telegram`, `httpx`), `DiscordAdapter` (`discord`, `aiohttp`), `SlackAdapter` (`aiohttp`), `MatrixAdapter` (`aiohttp`), `MattermostAdapter` (`aiohttp`), `IRCAdapter` (`ssl`), `EmailAdapter` (`imaplib`/`smtplib`), `SmsAdapter` (`aiohttp`), `LineAdapter` (`aiohttp`), `HomeAssistantAdapter` (`aiohttp`), `FeishuAdapter` (`lark_oapi`, `websockets`), `DingTalkAdapter` (`dingtalk_stream`, `alibabacloud`, `httpx`), `WeComAdapter` (`cryptography`, `httpx`), `TeamsAdapter` (`aiohttp`, `httpx`), `GoogleChatAdapter` (`aiohttp`, `requests`), `SimplexAdapter` (`websockets`), `NtfyAdapter` (`httpx`), `PhotonAdapter` (`httpx`), `RaftAdapter` (`aiohttp`), `BuzzAdapter` (`nostr`, `websockets`), `A2AAdapter`, `WhatsAppAdapter` (`WhatsAppBehaviorMixin` + base). `gateway/relay/adapter.py:RelayAdapter` fronts platforms through the connector.

## Adding a Platform (verified from `ADDING_A_PLATFORM.md`)
**Plugin path (recommended):** `~/.hermes/plugins/<name>/` with `plugin.yaml` + `adapter.py` subclassing `BasePlatformAdapter`, registered via `ctx.register_platform()` — zero core edits. Optional hooks: `env_enablement_fn`, `apply_yaml_config_fn`, `cron_deliver_env_var`, `standalone_sender_fn`, plus `requires_env`/`optional_env` rich dicts.
**Built-in path (16 steps):** adapter with `connect`/`disconnect`/`send`/`send_typing`/`send_image`/`get_chat_info` + `check_<platform>_requirements()` → `Platform` enum + `_apply_env_overrides()` in `gateway/config.py` → `_create_adapter()` branch → **both** maps in `_is_user_authorized()` (`platform_env_map`, `platform_allow_all_map`) → `SessionSource` fields if needed → `PLATFORM_HINTS` in `agent/prompt_builder.py` → toolset in `toolsets.py` → delivery via `_send_to_platform()` in `tools/send_message_tool.py:925`, called from `cron/scheduler.py` (there is no `platform_map` dict in either file — the platform is a positional argument, so a new platform adds a branch there rather than a map entry) → `tools/cronjob_tools.py` schema → `gateway/channel_directory.py` discovery list → `hermes_cli/status.py` → `hermes_cli/gateway.py` `_PLATFORMS` → redaction → docs → `tests/gateway/test_<platform>.py`.

## Dependencies
- **Used by:** `run_agent.py`, `cli.py`, `toolsets.py`, `cron/scheduler.py`, `cron/executions.py`, `agent/system_prompt.py`, `agent/skill_commands.py`, `agent/agent_init.py`, `agent/relay_runtime.py`, `agent/monitoring/gateway_health.py`, `acp_adapter/server.py`, `hermes_cli/main.py`, `hermes_cli/gateway.py`, `hermes_cli/web_server.py`, `hermes_cli/status.py`, `hermes_cli/profiles.py`, `hermes_cli/commands.py`, `tools/send_message_tool.py`, `tools/cronjob_tools.py`.
- **Uses:** `run_agent.AIAgent`, `model_tools`, `hermes_state.SessionDB`, `hermes_constants.get_hermes_home`, `hermes_cli.config`, `hermes_cli.commands` (`resolve_command`, `should_bypass_active_session`, `GATEWAY_KNOWN_COMMANDS`), `hermes_cli.plugins` (`fire_pre_command_hook`), `agent.secret_scope` (`get_secret`, `current_secret_scope`, `is_multiplex_active`), `agent.conversation_compression`, `agent.turn_context`, `cron.jobs`; external: `asyncio`, `aiohttp`/`httpx`, `yaml`, `sqlite3`, per-platform SDKs above.

## Notable Patterns / Gotchas
- **Fail-closed profile env reads.** Under `gateway.multiplex_profiles`, `os.environ` holds the *default* profile's values; a secondary profile's `.env` exists only in its secret scope. `_platform_gate_env()` returns `default` on a scoped miss when a scope is installed and multiplex is active — it never falls through to `os.environ` (a leaked default allowlist skips the allow-all check and silently rejects every secondary-profile sender, #72348/#86905). The trailing `os.getenv()` read is only the unscoped/single-profile path. Adapters use the same shape as `plugins/platforms/feishu/adapter.py:_get_scoped_secret()` (copy-pasted across ~15 adapters); `_auth_env()` keeps the legacy `os.getenv` fallback for the unscoped default-profile route.
- **Token locks.** An adapter with a unique credential must take `acquire_scoped_lock(scope, identity)` from `gateway/status.py` in `connect()` and `release_scoped_lock()` in `disconnect()` — see `BasePlatformAdapter._acquire_platform_lock()` and the `irc`/`line`/`buzz`/`feishu` adapters. Records are machine-global and stamp a profile label so a "token already in use (PID N)" conflict is attributable.
- **Cron deliveries are not mirrored into the target chat session.** They land in the job's own cron session with a header/footer frame, so the main conversation's strict role alternation survives. Mirroring is opt-in only: per-job `attach_to_session` → global `cron.mirror_delivery` → `False` (`cron/scheduler.py:_cron_mirror_delivery_enabled`), and when on it is scoped to the job's *origin* chat via `mirror_to_session(..., role="assistant")`.
- **Two busy guards, one command registry.** Mid-run behavior is declared on `CommandDef.busy_policy`/`busy_handler` in `hermes_cli/commands.py`; `ACTIVE_SESSION_BYPASS_COMMANDS` is derived from it. Queueing a recognized slash command is always wrong — the runner's safety net discards command text that reaches the pending queue, which historically produced an interrupt *plus* a zero-char reply (#5057, #6252, #10370).
- **`_handle_message` resets session ContextVars on entry.** The per-message task is created with `copy_context()`, so it can inherit a *sibling* message's `HERMES_SESSION_*` values; `reset_session_vars()` at entry closes that subprocess-env leak window.
- **Hard exit is deliberate.** `main()` funnels every path through `_exit_after_graceful_shutdown()` → `os._exit()`, because `sys.exit` would trigger `Py_FinalizeEx` and join a wedged non-daemon tool worker (#53107). Consequence: `atexit` never runs, so PID file, runtime lock, lifecycle sentinel, and the async log queue are released/drained explicitly there.
- **No external control channel into a running gateway.** Restart/drain is driven only by slash commands, signals, and file markers (`drain_control.write_drain_request()`), with `restart_loop_guard` + `shutdown_watchdog` + `systemd_notify` as the supervisor-side backstops.
