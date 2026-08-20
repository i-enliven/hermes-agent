# Hermes-Agent Fork Refactor — Work-In-Progress TODO

This file documents the current state of the fork refactor so work can resume after
a session reset. It is **not** a spec — it is a handoff snapshot. Read it first,
then re-verify state with git before acting (line numbers shift as edits land).

## Goal

Refactor this fork of NousResearch hermes-agent (`/home/ienliven/Projects/hermes-agent`,
branch `main`) into a lean, approval-friendly CLI+TUI-only codebase:

- DELETE the desktop app, web dashboard, website, and their tests.
- REMOVE all third-party chat-messenger backends (telegram, discord, slack, whatsapp,
  signal, matrix, mattermost, dingtalk, feishu, wecom, weixin, qqbot, yuanbao, line,
  irc, ntfy, raft, a2a, buzz, photon, google_chat, homeassistant, simplex, sms).
- KEEP the gateway/messaging framework + in-house infrastructure (api_server, webhook,
  msgraph_webhook, email, relay, wecom_callback) and Teams (kept for later work).
- CLI+TUI must keep launching and importing cleanly throughout.

## Environment / Constraints

- Repo venv: `.venv/bin/python` (Python 3.11.15). NEVER use system python (3.14 — wrong).
- LLM runs locally at ~15 tps. **Time is NOT a constraint.** Be patient, thorough,
  and deliberate. Prefer many small verified steps over big blind ones.
- Keep gateway/messaging functionality; remove ONLY third-party chat-messenger backends.
- Fork must stay approval-friendly — prune dead references, not just delete files.
- Use `grep -n "^" file` to see line numbers; `sed 'N,Md' file` to delete a line range;
  `git rm` for full-file deletions (reversible). Clear stale `.git/index.lock` first
  (`rm -f .git/index.lock`) if a git command fails — it recurs after interrupted git.
- Do NOT commit unless asked.

## Platform Enum (authoritative)

`gateway/config.py` `Platform` enum now has ONLY: `LOCAL, EMAIL, API_SERVER, WEBHOOK,
MSGGRAPH_WEBHOOK, RELAY`. `TEAMS` is a **dynamic plugin member** — reference it as
`Platform("teams")`, NEVER `Platform.TEAMS` (attribute raises AttributeError unless the
member was already created). Removed platforms fail as `Platform.X`:
TELEGRAM, DISCORD, SLACK, WHATSAPP, WHATSAPP_CLOUD, SIGNAL, MATRIX, MATTERMOST, DINGTALK,
FEISHU, WECOM, WECOM_CALLBACK, WEIXIN, BLUEBUBBLES, QQBOT, YUANBAO, LINE, IRC, NTFY, RAFT,
A2A, BUZZ, PHOTON, GOOGLE_CHAT, HOMEASSISTANT, SIMPLEX, SMS.

## DONE (verified)

1. Deleted surfaces via `git rm -r`: apps/desktop, apps/bootstrap-installer, apps/shared,
   web, website, tests/dashboard, tests/website, tests-js (1596 desktop files tracked).
2. Deleted ~21 messenger platform plugin dirs (plugins/platforms/...): telegram, discord,
   slack, whatsapp, signal, matrix, line, dingtalk, feishu, wecom, mattermost, simplex,
   buzz, photon, google_chat, homeassistant, irc, ntfy, raft, a2a, sms.
3. Pruned `gateway/config.py` Platform enum + env maps + validation + enable-from-env.
4. Pruned `gateway/run.py` `_create_adapter` + env maps + dead branches.
5. Pruned `gateway/session.py` + `delivery.py` removed-platform refs.
6. Trimmed package.json / pyproject.toml / Dockerfile / .dockerignore.
7. Pruned CLI command surface (whatsapp/slack/dashboard/gui removed from `--help`).
8. **ALL shipped-code `Platform.<REMOVED>` refs eliminated** — grep for
   `Platform\.(TELEGRAM|DISCORD|SLACK|WHATSAPP|WHATSAPP_CLOUD|SIGNAL|MATRIX|MATTERMOST|DINGTALK|FEISHU|WECOM|WECOM_CALLBACK|WEIXIN|BLUEBUBBLES|QQBOT|YUANBAO|LINE|IRC|NTFY|RAFT|A2A|BUZZ|PHOTON|GOOGLE_CHAT|HOMEASSISTANT|SIMPLEX|SMS)`
   in `gateway/ tools/ hermes_cli/ cron/ plugins/ tui_gateway/` (excluding tests/comments)
   returns **ZERO matches**.
9. Fixed real pre-existing fork bug: `build_session_key` referenced undefined
   `slack_scope_id` — removed.
10. Fixed a regression I introduced: `gateway/session.py` group-path referenced undefined
    `chat_type_slot`; restored the retained thread-slot logic (`effective_thread_id`,
    `chat_type_slot`) while keeping slack_scope_id removed. **Keep this pattern — do not
    re-remove the restored lines.**
11. CLI (`./hermes --help`, `--version`), TUI (`import tui_gateway.server, entry`), and all
    gateway core modules (`gateway.config/session/delivery/run/relay.adapter/relay.ws_transport/platforms.base/channel_directory/authz_mixin/slash_commands/stream_consumer/platform_registry` + `tools.send_message_tool`)
    import cleanly. `import gateway.run` → RUN OK.
12. Fixed removed-platform tests in `tests/gateway/relay/` — **all 113 relay tests pass**:
    pruned Discord/Slack-specific cases (relay_threads auto-thread renames,
    relay_passthrough discord interactions, relay_adapter stop_typing/send_typing,
    relay_interactive discord component, relay_per_platform_caps discord/telegram caps,
    relay_multiplatform discord/telegram stamping). Updated `test_handoff_relay_aliasing`
    to use `Platform("teams")` instead of discord. Fixed `test_platform_registry.py`
    (27 pass) — pruned removed lazy-installable platforms from `TestMigratedPlatformWiring`,
    updated enum-dynamic tests to WEBHOOK/teams/registered-plugin instead of telegram/irc.
13. `tests/gateway/test_resume_command.py` (22 pass), `test_post_delivery_callback_chaining.py`
    (changed `Platform.TELEGRAM`→`Platform.EMAIL`), `test_media_cache.py` (pruned
    bluebubbles/whatsapp_cloud/signal MIME classes via sed 76-163d — kept generic classes).

## IN PROGRESS / REMAINING (updated 2026-08-16, after full per-file sweep)

**Full per-file sweep (240s timeout per file, hang-safe) over tests/gateway/ + tests/cron/:
473 test files → 404 OK, 67 FAIL, 1 TIMEOUT (test_completion_delivery.py), 1 ERR
(test_plugin_platform_interface.py). 283 failing test nodes in /tmp/sweep_failed.txt.
Raw sweep output: /tmp/sweep_done.txt (status per file), classification: /tmp/wave2_rows.txt.**

**Known hang (root-caused): test_completion_delivery.py::test_concurrent_claims_share_the_same_narrow_delivery_seam**
— event session_key 'agent:main:telegram:dm:12345:678' is unroutable post-fork
(_build_process_event_source → None, "invalid platform metadata: 'telegram'"), so the
adapter is never called, entered.wait() never resolves, and the whole pytest process
stalls at ~21% of the suite. A FULL-suite pytest run is therefore unusable until fixed;
use per-file `timeout 240 .venv/bin/python -m pytest <file> -q --tb=line -rf` runs.
Fix = re-path fixture session keys to a retained platform (email).

**Delegation strategy (working well): waves of 6 subagents, each owning an exclusive
file list (~2-6 files each). Brief: /tmp/delegate_brief.md. Wave 1 (11 files) + Wave 2
(58 files) completed 2026-08-17 morning: ~39 files fixed, 4 files git-rm'd
(test_ws_auth_retry.py, test_text_batching.py, test_restart_redelivery_dedup.py,
test_shared_group_sender_prefix.py — all purely removed-platform tests).
One child hit its iteration cap with 4/7 files left (diagnosis only) — those files
re-entered wave 3.

**WAVE 3 (deleg_84e45b7e, 2026-08-17, 6 agents / 27 files) — COMPLETE.**
All 27 previously-failing files resolved:
- Tasks 0,2,4,5 + parent-verified: 15 files green (see /tmp/wave3_verify_status.txt, all PASS).
- Task-1 hit its iteration cap leaving 3 files red → re-dispatched as wave-3b
  (deleg_996776c1) with a fully root-caused brief (/tmp/wave3b_brief.md):
  - test_gateway_shutdown.py: KeyError 'telegram_reply_to_message_id' — fork's
    _thread_metadata_for_target only returns {'thread_id':...} for non-telegram DMs
    (_is_telegram_dm_topic_target stubbed to False). Re-pathed test off telegram
    source; now 9/9.
  - test_profile_resolution.py: route-matching requires route.platform == source
    platform (ProfileRoute.matches, gateway/profile_routing.py:98). Re-pathed
    adapter + routes to retained Platform.LOCAL (test is named test_local_adapter_*);
    13/13.
  - test_unauthorized_dm_behavior.py: telegram/whatsapp/simplex removed-platform cases
    pruned; re-pathed to retained LOCAL (the pair-default chat platform). 9/9.
- 3 stragglers verified together: 31 passed, 0 failed.
- 8 test files git-rm'd total (all verified purely removed-platform):
  test_ws_auth_retry.py, test_text_batching.py, test_restart_redelivery_dedup.py,
  test_shared_group_sender_prefix.py (waves 1+2); test_aiohttp_body_caps.py,
  test_handoff_thread_session_key.py, test_own_policy_startup_gate.py,
  test_platform_http_client_limits.py (wave 3).

**SHIPPED-CODE FIXES (verified):**
- gateway/config.py weak-credential guard: PLATFORM_TOKEN_ENV_NAMES pruned to {}
  during fork cleanup killed the guard (env lookup always fell through). Fix:
  `env_name = _token_env_names.get(platform) or platform.value`. 57/57 pass with
  test_weak_credential_guard.py + test_config.py.
- gateway/relay/adapter.py: stale discord comment → email.
- hermes_cli/main.py: cleanup stripped 5 dispatcher functions (cmd_kanban,
  cmd_project, cmd_hooks, cmd_doctor, cmd_verify) while leaving their
  set_defaults(func=...) registrations → `hermes --help` crashed with
  NameError: cmd_kanban. Restored all 5 from origin/main (thin wrappers over
  existing hermes_cli modules). `./hermes --help` now exits 0; all set_defaults
  func= references resolve. (cmd_slack/cmd_whatsapp/cmd_whatsapp_cloud removed
  intentionally with their parsers — correct.)

**FINAL STATE (2026-08-18): all tests/gateway/ + tests/cron/ green — 0 failures.**
Full-suite run completed (was previously unusable due to the completion_delivery
hang; the re-pathed fixture unblocked it). Ready to commit + push to FORK.

### Approach (user-approved: thorough — prune removed-platform-specific cases,
update generic-feature stand-ins to retained platforms)

For each failing test file:
- **DELETE entirely-removed-platform test files** via `git rm` (e.g. `test_voice_command.py`
  already deleted — Discord voice; `test_bluebubbles.py` — removed platform; files that
  `import gateway.platforms.<removed>` / `plugins.platforms.<removed>`).
- **PRUNE removed-platform-specific test cases** inside generic-feature files (e.g. in
  `tests/cron/test_scheduler.py`: `TestResolveDeliveryTarget` telegram/discord/whatsapp
  delivery-target tests, `TestRoutingIntents` telegram/discord/slack, etc.).
- **UPDATE generic-feature stand-ins**: replace removed `Platform.X` → `Platform.EMAIL` /
  `Platform.WEBHOOK` / `Platform("teams")` and remove removed-platform imports/env vars.
- **CAREFUL**: a file that imports a deleted module for ONE test may test retained features
  in others — do NOT blind-delete (e.g. `test_config.py`, `test_webhook_adapter.py`,
  `test_ws_auth_retry.py` are retained-feature tests; only prune their removed cases).
- The bulk `Platform.X`→`Platform.EMAIL` substitution (already applied to ~159 files)
  CORRUPTED some removed-platform-specific assertions (e.g. `test_platform_registry.py`
  `Platform.EMAIL.value == "telegram"`) — fix those individually, don't re-substitute.

### Known failing files to work next (line numbers shift; re-grep before acting)
- `tests/cron/test_scheduler.py` — telegram/discord/slack/whatsapp delivery-target cases
  in `TestResolveDeliveryTarget` (165-277), `TestRoutingIntents` (~296), `TestDeliverResultWrapping` (~332, ~389/412), `TestDeliverResultErrorReturns` (~493), `TestRunJobSessionPersistence`, `TestSilentDelivery` (~1008), etc.
- `tests/gateway/` — many files: test_session.py (SlackWorkspaceSessionKeys), test_delivery.py,
  test_media_cache.py (done), test_config.py (removed-platform cases), test_adapter_connect_classification,
  test_multiplex_*, test_completion_delivery, test_bluebubbles (delete), test_tts_media_routing,
  test_pairing_allowlist_bypass, test_kanban_notifier, test_send_retry, test_restart_notification, etc.
- `tests/cron/` — test_scheduler.py, test_relay_fronted_delivery.py, others.
- `tests/gateway/relay/` — DONE (113 pass).

### Helper files on disk (may be stale after edits — regenerate)
- `/tmp/failfiles.txt` — last captured failing test paths (format may drift; re-run pytest).
- `/tmp/batches/batch_*.txt` — old delegation batches (stale, ignore).
- `/tmp/upd_cands.txt`, `/tmp/del_cands.txt` — old classifications (stale, ignore).

## NOT YET DECIDED / OPTIONAL

- `hermes_cli/web_server.py` (18325 lines) + `web_models.py` + `dashboard_procs.py` +
  `dashboard_register.py` + `subcommands/dashboard.py` — the web-dashboard surface.
  Desktop app + web dashboard are removed; this surface serves them. Only referenced by
  hermes_cli itself (main.py lazy imports `start_server`/`should_require_auth` at
  ~10252/~10787, update_cmd.py startup list, web_models.py re-exports). Retained gateway/
  cron/tools/tui code references it only in comments. Removing it entirely is a large,
  risky deletion (18325-line file + the dashboard subcommands) — flag as optional deep
  prune; do it only if the user confirms.
- `gateway/slash_commands.py` `/voice` command (retained code) is now dead (Discord removed) —
  could prune the voice slash-command handler, but it's not failing tests; leave unless asked.
- `gateway/run.py` still has a few removed-platform comment references (e.g. line 84
  "Telegram cold polling") — harmless comments; update only if doing a final comment sweep.

## RESUME PROMPT (for the next agent session)

> You are resuming a refactor of the hermes-agent fork at /home/ienliven/Projects/hermes-agent
> (branch main). Read TODO.md first — it is the authoritative handoff snapshot. Use the repo
> venv `.venv/bin/python` (Python 3.11.15); never system python. The LLM runs locally at ~15
> tps — time is NOT a constraint, so be thorough and verify every step.
>
> CRITICAL CONTEXT-PRESERVATION + DELEGATION INSTRUCTIONS:
> - You will run with a 131k context window and delegation enabled. CONSERVE YOUR OWN CONTEXT
>   aggressively: do not dump large files or long grep outputs into the main context. Use
>   `execute_code` to filter/reduce large outputs (counts, dedupes, summaries) before they
>   reach you. Keep your working state in short notes, not in the transcript.
> - The remaining work is MECHANICAL and VOLUMINOUS: pruning removed-platform-specific test
>   cases (~180 files, ~700-800 cases) in tests/gateway/ and tests/cron/. DELEGATE VERY SMALL
>   TASKS to subagents to keep your own context small: give each subagent ONE directory or a
>   small batch of files (5-15 files max) with the exact pattern from TODO.md (prune removed-
>   platform-specific cases, update generic stand-ins to Platform.EMAIL/WEBHOOK/Platform("teams"),
>   git rm entirely-removed files). Ask each subagent to VERIFY with `.venv/bin/python -m pytest`
>   on its files and report only file-by-file pass/delete summaries. Do NOT hand a subagent a
>   whole directory with hundreds of files — keep tasks tiny and verifiable.
> - After each batch of fixes, verify: `import gateway.run, gateway.session, gateway.relay.adapter,
>   tools.send_message_tool` exits 0, and the affected test files pass.
> - When the gateway+cron test suite is clean, do a final full verify (CLI `./hermes --help`,
>   TUI import, gateway imports, full `pytest tests/gateway/ tests/cron/`) and update TODO.md to
>   mark completion. Consider the optional web_server.py deep-prune only if the user confirms.
> - Do NOT commit. Keep TODO.md updated as the single source of truth for resumption.

## Verification commands (run after finishing)

```
.venv/bin/python -c "import gateway.config, gateway.session, gateway.delivery, gateway.run, gateway.relay.adapter, gateway.relay.ws_transport, gateway.platforms.base, gateway.channel_directory, gateway.authz_mixin, gateway.slash_commands, gateway.stream_consumer, gateway.platform_registry, tools.send_message_tool; print('ALL OK')"
timeout 30 ./hermes --help
.venv/bin/python -c "import tui_gateway.server, tui_gateway.entry; print('TUI OK')"
.venv/bin/python -m pytest tests/gateway/ tests/cron/ -q   # target: 0 failures (removed-platform cases pruned)
```
