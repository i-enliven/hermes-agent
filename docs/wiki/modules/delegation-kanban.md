# Module: `delegation & kanban`

Hermes has two multi-agent mechanisms that look alike and are not. `delegate_task` spawns **ephemeral, in-process** child `AIAgent`s inside the caller's Python process: they live and die with that turn, share the parent's interpreter, and return a summary string. Kanban is a **durable, cross-process** SQLite board: the dispatcher is a long-lived loop that claims rows and `subprocess`-spawns `hermes -p <profile> chat -q …` workers that survive a restart and never share memory with whoever created the card.

Reach for delegation when the work is a fan-out of research/analysis whose only consumer is the current conversation. Reach for Kanban when the work must be assigned to a *named profile*, survive process death, be inspected by a human on a board, or hand off structured results between agents that are not alive at the same time.

## Part 1 — Delegation (`delegate_task`)

### Responsibilities

- Build child `AIAgent`s with a fresh conversation, their own `task_id` (own terminal session + file-ops cache), and the parent's toolsets minus a child blocklist.
- Run single and batch (parallel) fan-outs, either joined into the parent's turn or dispatched to a daemon executor whose result re-enters the conversation later as a new turn.
- Expose a control plane (`list` / `steer` / `stop`) over already-running children.
- Keep the parent's prompt cache and message-role alternation legal: the parent sees only the delegation call and the summary, never the child's tool traffic.

### Key Files

- [`../../../tools/delegate_tool.py`](../../../tools/delegate_tool.py) — `DELEGATE_BLOCKED_TOOLS`, `DELEGATE_TASK_SCHEMA`, `delegate_task()`, `_build_child_agent()`, `_build_child_preserving_parent_tools()`, `_run_single_child()`, `_execute_and_aggregate()`, `_load_config()`.
- [`../../../tools/async_delegation.py`](../../../tools/async_delegation.py) — background dispatch + durable completion queue (`dispatch_async_delegation`, `dispatch_async_delegation_batch`, `recover_abandoned_delegations`, `restore_undelivered_completions`).
- [`../../../tools/delegation_live_log.py`](../../../tools/delegation_live_log.py) — append-only per-child transcripts at `<hermes_home>/cache/delegation/live/<delegation_id>/task-<n>.log`, pruned at `LIVE_RETENTION_DAYS = 7`.
- [`../../../tools/delegation_output_schema.py`](../../../tools/delegation_output_schema.py) — `output_schema` contract: `coerce_output_schema()`, `MAX_SCHEMA_RETRIES = 1` bounded correction turn.
- [`../../../agent/delegation_context.py`](../../../agent/delegation_context.py) — `delegated_child_context()`, `is_dispatcher_owned_worker_context()`, `scrub_kanban_env()`, `KANBAN_ENV_KEYS`.
- [`../../../agent/subagent_lifecycle.py`](../../../agent/subagent_lifecycle.py) — plugin-safe lifecycle contract (`SubagentLifecycleService`, `SubagentLaunchRequest`, `SubagentState`), reached via `PluginContext.subagent_lifecycle`.
- [`../../../toolsets.py`](../../../toolsets.py) — the `delegation` toolset (`tools: ["delegate_task"]`).

### Public API

Registered in `tools/delegate_tool.py` via `registry.register(name="delegate_task", toolset="delegation", …, check_fn=check_delegate_requirements, dynamic_schema_overrides=_build_dynamic_schema_overrides)`.

`DELEGATE_TASK_SCHEMA` parameters:

| Param | Shape |
|---|---|
| `goal`, `context` | single-task form |
| `tasks` | batch form — `[{goal, context, role, output_schema}, …]`, `goal` required per entry |
| `role` | `"leaf"` \| `"orchestrator"`; per-task `role` overrides the top-level one |
| `output_schema` | JSON Schema the child's final answer must validate against |
| `background` | **deprecated / ignored** — see Gotchas |
| `action` | `"spawn"` (default) \| `"list"` \| `"steer"` \| `"stop"` (`_CONTROL_ACTIONS = frozenset({"list", "steer", "stop"})`) |
| `subagent_id`, `message` | targets for `steer` / `stop` |

The `description`, `tasks.description` and `role.description` strings are rebuilt on every `get_definitions()` call by `_build_dynamic_schema_overrides()` so the model sees the user's real `max_concurrent_children` / `max_spawn_depth`, not the framework defaults.

Child blocklist, verbatim from source:

```python
DELEGATE_BLOCKED_TOOLS = frozenset([
    "delegate_task",   # no recursive delegation
    "clarify",         # no user interaction
    "memory",          # no writes to shared MEMORY.md
    "send_message",    # no cross-platform side effects
    "cronjob",         # no scheduling more work in the parent's name
])
```

`role="leaf"` (the default; `_normalize_role()` coerces unknown strings to `"leaf"`) ends up with none of the five in its schema, but the five are not removed alike: `delegate_task`, `clarify`, `memory` and `cronjob` are subtracted by the two deny layers, whereas `send_message` is never registered as an agent-callable tool at all (`tools/send_message_tool.py:2264`). Its blocklist entry is therefore defensive only: both layers remove toolsets *by name*, never by member — the deny layer needs a non-empty `tools` list that is a subset of the blocklist (`:1203-1204`), the strip layer needs membership of `_COMPOSITE_BLOCKED_TOOLSETS` **or** an all-blocked list (`:1180-1181`), and `kanban` is then dropped unconditionally (`:1183`). A toolset pairing `send_message` with any unblocked tool matches neither predicate, so what keeps it out of the schema is the missing registration, not the blocklist. `role="orchestrator"` re-adds `delegate_task` (`_blocked_toolsets_for_role()` discards it from the deny set) and keeps the `delegation` toolset, bounded by `max_spawn_depth`. `execute_code` is **not** in the blocklist — it lives in the separate `code_execution` toolset and survives the strip, so a leaf can still drive tools programmatically.

### Internal Structure

**Config resolution.** `_load_config()` reads the `delegation` block through `hermes_cli.config.load_config_readonly()` (profile-aware, no defensive deepcopy because it runs on every schema rebuild), falling back to `cli.CLI_CONFIG` only under `HERMES_IGNORE_USER_CONFIG=1`. Priority is `config.yaml` > env > default.

Real defaults, from the `delegation` block in [`../../../hermes_cli/config_defaults.py`](../../../hermes_cli/config_defaults.py):

| Key | Default |
|---|---|
| `max_concurrent_children` | `10` |
| `max_spawn_depth` | `1` |
| `child_timeout_seconds` | `0` (no stopwatch; floor `30` if set) |
| `orchestrator_enabled` | `True` |
| `subagent_auto_approve` | `False` |
| `inherit_mcp_toolsets` | `True` |
| `max_iterations` | `250` |
| `max_summary_chars` | `24000` |

`tools/delegate_tool.py` mirrors several of these as module constants: `_DEFAULT_MAX_CONCURRENT_CHILDREN = 10`, `MAX_DEPTH = 1` with `_MIN_SPAWN_DEPTH = 1`, `DEFAULT_MAX_ITERATIONS = 250`, `DEFAULT_MAX_SUMMARY_CHARS = 24000`, `DEFAULT_CHILD_TIMEOUT = None`, `DEFAULT_TOOLSETS = ["terminal", "file", "web"]`. `_get_max_concurrent_children()` enforces a floor of `1` and no ceiling, and warns once per process (`_HIGH_CONCURRENCY_WARNED`) above `10`.

**The process-global hazard.** `model_tools._last_resolved_tool_names` is process-wide, and building a child overwrites it with the *child's* resolved set. `_build_child_preserving_parent_tools()` wraps construction in `_CHILD_CONSTRUCTION_LOCK`, snapshots the parent list, restores it in a `finally`, and stashes it on `child._delegate_saved_tool_names`. `_run_single_child()` then restores that saved list in its own `finally` so later `execute_code` calls in the parent see the parent's tools. Any new reader of that global must assume it may be stale during a child run.

**Heartbeat and staleness.** `_run_single_child()` starts a `_heartbeat_loop()` that pushes the child's activity into `parent_agent._touch_activity()` every `_HEARTBEAT_INTERVAL = 30`s so the gateway inactivity timeout doesn't kill the parent. It counts cycles where `api_call_count`, `current_tool` and `last_activity_ts` are all frozen, against `_HEARTBEAT_STALE_CYCLES_IDLE = 15` (≈450s) or `_HEARTBEAT_STALE_CYCLES_IN_TOOL = 40` (≈1200s) depending on whether the child is inside a tool.

**Approvals.** Subagent worker threads never call `input()` — the parent's `prompt_toolkit` TUI owns stdin. The executor is built with `initializer=_set_subagent_approval_cb`, installing either `_subagent_auto_deny` (default, returns `"deny"`) or `_subagent_auto_approve` (returns `"once"`) per `delegation.subagent_auto_approve`. Gateway sessions are unaffected; they resolve approvals through `tools/approval.py`'s per-session queue.

**Async rail.** `tools/async_delegation.py` owns only the async lifecycle; the actual build+run is injected back as a `runner` callable so credential leasing, heartbeat and result-shaping stay in `delegate_tool`. A whole batch occupies **one** async slot (`dispatch_async_delegation_batch`), returning `{"status": "dispatched", "delegation_id": …}` or `{"status": "rejected", …}` at capacity (`_DEFAULT_MAX_ASYNC_CHILDREN = 3`). On completion it pushes `type="async_delegation"` onto the shared `process_registry.completion_queue`, which the CLI `process_loop` and the gateway's `_run_process_watcher` drain while the agent is idle — so a result becomes a **new turn**, never spliced between a tool result and an assistant message.

**Durability boundary.** Records persist in the `async_delegations` table of `state.db` (`_db_path() = get_hermes_home() / "state.db"`), with `delivery_state`, `delivery_attempts`, `owner_pid`, `owner_started_at`, `delivery_claim`. `recover_abandoned_delegations()` marks rows whose owner PID died as `state='unknown'`; `restore_undelivered_completions()` re-enqueues them stamped `restored=True`. Retention/cap constants: `_DURABLE_RETENTION_SECONDS = 7*24*60*60`, `_MAX_DURABLE_PENDING = 1000`, `_MAX_DELIVERY_ATTEMPTS = 8`, `_MAX_COMPLETION_REPLAY_AGE_S = 48*3600.0`. This makes a completion *reportable* after a restart — it does **not** make the child's work resumable. For work that must survive a restart, use `cronjob` or `terminal(background=True, notify_on_complete=True)`.

### Dependencies

- **Uses:** `run_agent.AIAgent`, `model_tools` (global + dispatch), `tools/registry.py`, `tools/daemon_pool.DaemonThreadPoolExecutor`, `tools/thread_context.propagate_context_to_thread`, `gateway.session_context`, `agent/interrupt_compat.request_hard_interrupt`, `tools/file_state`, `tools/terminal_tool.set_approval_callback`.
- **Used by:** the `delegation` toolset; `agent/subagent_lifecycle.py` (plugin surface); the TUI `/agents` pane via the `delegation.pause` RPC and `is_spawn_paused()`.

## Part 2 — Kanban (multi-agent work queue)

### Responsibilities

- Give multiple profiles a shared, durable queue of cards with dependencies, comments, events, runs and attachments.
- Reclaim stale claims, promote dependency-satisfied `todo` → `ready`, atomically claim, and spawn the assigned profile — the dispatcher loop.
- Pin each spawned worker to exactly one board and one task via the environment, so a worker cannot see or mutate another board.
- Deliver terminal events back to the gateway/TUI session that created the card.

### Key Files

- [`../../../hermes_cli/kanban.py`](../../../hermes_cli/kanban.py) — `build_parser()`; the `hermes kanban <verb>` CLI surface plus `_check_dispatcher_presence()` (warns at `create` time when nothing will pick the card up).
- [`../../../hermes_cli/kanban_db.py`](../../../hermes_cli/kanban_db.py) — the store and the kernel: `SCHEMA_SQL`, `kanban_home()`, `boards_root()`, `get_current_board()`, `create_task()`, `claim_task()`, `release_stale_claims()`, `_record_task_failure()`, `dispatch_once()` / `_dispatch_once_locked()`, `_default_spawn()`.
- [`../../../tools/kanban_tools.py`](../../../tools/kanban_tools.py) — the `kanban_*` model-facing toolset and its gating predicates.
- [`../../../gateway/kanban_watchers.py`](../../../gateway/kanban_watchers.py) — `GatewayKanbanWatchersMixin`: `_kanban_notifier_watcher()` (interval `5.0`s) and `_kanban_dispatcher_watcher()` (the embedded dispatcher).
- [`../../../agent/kanban_stop.py`](../../../agent/kanban_stop.py) — turn-end guard: a worker may only exit after `kanban_complete` or `kanban_block` (`_TERMINAL_KANBAN_TOOLS`, `_DEFAULT_MAX_ATTEMPTS = 2`), else a bounded synthetic nudge keeps the loop alive instead of a `protocol_violation`.
- [`../../../plugins/kanban/dashboard/`](../../../plugins/kanban/dashboard/) — web-dashboard tab (`manifest.json` tab `path: "/kanban"`, `entry: dist/index.js`, `api: plugin_api.py`).
- [`../../../plugins/kanban/systemd/`](../../../plugins/kanban/systemd/) — `hermes-kanban-dispatcher.service`, **marked DEPRECATED** in its own header; retained for hosts that cannot run a gateway, and it invokes the standalone dispatcher behind an explicit `--force`.
- Specs: [`../../hermes-kanban-v1-spec.pdf`](../../hermes-kanban-v1-spec.pdf), [`../../kanban/multi-gateway.md`](../../kanban/multi-gateway.md).

### Public API

**CLI verbs** — the real `sub.add_parser(...)` set in `hermes_cli/kanban.py` (45 top-level verbs; `list` also has the alias `ls`):

`init`, `boards`, `create`, `swarm`, `list`, `show`, `assign`, `set-model`, `reclaim`, `reassign`, `diagnostics`, `link`, `unlink`, `claim`, `comment`, `attach`, `attachments`, `attach-rm`, `complete`, `edit`, `block`, `schedule`, `unblock`, `request-review`, `request-changes`, `reopen-review`, `promote`, `archive`, `tail`, `dispatch`, `daemon`, `watch`, `stats`, `notify-subscribe`, `notify-list`, `notify-unsubscribe`, `log`, `runs`, `heartbeat`, `assignees`, `context`, `specify`, `decompose`, `gc`, `repair`.

`boards` has its own subverbs: `list`, `create`, `rm`, `switch`, `show`, `rename`, `set-default-workdir`.

**Model tools** — 14 registrations in `tools/kanban_tools.py`, all `toolset="kanban"`:

`kanban_show`, `kanban_list`, `kanban_complete`, `kanban_block`, `kanban_request_review`, `kanban_request_changes`, `kanban_heartbeat`, `kanban_comment`, `kanban_attach`, `kanban_attach_url`, `kanban_attachments`, `kanban_create`, `kanban_unblock`, `kanban_link`.

Two availability predicates split them, and a plain `hermes chat` session sees **zero** kanban tools:

- `_check_kanban_mode()` — worker surface. True when `HERMES_KANBAN_TASK` is set *and* `_is_dispatcher_owned_worker()`, or when the profile's config enables the `kanban` toolset.
- `_check_kanban_orchestrator_mode()` — board-routing surface, i.e. `kanban_list` and `kanban_unblock`. Returns **False** for a dispatcher-spawned worker (a worker closes its own card; it does not enumerate or unblock the board). `_require_orchestrator_tool()` is the belt-and-suspenders runtime guard behind the same split.

### Internal Structure

**Store.** `kanban_home()` resolves `HERMES_KANBAN_HOME` else `get_default_hermes_root()` — the board is **shared across profiles by design**; resolving through the active profile's `HERMES_HOME` would silently fork the board and break the dispatcher↔worker handoff. The `default` board's DB sits at `<root>/kanban.db`; additional boards at `<root>/kanban/boards/<slug>/` (`boards_root()`), with the active selection persisted in the one-line file `<root>/kanban/current`.

`SCHEMA_SQL` defines seven tables:

| Table | Carries |
|---|---|
| `tasks` | `id`, `title`, `body`, `assignee`, `status`, `priority`, `tenant`, `workspace_kind`/`workspace_path`/`branch_name`, `project_id`, `claim_lock`/`claim_expires`, `consecutive_failures`, `worker_pid`, `last_failure_error`, `max_runtime_seconds`, `last_heartbeat_at`, `current_run_id`, `skills`, `model_override`/`provider_override`/`reasoning_effort`, `max_retries`, `goal_mode`/`goal_max_turns`, `session_id`, `block_kind`/`block_recurrences`, `result`, `idempotency_key` |
| `task_links` | `parent_id`, `child_id` (composite PK) — the dependency DAG |
| `task_comments` | `task_id`, `author`, `body`, `created_at` |
| `task_events` | `task_id`, `run_id`, `kind`, `payload`, `created_at` — tailed by the notifier |
| `task_runs` | one row per attempt: `profile`, `status`, `claim_lock`, `worker_pid`, `last_heartbeat_at`, `outcome`, `summary`, `error` |
| `task_attachments` | `filename`, `stored_path`, `content_type`, `size` — blobs live under `attachments_root(board)/<task_id>/` |
| `kanban_notify_subs` | `(task_id, platform, chat_id, thread_id)` PK, `notifier_profile`, `delivery_mode` (`notify` \| `notify+wake` \| `wake`), `last_event_id` |

Claim state, PID, heartbeat and runtime cap live on the **run**, not the task; `tasks.current_run_id` is a denormalised pointer.

**Dispatcher.** `gateway/kanban_watchers.py::_kanban_dispatcher_watcher()` is the long-lived loop, started from `gateway/run.py` via `_spawn_supervised(...)`. It reads config once at boot, honours the `HERMES_KANBAN_DISPATCH_IN_GATEWAY` escape hatch, and is gated by `kanban.dispatch_in_gateway` (**default `True`** in `hermes_cli/config_defaults.py`). Each tick calls `kanban_db.dispatch_once()` inside `asyncio.to_thread` so the SQLite WAL lock never blocks the event loop. Interval: `dispatch_interval_seconds = 60`, floored at `1.0`.

Two locks guard it. A machine-global advisory lock at `<kanban_home>/kanban/.dispatcher.lock` (`_acquire_singleton_lock`) serialises *all* gateways on the host — a second one logs "will NOT dispatch" and returns. Per tick, `dispatch_once()` takes a non-blocking, board-scoped `_dispatch_tick_lock` keyed off the resolved DB path, so unrelated boards tick in parallel and a losing dispatcher returns `DispatchResult(skipped_locked=True)` with zero writes.

`claim_task()` performs the atomic `ready → running` compare-and-set: `UPDATE tasks SET status='running', claim_lock=?, claim_expires=? WHERE id=? AND status='ready' AND claim_lock IS NULL`, accepting only `rowcount == 1`. Before the CAS it enforces the structural invariant that no parent is outside `('done','archived')` — a racy promotion is demoted back to `todo` with a `claim_rejected` / `parents_not_done` event. TTL: `DEFAULT_CLAIM_TTL_SECONDS = 15 * 60`, overridable per call or by `HERMES_KANBAN_CLAIM_TTL_SECONDS`.

Stale reclaim: `dispatch_stale_timeout_seconds = 14400` in `hermes_cli/config_defaults.py` — a `running` task with no heartbeat for that long is reclaimed to `ready` (worker terminated first if host-local); `0` disables it. `reconcile_orphans = True` requeues `running` cards whose claim bookkeeping is broken.

**Failure circuit breaker.** `_record_task_failure()` increments `tasks.consecutive_failures` on `spawn_failed` / `timed_out` / `crashed` and auto-blocks past the limit. Resolution order: per-task `tasks.max_retries` → `kanban.failure_limit` config (**`2`**) → `DEFAULT_FAILURE_LIMIT = 2` (`hermes_cli/kanban_db.py`). Separately, `BLOCK_RECURRENCE_LIMIT = 2` routes a task re-blocked for the same reason twice to `triage` instead of `blocked`, so a cron cannot spin it forever; `block_recurrences` resets only on a successful completion.

**Isolation.** `_default_spawn()` fires `hermes -p <profile> chat -q "work kanban task <id>"` and pins the child's board identity into its environment: `HERMES_KANBAN_BOARD`, `HERMES_KANBAN_DB`, `HERMES_KANBAN_WORKSPACES_ROOT`, `HERMES_KANBAN_TASK`, `HERMES_KANBAN_WORKSPACE`, `HERMES_KANBAN_RUN_ID`, `HERMES_KANBAN_CLAIM_LOCK`, `HERMES_KANBAN_BRANCH`, plus `HERMES_HOME` (profile-scoped config), `HERMES_PROFILE`, and `TERMINAL_CWD` pinned to the workspace. **Board is the hard boundary** — a worker cannot resolve another board's DB. **Tenant is a soft namespace inside a board**: `tasks.tenant` is a column, and `env["HERMES_TENANT"] = task.tenant` only when set, giving one fleet multiple workspaces/memory keys without separate boards. The dispatcher also strips every `gateway.session_context._VAR_MAP` key so a worker never inherits a previous conversation's routing.

### Dependencies

- **Uses:** `sqlite3` (WAL), `hermes_constants.get_default_hermes_root()`, `hermes_cli.profiles.resolve_profile_env()`, `gateway.session_context`, `agent/delegation_context`, `agent/skill_utils`, `subprocess`.
- **Used by:** `gateway/run.py` (`GatewayKanbanWatchersMixin`), the dashboard plugin API, the `/kanban` gateway slash command (`notify-subscribe` is its transport), `hermes_cli/main.py` (pins `HERMES_KANBAN_BOARD` at boot).

## Notable Patterns / Gotchas

- **`background` is a no-op.** `DELEGATE_TASK_SCHEMA` documents `background` as "DEPRECATED / IGNORED". Top-level delegations *always* run in the background: `_model_background_value()` returns `not is_subagent`, i.e. the model does not choose. The one exception is a delegation from an `orchestrator` child (`_delegate_depth > 0`), which must join its workers inside its own turn. A one-shot host that cannot receive a detached completion (`hermes -z`, a cron job, a Kanban worker, a stateless HTTP endpoint) falls back to synchronous execution and stamps a `note` on the result.
- **`delegate_task` requires `parent_agent`.** Called without one it returns `tool_error("delegate_task requires a parent agent context.")`. The live dispatch path is `run_agent._dispatch_delegate_task`; the registry handler is the fallback.
- **A delegated child is not a Kanban run owner.** `_reject_delegated_child_mutation()` refuses every board mutation from a `delegate_task` child, because the child shares the parent's process and inherited `HERMES_KANBAN_*` vars are not proof of ownership. `agent/delegation_context.py::is_dispatcher_owned_worker_context()` is the single predicate every `HERMES_KANBAN_*` gate must consult; it is `False` for delegate children and for cron jobs run in-process from a worker (`non_dispatcher_owned_context()`), and `scrub_kanban_env()` strips `KANBAN_ENV_KEYS` from a child's subprocess env.
- **Two dispatchers on one `kanban.db` is unsupported.** Beyond the singleton lock, concurrent dispatchers double reclaim frequency and — with `wal_autocheckpoint=0` — concurrent manual WAL checkpoints can corrupt index pages. `hermes kanban daemon` therefore requires `--force` when a gateway already dispatches.
- **`max_concurrent_children` is a shared cap.** It bounds both in-batch parallelism *and* concurrent background delegation units; new async dispatches beyond it fall back to synchronous.
- **`orchestrator_enabled` is a kill switch, and degradation is silent.** When `False`, `role="orchestrator"` is forced to `"leaf"` (`_build_child_agent` logs and stashes the post-degrade role for introspection). Same silent-degrade posture as an unknown `role` string.
- **Summaries are budgeted, not truncated-and-forgotten.** `_apply_summary_budget()` sizes each child summary against the parent's remaining context headroom split across the batch; when it must trim, the full text spills to `~/.hermes/cache/delegation/` and the in-context summary becomes a head+tail window plus a `read_file` offset footer. `max_summary_chars` is only the hard ceiling on top.
- **Workers are OS processes, not threads.** Concurrency is bounded by the host, not per board: `kanban.max_in_progress` unset derives roughly `MemTotal / 512 MiB` clamped to `[2, 8]`, and `max_in_progress_per_profile` is an independent per-profile cap. Both are `None` by default in `hermes_cli/config_defaults.py`.
- **`kanban_complete` / `kanban_block` default their `task_id` to `$HERMES_KANBAN_TASK`.** `_enforce_worker_task_ownership()` rejects a worker passing a foreign `task_id`; orchestrator profiles (kanban toolset on, no `HERMES_KANBAN_TASK`) are exempt because routing is their job.
