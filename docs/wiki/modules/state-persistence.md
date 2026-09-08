# Module: state & persistence

Every surface — CLI, TUI, gateway, dashboard, `serve`, cron, ACP — reads and writes its sessions through one SQLite file. This layer owns that file, its three FTS5 indexes, its corruption-repair ladder, and the profile-aware paths that locate it. Remove it and `/resume`, `/title`, `/history`, `/branch`, session search, gateway routing, and the dashboard sidebar all go dark.

## Responsibilities

- Own `HERMES_HOME/state.db`: schema creation, declarative column reconciliation, version-gated data migrations, and the FTS5/trigram/CJK index surfaces.
- Serialize concurrent access from many processes (gateway + CLI sessions + cron + worktree agents) sharing one file, without letting lock contention destroy a user turn.
- Keep reads off the writer lock via a bounded read-only connection pool under WAL.
- Provide full-text search across all session messages with four routing paths (FTS5, trigram, CJK-bigram, LIKE) and a fail-open path when an index corrupts.
- Repair itself: zeroed-file quarantine, malformed-`sqlite_master` surgery, in-place FTS rebuild, cross-process repair locking with a persistent attempt ledger.
- Resolve every path profile-aware through `get_hermes_home()`, and write profile-aware rotating logs.

## Key Files

- [`hermes_state.py`](../../../hermes_state.py) — `SessionDB` (13k lines), `AsyncSessionDB`, WAL/pragma machinery, repair ladder, CJK extension loading, the live-DB test guard.
- [`hermes_state_common.py`](../../../hermes_state_common.py) — `SCHEMA_VERSION`, `SCHEMA_SQL`, `DEFERRED_INDEX_SQL`, `FTS_SQL`, `FTS_TRIGRAM_SQL`, `LEGACY_FTS_*_SQL`, stale-index breadcrumbs. Shared constants so the mixins never import `hermes_state` (cycle).
- [`hermes_state_schema.py`](../../../hermes_state_schema.py) — `SessionSchemaMixin`: `_init_schema()`, `_reconcile_columns()`, PK healers, FTS capability probes.
- [`hermes_state_search.py`](../../../hermes_state_search.py) — `SessionSearchMixin`: `search_messages()`, query sanitizer, CJK routing, chunked FTS backfill, `rebuild_fts()` / `optimize_fts()`.
- [`hermes_state_portability.py`](../../../hermes_state_portability.py) — `SessionPortabilityMixin`: rich session rows, `export_session()` / `export_all()` / `import_sessions()`, cron-run listing.
- [`hermes_constants.py`](../../../hermes_constants.py) — `get_hermes_home()`, `display_hermes_home()`, `get_default_hermes_root()`, well-known paths.
- [`hermes_logging.py`](../../../hermes_logging.py) — `setup_logging()`, rotating file handlers behind a `QueueListener`.
- [`hermes_time.py`](../../../hermes_time.py) — timezone-aware wall clock: `now()` ([hermes_time.py:now](../../../hermes_time.py)) resolves one IANA zone from `HERMES_TIMEZONE` → `config.yaml timezone:` → server-local.
- [`native/fts5_cjk/fts5_cjk.c`](../../../native/fts5_cjk/fts5_cjk.c) — the `cjk_unicode61` loadable tokenizer (real, 252 lines; built by `native/fts5_cjk/build.sh`).

## The Mixin Contract

`SessionDB` is one class assembled from three mixins: `class SessionDB(SessionSearchMixin, SessionSchemaMixin, SessionPortabilityMixin)` ([hermes_state.py:SessionDB](../../../hermes_state.py)). None of the three defines a dunder — no `__init__`, no context-manager protocol — so all construction and teardown stays in the host. They are *not* stateless, though: `SessionSchemaMixin` and `SessionSearchMixin` both write the host's FTS capability flags (`_fts_enabled`, `_trigram_available`, `_fts_cjk_available`, `_fts_stale`, `_fts_usermerge_floor_applied`). `SessionDB.__init__` seeds all five ([hermes_state.py:3222](../../../hermes_state.py)); the mixins only ever rewrite them, while probing SQLite for tokenizer availability. They also reach into the host's `self._conn`, `self.db_path`, `self._execute_write`. The rule that keeps the split from becoming a cycle: **a mixin must never import `hermes_state`**; anything shared lives in `hermes_state_common`. All three log under the borrowed logger name `hermes_state` so log filters survive the split.

## Callable Surface

`SessionDB` exposes **157 public methods** (AST count at `hermes_state.py:3093`); `SessionSearchMixin` adds 12 and `SessionPortabilityMixin` 9, while `SessionSchemaMixin` contributes none — all 18 of its methods are underscore-prefixed. Signatures are not reproduced here; the grouping is by name family so you can find the right one to open. The two mixin rows list only what is defined *on the mixin itself*; host-defined helpers for the same concern (for example `search_sessions`, `assert_export_safe`) sit in the rows above.

| Family | Methods |
|---|---|
| Session identity | `create_session`, `ensure_session`, `get_session`, `get_session_by_title`, `find_session_by_origin`, `resolve_session_id`, `list_sessions_rich`, `list_gateway_sessions`, `reopen_session`, `end_session`, `session_count`, `session_count_by_source`, `session_count_ge`, `session_lifecycle_statuses`, `session_gateway_runtime`, `session_unread`, `session_yolo_enabled`, `touch_session_activity`, `get_session_activity`, `close`, `clear_session_activity_labels`, `resolve_session_by_title`, `search_sessions` |
| Messages | `append_message`, `append_messages_batch`, `replace_messages`, `clear_messages`, `get_messages`, `get_messages_around`, `get_messages_as_conversation`, `message_count`, `latest_message_row_id`, `latest_user_message_row_id`, `get_message_role`, `get_message_reactions`, `set_message_reaction`, `take_unseen_reactions`, `has_archived_messages`, `has_platform_message_id`, `get_active_message_watermark`, `set_latest_user_api_content`, `set_latest_matching_message_display_kind`, `find_pr_url_messages`, `purge_stale_tool_call_markers` |
| Titles & meta | `get_session_title`, `set_session_title`, `set_auto_title`, `set_auto_title_if_empty`, `sanitize_title`, `get_next_title_in_lineage`, `get_session_title_source`, `set_session_title_source`, `get_meta`, `set_meta`, `update_session_meta`, `list_meta_prefix`, `update_system_prompt` |
| Model & usage | `update_session_model`, `patch_session_model_config`, `get_session_model_config_value`, `get_dominant_session_model_route`, `update_session_billing_route`, `update_token_counts`, `flush_token_counts`, `queue_token_counts`, `record_auxiliary_usage`, `usage_totals` |
| Compression lineage | `get_compression_lineage`, `get_compression_tip`, `get_conversation_root`, `find_live_compression_child`, `publish_compression_child`, `finalize_orphaned_compression_sessions`, `reopen_orphaned_compression_session`, `try_acquire_compression_lock`, `refresh_compression_lock`, `release_compression_lock`, `get_compression_lock_holder`, `get_compression_failure_cooldown`, `get_compression_failure_cooldown_row`, `record_compression_failure_cooldown`, `clear_compression_failure_cooldown`, `restore_compression_failure_cooldown_row`, `get_compression_fallback_streak`, `set_compression_fallback_streak`, `get_compression_ineffective_count`, `set_compression_ineffective_count` |
| Turn leases & handoffs | `acquire_session_turn_lease`, `try_acquire_session_turn_lease`, `refresh_session_turn_lease`, `release_session_turn_lease`, `request_handoff`, `claim_handoff`, `complete_handoff`, `fail_handoff`, `get_handoff_state`, `list_pending_handoffs` |
| Prune / archive / vacuum | `prune_sessions`, `list_prune_candidates`, `count_open_prune_matches`, `count_empty_sessions`, `delete_empty_sessions`, `prune_empty_ghost_sessions`, `prune_never_active_keyed_sessions`, `list_never_active_keyed_sessions`, `maybe_auto_archive`, `maybe_auto_prune_and_vacuum`, `archive_sessions`, `archive_stale_sessions`, `archive_and_compact`, `vacuum`, `logical_size_bytes` |
| Delete & rewind | `delete_session`, `delete_sessions`, `delete_session_if_empty`, `get_session_delete_targets`, `promote_to_session_reset`, `rewind_to_message`, `restore_rewound`, `resolve_resume_session_id`, `get_resume_conversations`, `get_resume_message_count`, `get_ancestor_display_prefix` |
| Row flags | `set_session_pinned`, `set_session_archived`, `set_session_hidden`, `set_session_read`, `set_session_yolo`, `set_expiry_finalized` |
| Gateway routing | `load_gateway_routing_entries`, `replace_gateway_routing_entries`, `save_gateway_routing_entry`, `delete_gateway_routing_entries`, `record_gateway_session_peer`, `find_orphaned_gateway_sessions`, `find_latest_gateway_session_for_peer`, `adopt_orphaned_gateway_session` |
| Telegram topics | `enable_telegram_topic_mode`, `disable_telegram_topic_mode`, `is_telegram_topic_mode_enabled`, `bind_telegram_topic`, `delete_telegram_topic_binding`, `get_telegram_topic_binding`, `get_telegram_topic_binding_by_session`, `list_telegram_topic_bindings_for_chat`, `list_unlinked_telegram_sessions_for_user`, `is_telegram_session_linked_to_topic`, `apply_telegram_topic_migration` |
| Hygiene & misc | `increment_hygiene_failure_streak`, `reset_hygiene_failure_streak`, `assert_export_safe`, `assert_resume_safe`, `retag_kanban_worker_sessions`, `backfill_repo_roots`, `publish_session_git_metadata`, `update_session_cwd`, `update_session_runtime_lock` |
| Search (`SessionSearchMixin`) | `search_messages`, `search_sessions_by_id`, `rebuild_fts`, `optimize_fts`, `optimize_fts_storage`, `fts_rebuild_status`, `fts_rebuild_step`, `fts_cjk_rebuild_status`, `fts_cjk_rebuild_step`, `fts_optimize_available`, `get_anchored_view`, `list_recent_user_messages` |
| Portability (`SessionPortabilityMixin`) | `export_session`, `export_all`, `export_session_lineage`, `import_sessions`, `get_session_rich_row`, `distinct_session_cwds`, `list_cron_job_runs`, `list_skill_scaffolded_sessions`, `get_first_assistant_text` |

Treat the counts as a navigation aid, not a contract — re-derive them with an AST walk rather than pinning them in a test.

## Schema

`SCHEMA_VERSION = 26` ([hermes_state_common.py:SCHEMA_VERSION](../../../hermes_state_common.py)) and `SCHEMA_SQL` is the single source of truth. Tables:

| Table | Purpose |
|---|---|
| `schema_version` | one-row version gate for *data* migrations only |
| `system_prompts` | `hash → prompt`, deduplicated; `sessions` holds the hash |
| `sessions` | identity (`source`, `user_id`, `session_key`, `chat_id`, `thread_id`), model + `model_config`, token/cost columns, `parent_session_id` lineage, `title`, `archived`/`pinned`/`hidden`, `profile_name` |
| `messages` | `id INTEGER PRIMARY KEY AUTOINCREMENT`, `role`, `content`, `tool_calls`, `reasoning*`, `api_content`, `platform_message_id`, `active`, `compacted` |
| `session_model_usage` | per-`(session, model, billing_*, task)` usage and cost |
| `state_meta` | key/value: FTS rebuild markers, stale breadcrumbs, `fts_storage_version` |
| `gateway_routing` | `(scope, session_key) → entry_json` |
| `gateway_hygiene_state` | per-`session_key` failure streaks |
| `compression_locks` | durable lease: `holder`, `acquired_at`, `expires_at` |
| `session_turn_leases` | same shape, keyed by `conversation_id` |
| `async_delegations` | background `delegate_task` results + delivery state machine |

Two-phase DDL is load-bearing. `SCHEMA_SQL` runs first via `executescript`; indexes that reference columns the reconciler will `ADD` live in `DEFERRED_INDEX_SQL` and run *after* `_reconcile_columns()` — declaring them up front makes the initial script fail on legacy DBs with `no such column: active`.

### Declarative reconciliation, not a migration chain

`_init_schema()` executes `SCHEMA_SQL`, then `_reconcile_columns()` diffs live columns against the parsed DDL and `ALTER TABLE ADD`s any missing one ([hermes_state_schema.py:_init_schema](../../../hermes_state_schema.py)). Adding a column to `SCHEMA_SQL` is the whole change; reordered or inserted migrations can no longer skip columns. The `schema_version` row survives only for transforms that cannot be expressed declaratively. Two repairs fall outside what `ADD COLUMN` can express and are explicit healers: `_heal_gateway_routing_pk()` and `_heal_session_model_usage_pk()` rebuild a table whose `PRIMARY KEY` predates a PK column.

The column parse is memoized to disk at `get_hermes_home() / "cache" / "schema_columns.json"`, keyed by a SHA-256 of the DDL text ([hermes_state_schema.py:_parse_schema_columns](../../../hermes_state_schema.py)).

## Connection Lifecycle

`SessionDB.__init__(db_path=None, read_only=False)` ([hermes_state.py:SessionDB](../../../hermes_state.py)) resolves the path through `_default_db_path()` → `get_hermes_home() / "state.db"`, deliberately re-resolving at call time rather than trusting the import-time `DEFAULT_DB_PATH` snapshot — a fixture that redirects `HERMES_HOME` after collection would otherwise keep pointing every default `SessionDB()` at the developer's real file. Order for a writable open, each step load-bearing: (1) `_ensure_test_isolation()` — fail hard before any connection, `mkdir`, pragma, or byte probe; (2) `preflight_db_writability()` — read-only file/sidecar probe, repair-or-refuse with an actionable message instead of an opaque error from inside `_init_schema`; (3) `is_zeroed_state_db()` / `quarantine_zeroed_state_db()` — a size>0 file with an all-NUL header is *preserved*, never deleted, and a fresh DB opens; (4) `_connect_tracked_db(...)` with `timeout=1.0`, `isolation_level=None` (the code manages transactions; Python's auto-start would fight `BEGIN IMMEDIATE`), `check_same_thread=False`; (5) `apply_wal_with_fallback()` → `self._wal_active`, then `apply_database_pragmas()`, `PRAGMA foreign_keys=ON`, `load_fts5_cjk_extension()`, `_init_schema()`; (6) on the malformed-schema class, `repair_state_db_schema()` then one reopen; (7) `finally:` — if initialization did not complete, the half-built connection is closed via `_close_connection_quietly()` so a leaked tracked fd cannot later block `_backup_db_file()`'s raw copy and cost the repair its forensic backup. Steps (4)–(5) are wrapped in `_connect_and_init_with_lock_patience()`: `_init_schema`'s DDL runs on a 1s-timeout connection with no retry, so a sibling holding the write lock (VACUUM, a TRUNCATE checkpoint at close, an older pre-update process's FTS pass) used to fail the *entire* open and callers disabled persistence for the whole run; non-lock errors, including the malformed class, propagate immediately.

`close()` drains the token writer, unregisters the `atexit` hook, drains the read pool, then checkpoints **PASSIVE** — never TRUNCATE: transient per-cron connections close many times an hour and a TRUNCATE fires a full WAL reset that races the gateway's live writer and tears B-tree pages. `__enter__`/`__exit__` exist so an owning scope is exception-safe by construction; `__exit__` returns `False` and never suppresses. `__del__` is a last-resort net that delegates to `close()`.

## Concurrency Model

Multiple processes share one `state.db`, so SQLite's built-in busy handler is deliberately bypassed: the connection timeout is 1s and retries happen in `_execute_write()` with random jitter, which staggers competing writers and breaks the convoy that a deterministic backoff creates. Writes run under `BEGIN IMMEDIATE` (taking the WAL write lock at transaction start, not at commit, so contention surfaces immediately), then `commit()`; the callee must not commit itself.

Patience is **time-based, not attempt-based** — a shared store is legitimately held for seconds by a sibling, and an attempt-counted budget silently loses that race and surfaces as `session_persistence_failed`, a destroyed turn:

| Budget | Value | Used by |
|---|---|---|
| `_WRITE_PATIENCE_S` | 20.0 | routine writes |
| `_TRANSCRIPT_WRITE_PATIENCE_S` | 60.0 | `append_message()`, session-row creation — the writes whose failure aborts a turn |
| `_ACTIVITY_WRITE_PATIENCE_S` | 0.5 | heartbeat/label writes on the response-critical path; a skip is retried next window |
| `_COMPRESSION_BUSY_WAIT_S` | 5.0 | a live compression lease — short on purpose, since the lease is a correctness boundary, not a busy signal |

Jitter is 20–150ms, widening to 250ms–1s once the lock has been held past `_WRITE_RETRY_SLOW_AFTER_S = 2.0`.

**Reads do not take the writer lock.** `_read_ctx()` borrows a read-only connection from a bounded `queue.LifoQueue(maxsize=_READ_POOL_MAX)` guarded by a `threading.BoundedSemaphore` — the pool bounds the *idle* set, the permits bound the *peak*. The permit is acquired non-blocking on purpose: a reader that cannot get one degrades to the locked writer connection rather than converting fd exhaustion into a stall. The bug this replaced was per-thread connections pinning one fd pair per (SessionDB × thread) until a service-managed process hit its `RLIMIT_NOFILE` and every request failed with EMFILE while the process stayed alive, so the supervisor's restart-on-exit never fired. Under DELETE journal mode (the NFS/ZFS fallback) readers keep the legacy locked single-connection path, because a reader there can hit `SQLITE_BUSY` storms mid-write.

Maintenance is amortised into the write path so it never monopolises the lock: every `_CHECKPOINT_EVERY_N_WRITES = 50` writes a PASSIVE checkpoint; every `_FTS_MERGE_EVERY_N_WRITES = 1000` a *bounded* FTS5 `merge` (`_FTS_MERGE_MAX_PAGES_PER_INDEX = 500`, `_FTS_MERGE_COMMANDS_PER_PASS = 4`) instead of the unbounded `optimize`, which measured 9–18s of held write lock per index on a 10GB production DB. `usermerge` is lowered to 2 so positive-rank merges act on any level with ≥2 segments, or a fragmented index never converges.

## WAL, Journal Mode, and Platform Barriers

`resolve_journal_mode()` reads `database.journal_mode` from `config.yaml` (`wal` default; `delete` for filesystems without WAL-safe durability — macOS virtiofs, NFS, SMB). `apply_wal_with_fallback()` returns the mode actually set and degrades to DELETE on the markers in `_WAL_INCOMPAT_MARKERS` (`locking protocol`, `not authorized`, `disk i/o error`), also catching the quiet macOS-NFS case where the pragma *returns* the still-effective mode without raising; `WalUnsupportedError` subclasses `sqlite3.OperationalError` so existing DB-init handlers still catch it while WAL-mandating callers catch the narrower type. Two invariants are worth internalising: **never live-downgrade an on-disk WAL database** (peer connections may hold it open; `_on_disk_journal_mode()` reads the header, retrying transient `disk i/o error`, so the "mode unknown → refuse to downgrade" branch is reachable), and **never TRUNCATE the WAL on the shared store** (`_try_wal_checkpoint()` is PASSIVE-only; the WAL is bounded instead by `_WAL_SIZE_LIMIT_BYTES = 64 MiB` via `PRAGMA journal_size_limit`, because SQLite's default `-1` leaves `state.db-wal` pinned at the high-water mark of the largest transaction ever run — observed 3.07GB stranded on a 3.0GB DB, taking the host to 100% full).

Platform-specific: `_apply_macos_checkpoint_barrier()` sets `PRAGMA checkpoint_fullfsync=1` only on `darwin`, where `fsync()` does not guarantee a write barrier, so a launchd *system* shutdown can drop in-flight pages and leave a malformed image; `_enforce_macos_synchronous_full()` pins `synchronous=FULL`. `is_sqlite_wal_reset_vulnerable()` / `sqlite_source_id()` gate the upstream WAL-reset bug (fixed in 3.51.3, backports 3.50.7 / 3.44.6): on vulnerable builds Hermes refuses to *enable* WAL for a fresh DB. `apply_database_pragmas()` applies the operator's `database:` section — `cache_size`, `mmap_size`, `temp_store`, `wal_autocheckpoint`, `journal_size_limit` — to every connection type, best-effort, and never touches journal mode.

## Writing Sessions and Messages

`create_session()` → `_insert_session_row()`; `append_message()` ([hermes_state.py:append_message](../../../hermes_state.py)) is the single-message path and `append_messages_batch()` the bulk one. Both serialize structured fields to JSON *before* entering the write transaction (sqlite3 cannot bind `list`/`dict`), scrub lone surrogates, and store multimodal content JSON-encoded. `append_message()` returns the row id and bumps `message_count` (and `tool_call_count` when tool calls are present) in the same transaction, and rides the 60s transcript patience. `AsyncSessionDB` ([hermes_state.py:AsyncSessionDB](../../../hermes_state.py)) is a generic `__getattr__` forwarder wrapping every callable in `asyncio.to_thread`, so a blocking SQLite call cannot freeze an event loop.

`api_content` is a byte-fidelity sidecar: the exact string sent to the provider when it differs from `content` (ephemeral injections, persist overrides), so a prompt-cache-stable replay is possible. `display_kind` / `display_metadata` are presentation-only and never change the role/content replayed to a provider. System prompts are content-addressed: `_store_system_prompt()` inserts `INSERT OR IGNORE INTO system_prompts (hash, prompt)` and the session stores only the hash; `_delete_unreferenced_system_prompts()` GCs hashes no `sessions` row references.

Token accounting is off the turn thread: `queue_token_counts()` appends to a deque and a demand-started daemon writer drains it; the writer retires after `_TOKEN_WRITER_IDLE_SECONDS = 30.0` idle so its bound target does not pin an abandoned `SessionDB` (and its descriptors) forever, and the `atexit` drain hook holds only a weak reference. `flush_token_counts(timeout=5.0)` forces a drain; `close()` stops the writer first because the writer needs the connection.

## Search

`search_messages()` sanitizes, refreshes cross-process stale state, and routes. `_sanitize_fts5_query()` caps input at `MAX_FTS5_QUERY_CHARS = 2_048` before any regex runs, protects balanced quoted phrases with a linear scan (no backtracking on pathological quote runs), strips unmatched FTS5-special characters, and quotes hyphenated/dotted terms so `chat-send` does not split.

`_describe_search_path()` names the route for the slow-query log (`HERMES_SEARCH_SLOW_MS`, default 1000):

| Path | Condition |
|---|---|
| `fts5` | no CJK in the sanitized query |
| `fts_cjk` | `_fts_cjk_available` and no lone 1-char CJK run |
| `trigram` | ≥3 CJK chars, every token ≥3, `_trigram_available` |
| `like_scan` | short CJK tokens, `role='tool'` filters, or `_fts_stale` |

The CJK index exists because SQLite's `unicode61` treats a CJK run as ONE token, so a 2-char Korean query can never match inside it, and the stock `trigram` tokenizer needs ≥3 chars per term — below that, queries fell through to a full-table `LIKE` scan measured at 3–6s on a 6.8GB `messages` table. `cjk_unicode61` wraps `unicode61` and re-emits maximal CJK runs as overlapping **bigrams** (Lucene `CJKAnalyzer` semantics), giving exact substring semantics at index speed down to 2-char terms. It is a real loadable extension: `native/fts5_cjk/fts5_cjk.c`, entry point `sqlite3_ftscjk_init`, expected at `get_hermes_home() / "lib" / "libfts5_cjk.so"` (override with `HERMES_FTS5_CJK_SO`), gated by `config.yaml sessions.cjk_fts` via the `HERMES_CJK_FTS` bridge. `load_fts5_cjk_extension()` returns `False` — never raises — when the `.so` is absent, the feature is off, or the Python build has extension loading compiled out; every caller then behaves exactly as if the index had never existed.

Both optional indexes read through a `*_src` view that excludes `role='tool'` rows: tool output is ~90% of message bytes and almost entirely machine noise, and the trigram index costs ~2.6× the text it covers. Tool rows stay fully stored and fully searchable via `messages_fts`; they just do not get substring treatment, so a CJK query filtering `role='tool'` routes to the LIKE fallback — where `_compile_like_boolean_query()` compiles the supported boolean subset into `LIKE` predicates (`python NOT java` → positive match plus exclusion), escaping `%`/`_`/`\` with `ESCAPE '\'`.

## FTS Consistency and Self-Healing

Three FTS5 virtual tables are maintained: `messages_fts` (external-content over `messages`), `messages_fts_trigram`, `messages_fts_cjk` — `_FTS_TABLES`. The v23 external-content layout (`FTS_STORAGE_VERSION = 1`) stores no duplicate copy of the text; `LEGACY_FTS_SQL` / `LEGACY_FTS_TRIGRAM_SQL` exist only so a pre-v23 install keeps working until the user opts into `hermes sessions optimize-storage`. Fresh installs are born on v23; the migration is deliberately foreground and never automatic because it is disk-heavy and long.

Every sync trigger gates on the same predicate over two `state_meta` keys, `fts_rebuild_high_water` (H) and `fts_rebuild_progress` (P): a row is indexed iff `id > H` (inserted after the drop, indexed live) or `id <= P` (backfilled). Firing an external-content `'delete'` for a rowid the index never held is the canonical FTS5 corruption hazard; when no rebuild is pending, `COALESCE(..., -1)` turns the predicate into a tautology. UPDATE triggers use `AFTER UPDATE OF content, tool_name, tool_calls` — the `OF` variant skips non-content writes (`active`, `compacted`, `observed`) entirely, which is stronger than the `WHEN` gate and avoids I/O saturation on large stores. `_migrate_broad_fts_update_triggers()` rewrites pre-existing broad triggers, since `CREATE TRIGGER IF NOT EXISTS` cannot replace one.

Backfill runs in `_FTS_REBUILD_CHUNK_ROWS = 500`-row chunks, each in its own short transaction, with an inter-chunk pause of `max(_FTS_REBUILD_MIN_PAUSE, cost × _FTS_REBUILD_DUTY_FACTOR)` capping this process's share of DB bandwidth (an early greedy loop held the write lock ~85% of the time and froze concurrent CLI sessions). Progress is compare-and-swapped on the marker rows, so a second runner just interleaves chunks. Demoted v22 shadow tables are renamed to `fts_v22_trash_*` and emptied in bounded chunks before being dropped cheaply.

Two breadcrumbs make degradation visible across processes: `FTS_STALE_KEY` (a base/trigram index detached after runtime corruption — startup must rebuild before reinstalling triggers, or an unknown gap is preserved) and `FTS_CJK_STALE_KEY` (a tokenizer-less process dropped the cjk triggers, so the index must not serve reads until a capable host rebuilds it). `_refresh_fts_stale_state()` observes a peer's fail-open and disables the affected paths locally.

Recovery escalates least-destructive first, all reachable from the shared write boundary: `_try_runtime_fts_rebuild()` (one-shot per instance `INSERT INTO <t>(<t>) VALUES('rebuild')`, rewriting segments from the canonical `messages` rows with zero row mutation) → `_enter_fts_fail_open()` (drop the triggers and set the stale breadcrumb atomically so canonical writes continue) → `_reconnect_after_notadb()` (one-shot close/reopen when the backing file was replaced or truncated under us — a forked child inheriting and closing the write fd wedges a gateway permanently without this) → `repair_state_db_schema()` (offline surgery for a malformed `sqlite_master`, e.g. two `CREATE VIRTUAL TABLE messages_fts` rows, where even `PRAGMA` statements fail and only `PRAGMA writable_schema=ON` plus direct `sqlite_master` edits work: rebuild FTS in place, then de-duplicate `sqlite_master` keeping the lowest rowid per `(type, name)`, then drop the FTS schema and `VACUUM`). Canonical `sessions`/`messages` rows are never modified.

The surgery is serialised by `_cross_process_repair_lock()` (the gateway, the Desktop backend, and interactive CLIs all open the same file, and two concurrent `writable_schema` edits are themselves a corruption source), claimed at most once per path per process by `_claim_repair_attempt()`, and capped across restarts by a persistent ledger — otherwise an unhealable class re-ran the whole surgery and took a fresh multi-hundred-MB backup on every restart, forever. `_backup_db_file()` copies the DB plus its `-wal`/`-shm` sidecars and `_prune_malformed_backups()` keeps the newest `_MAX_MALFORMED_BACKUPS = 3`.

## Profile-Aware Path Resolution

`get_hermes_home()` ([hermes_constants.py:get_hermes_home](../../../hermes_constants.py)) resolves in three tiers: a ContextVar override (`set_hermes_home_override()` — the seam multiplexed profiles use to give a turn a different home), then `HERMES_HOME`, then `_get_platform_default_hermes_home()` (`Path.home() / ".hermes"` on POSIX; `%LOCALAPPDATA%\hermes` on Windows). It is **not** cached and it does **not** raise — raising there would brick the 30-odd module-level callers; a profile whose `HERMES_HOME` is unset only emits a one-shot stderr warning. `get_process_hermes_home()` is the deliberate escape hatch that ignores the ContextVar and reads the process env.

`display_hermes_home()` returns a `str` for *user-facing* text only, rewriting a home under the OS home as `~/…`; it is not a path source. `hermes_home_key()` is the normalising key (`expanduser().resolve().normcase`) used to compare two homes. **Profile operations are HOME-anchored, not `HERMES_HOME`-anchored** — and the anchor is not the literal `Path.home()/".hermes"/"profiles"`: `_get_profiles_root()` returns `_get_default_hermes_home() / "profiles"` ([hermes_cli/profiles.py:_get_profiles_root](../../../hermes_cli/profiles.py)), delegating to `get_default_hermes_root()` ([hermes_constants.py:get_default_hermes_root](../../../hermes_constants.py)) — the *root*, memoized on `(native_home, env_home)`, unwinding `HERMES_HOME` back to it when it sits under `~/.hermes` or under `.../profiles/<name>`. That is what lets `hermes -p coder profile list` see every profile regardless of which is active; anchoring on `get_hermes_home()` would hide all siblings. On a Docker/custom layout where `HERMES_HOME` is outside `~/.hermes`, the root *is* `HERMES_HOME` and so is the profiles root.

Module-level constants that cache `get_hermes_home()` at import are safe **because of an import-order contract**: `_apply_profile_override()` ([hermes_cli/main.py:_apply_profile_override](../../../hermes_cli/main.py)) pre-parses `--profile`/`-p` out of `sys.argv`, sets `os.environ["HERMES_HOME"]`, and strips the flag — and it is *called at module scope* at `hermes_cli/main.py:692`, before the first `hermes_cli.config` import at `:698` and before any heavy surface is imported. `hermes_state.py:349`'s `DEFAULT_DB_PATH = get_hermes_home() / "state.db"` therefore caches the already-overridden value; `_default_db_path()` still re-resolves at call time so a runtime redirect wins.

## On-Disk Layout Owned by This Layer

Every name below is a literal in the cited source; none is inferred.

| Path (under `HERMES_HOME`) | Owner |
|---|---|
| `state.db` | `DEFAULT_DB_PATH`, `_default_db_path()` |
| `state.db-wal`, `state.db-shm` | SQLite; backed up and writability-preflighted as a set with the main file |
| `state.db.repair.lock` | `_cross_process_repair_lock()` |
| `state.db.repair-attempts.json` | `_repair_ledger_path()` |
| `state.db.malformed-backup-<stamp>[_<seq>]` | `_backup_db_file()` |
| `state.db.zeroed-<ts>-<pid>.bak` | `quarantine_zeroed_state_db()` |
| `state.db.quarantine.lock` | `quarantine_zeroed_state_db()` |
| `state.db.pre-clean-markers-backup-<stamp>` | the `clean-markers` backup (`VACUUM INTO`) |
| `state-snapshots/` | referenced by the zeroed-DB recovery hint (`hermes snapshot list` / `restore`) |
| `logs/agent.log`, `logs/errors.log` | `setup_logging()`, always |
| `logs/gateway.log`, `logs/gui.log` | `setup_logging()`, when `mode="gateway"` / `mode="gui"` |
| `lib/libfts5_cjk.so` | `fts5_cjk_so_path()` |
| `cache/schema_columns.json` | `_parse_schema_columns()` memo |
| `sessions/sessions.json` | legacy, read once by the v18 backfill `_backfill_gateway_metadata_from_sessions_json()` |

## Logging

`setup_logging()` is keyword-only (`hermes_home`, `log_level`, `max_size_mb`, `backup_count`, `mode`, `force`) and returns the log directory. Profile-awareness has exactly one point: `home = hermes_home or get_hermes_home()`, then `log_dir = home / "logs"`. There are **no** `HERMES_LOG_*` env overrides — configuration comes from `config.yaml logging.{level,max_size_mb,backup_count}` via `_read_logging_config()`.

| File | Level | Max bytes | Backups |
|---|---|---|---|
| `agent.log` | `log_level` → `logging.level` → `INFO` | `max_size_mb` → 5 MiB | `backup_count` → 3 |
| `errors.log` | `WARNING` (fixed) | 2 MiB (fixed) | 2 (fixed) |
| `gateway.log` | `INFO` (fixed) | 5 MiB (fixed) | 3 (fixed) |
| `gui.log` | `INFO` (fixed) | 10 MiB (fixed) | 5 (fixed) |

Handlers are `_ManagedRotatingFileHandler`, a `RotatingFileHandler` subclass that reopens the file when an external process rotates it (`_reopen_if_externally_rotated()`) and `chmod`s to `0o660` in managed installs; the base is platform-aliased — `concurrent_log_handler.ConcurrentRotatingFileHandler` on Windows (rename-while-open raises `WinError 32`), stdlib elsewhere. Two consequences trip people up: handlers do **not** attach to the root logger (one `QueueListener` feeds them over a single queue via `_NonFormattingQueueHandler`), so `logging.getLogger().handlers` is the wrong place to look — use `rotating_file_handlers()` and `flush_log_queue()` / `drain_log_queue()` before asserting on file contents; and the `_logging_initialized` idempotence guard is checked *after* registration, with duplicate avoidance living in `_add_rotating_handler()`'s resolved-`baseFilename` comparison. Third-party noise (`openai`, `httpx`, `httpcore`, `asyncio`, …) is pinned to `WARNING`; `COMPONENT_PREFIXES` maps logger-name prefixes to the `--component` filter; `set_session_context()` injects the session tag seen in `_LOG_FORMAT`.

## Rules for Contributors

1. **Never hardcode `~/.hermes`.** Use `get_hermes_home()` for every code path and `display_hermes_home()` for every user-facing string. Hardcoding breaks profiles — each profile is its own `HERMES_HOME` — and was the source of 5 bugs in PR #3575. Reading `~/.hermes/*` via `Path.home() / ".hermes"` instead of `get_hermes_home()` is a bug to fix at the callsite.
2. **Import-time caching is allowed, but only behind the profile override.** `_apply_profile_override()` runs at `hermes_cli/main.py:692` before the heavy imports, so a module-level `get_hermes_home()` constant sees the overridden value. Anything that runs before that line, or in a process that never imports `hermes_cli.main`, must resolve lazily.
3. **Tests must not write to the real `~/.hermes`.** The autouse fixture is `_hermetic_environment` in [`tests/conftest.py`](../../../tests/conftest.py), which points `HERMES_HOME` at `tmp_path / "hermes_test"` and re-points `HERMES_TEST_ISOLATION` at it so spawned children inherit the marker. `_isolate_hermes_home` still exists but is now a no-op back-compat alias — do not treat it as the mechanism. The last line of defence is in the store itself: `_ensure_test_isolation()` raises `RuntimeError` if a pytest-context process (detected by `PYTEST_CURRENT_TEST` / `PYTEST_VERSION` / `HERMES_TEST_ISOLATION`, or by walking process ancestors with `psutil` — the one signal that survives a child's rebuilt environment, and it fails open to `False` when psutil is unavailable) resolves a production `state.db`. Opt out only via `@pytest.mark.live_system_guard_bypass` (wired by the `_state_db_write_guard` fixture) or `HERMES_STATE_DB_GUARD_BYPASS=1` in a child.
4. **Release handles deterministically.** A `SessionDB` you created is yours to close — `with SessionDB(path) as db:` or an explicit `close()`. `__del__` is a net, not a policy: historically an instance with a started token writer pinned itself through a bound-method target plus a strong `atexit` hook, so `__del__` never ran for exactly the instances that leaked descriptors. Connections open through `_connect_tracked_db()` → `connect_tracked()` ([hermes_cli/sqlite_safe_read.py:connect_tracked](../../../hermes_cli/sqlite_safe_read.py)) and register their fd, which is what makes a byte-level probe of a live database refuse rather than cancel the POSIX advisory locks this process holds — including a running VACUUM's `EXCLUSIVE` lock. Never `open()`/`read()` a state.db header directly; use `read_header_bytes_preopen()`.
5. **Do not add a `TRUNCATE` checkpoint or a broad `AFTER UPDATE` trigger to this store.** Both have shipped, both corrupted real databases, and both are now explicitly prevented (see WAL invariants and the `OF` trigger rule above).
6. **New columns go in `SCHEMA_SQL`, not in a migration.** The reconciler adds them. Only row-transforming migrations get a `schema_version` gate, and indexes on new columns go in `DEFERRED_INDEX_SQL`.

## Diagnostics

`get_last_init_error()` returns the cause of the most recent `SessionDB.__init__` failure so `/resume` can say *why* the store is unavailable instead of a bare "Session database not available"; it is deliberately not cleared on a later success, because a concurrent successful open in a per-request `SessionDB()` caller would erase the cause another thread is about to format. `classify_persistence_error()` buckets an exception for user-facing reporting; `is_disk_full_error()`, `is_malformed_db_error()`, `is_zeroed_state_db()` are the classifiers. `collect_state_db_stats()` reports sizes and row counts; `count_db_holders()` counts processes holding the file open by scanning `/proc/*/fd` (Linux only, a lower bound, no `lsof` dependency) — the number to check before a VACUUM or an exclusive repair.
