# Module: `agent core loop`

This is the thing that actually talks to a model and executes what it asks for. Remove it and every Hermes surface — CLI, TUI, gateway, desktop, cron, kanban workers, batch runner — still parses input and renders output, but no turn ever reaches a provider, no tool ever runs, and no transcript is ever written.

## Responsibilities

- Drive one user turn to completion: build the request, call the provider, execute returned tool calls, append results, and loop until the model emits a text response or a budget/interrupt/guardrail ends it.
- Own the `AIAgent` attribute surface (~60 constructor params) and every piece of per-session state the loop reads: cached system prompt, tool snapshot, iteration budget, interrupt flags, activity stamps.
- Keep the per-conversation prompt-cache prefix byte-stable — build the system prompt once per session, decorate cache breakpoints per destination, and refuse mid-conversation rebuilds except through compaction.
- Enforce provider message-shape contracts: role alternation repair, orphan tool-result dropping, surrogate/non-ASCII sanitization, tool-call-id uniquification, interrupted-tool-sequence closure.
- Classify every provider failure into a `FailoverReason` and pick the recovery: backoff, credential-pool rotation, per-provider OAuth refresh, request rebuild, or fallback-model activation.
- Plan and run tool batches — parallel-safe segments concurrently, side-effecting calls sequentially in emission order — under guardrails that halt runaway loops.
- Bound context: preflight and post-response compaction checks, lossy summarization via the pluggable `ContextEngine`, session rotation in SQLite.
- Share one iteration budget across a parent and its delegate children, and refund `execute_code` turns so programmatic tool-calling is free.
- Persist the transcript incrementally while never committing ephemeral recovery scaffolding.

## Key Files

- [`run_agent.py`](../../../run_agent.py) — `AIAgent` class; thin forwarders to the extracted `agent/` modules, plus interrupt/steer/redirect, session flush, client lifecycle.
- [`agent/conversation_loop.py`](../../../agent/conversation_loop.py) — `run_conversation()`: the turn prologue, the tool-calling `while` loop, the inner retry loop, all recovery branches.
- [`agent/agent_init.py`](../../../agent/agent_init.py) — `init_agent(agent, ...)`, the ~1,400-line body behind `AIAgent.__init__`.
- [`agent/agent_runtime_helpers.py`](../../../agent/agent_runtime_helpers.py) — `repair_message_sequence`, `restore_primary_runtime`, `recover_with_credential_pool`, `anthropic_prompt_cache_policy`, `switch_model`, `invoke_tool`.
- [`agent/turn_context.py`](../../../agent/turn_context.py) — `TurnContext` dataclass + `build_turn_context()` (once-per-turn prologue).
- [`agent/turn_finalizer.py`](../../../agent/turn_finalizer.py) — `finalize_turn()`: post-loop budget fallback, completion verdict, result dict.
- [`agent/turn_retry_state.py`](../../../agent/turn_retry_state.py) — `TurnRetryState`, the one-shot recovery guards for a single API attempt.
- [`agent/turn_summary.py`](../../../agent/turn_summary.py) — `TurnSummaryCollector` / `format_turn_summary` / `format_token_flow` (consumed by the CLI, not the loop).
- [`agent/iteration_budget.py`](../../../agent/iteration_budget.py) — `IterationBudget`, the lock-guarded consume/refund counter.
- [`agent/system_prompt.py`](../../../agent/system_prompt.py) — `build_system_prompt_parts` / `build_system_prompt` / `invalidate_system_prompt` / `reconstruct_static_prefix`.
- [`agent/prompt_builder.py`](../../../agent/prompt_builder.py) — prompt layers: `DEFAULT_AGENT_IDENTITY`, `load_soul_md`, `build_context_files_prompt`, `build_skills_system_prompt`, `build_environment_hints`, `format_steer_marker`.
- [`agent/prompt_caching.py`](../../../agent/prompt_caching.py) — `build_prompt_cache_plan`, `strip_anthropic_cache_control`, `effective_cache_ttl`.
- [`agent/prompt_cache_boundary.py`](../../../agent/prompt_cache_boundary.py) — builder-declared stable-prefix registry for skill/webhook/cron messages.
- [`agent/prompt_cache_scope.py`](../../../agent/prompt_cache_scope.py) — `resolve_prompt_cache_scope()`, the rotation-stable cache bucket.
- [`agent/context_engine.py`](../../../agent/context_engine.py) — `ContextEngine` ABC (the pluggable compaction seam).
- [`agent/context_compressor.py`](../../../agent/context_compressor.py) — `ContextCompressor`, the default lossy-summarization engine.
- [`agent/conversation_compression.py`](../../../agent/conversation_compression.py) — `compress_context()`, `CompressionCommitFence`, `recover_rotated_compression_session`, `compression_skipped_due_to_lock`.
- [`agent/native_compaction.py`](../../../agent/native_compaction.py) — provider-side compaction (`native_compaction_context_management`, `prune_pre_checkpoint_items`).
- [`agent/context_breakdown.py`](../../../agent/context_breakdown.py) — `/usage` token accounting (`compute_context_details`).
- [`agent/error_classifier.py`](../../../agent/error_classifier.py) — `classify_api_error()` → `ClassifiedError` + `FailoverReason`.
- [`agent/retry_utils.py`](../../../agent/retry_utils.py) — `jittered_backoff`, `adaptive_rate_limit_backoff`, `parse_retry_after_seconds`.
- [`agent/repetition_guard.py`](../../../agent/repetition_guard.py) — `is_repetition_dominated()` for truncated continuations.
- [`agent/empty_response_guard.py`](../../../agent/empty_response_guard.py) — cost-bounded empty-completion retries.
- [`agent/message_sanitization.py`](../../../agent/message_sanitization.py) — surrogate/ASCII repair, `close_interrupted_tool_sequence`, `uniquify_tool_call_ids`.
- [`agent/message_content.py`](../../../agent/message_content.py) / [`agent/message_metadata.py`](../../../agent/message_metadata.py) — `flatten_message_text`, `append_message`, `stamp_message_timestamp`.
- [`agent/tool_executor.py`](../../../agent/tool_executor.py) — `execute_tool_calls_concurrent`, `execute_tool_calls_sequential`, `execute_tool_calls_segmented`.
- [`agent/tool_dispatch_helpers.py`](../../../agent/tool_dispatch_helpers.py) — `_plan_tool_batch_segments`, `make_tool_result_message`, untrusted-output wrapping.
- [`agent/tool_guardrails.py`](../../../agent/tool_guardrails.py) — `ToolCallGuardrailController`, `ToolGuardrailDecision`, `toolguard_synthetic_result`.
- [`agent/tool_result_classification.py`](../../../agent/tool_result_classification.py) — `tool_may_have_side_effect`, `file_mutation_result_landed`.
- [`agent/deadline.py`](../../../agent/deadline.py) — `resolve_timeout`, `run_bounded_sync`, `DeadlineExpired`.
- [`agent/interrupt_compat.py`](../../../agent/interrupt_compat.py) — `request_hard_interrupt`.
- [`agent/think_scrubber.py`](../../../agent/think_scrubber.py) — `StreamingThinkScrubber`, delta-safe `<|im_start|>` removal.
- [`agent/subagent_lifecycle.py`](../../../agent/subagent_lifecycle.py) — `SubagentLifecycleService`, `bind_subagent_parent`, `get_active_subagent_parent`.
- [`agent/delegation_context.py`](../../../agent/delegation_context.py) — `delegated_child_context` / `is_delegated_child_context` contextvars.
- [`agent/chat_completion_helpers.py`](../../../agent/chat_completion_helpers.py) — `handle_max_iterations()` and the request-shape helpers the loop leans on.

## Public API

`AIAgent` is the only entry point other modules use. Every frontend constructs it lazily inside a function (`from run_agent import AIAgent`) so importing `run_agent` stays off the startup path.

```python
AIAgent(base_url=None, api_key=None, provider=None, api_mode=None, model="",
        max_iterations=90, enabled_toolsets=None, disabled_toolsets=None,
        quiet_mode=False, save_trajectories=False, platform=None, session_id=None,
        skip_context_files=False, skip_memory=False, session_db=None,
        parent_session_id=None, iteration_budget=None, fallback_model=None,
        credential_pool=None, prefill_messages=None, checkpoints_enabled=False, ...)
```

The ~20 `*_callback` parameters (`stream_delta_callback`, `tool_progress_callback`, `clarify_callback`, `step_callback`, `notice_callback`, `event_callback`, …) are the platform seam: CLI, TUI, and gateway each pass their own set.

```python
agent.chat(message, stream_callback=None) -> str          # returns result["final_response"]
agent.run_conversation(user_message, system_message=None, conversation_history=None,
                       task_id=None, stream_callback=None, persist_user_message=None,
                       persist_user_timestamp=None, persist_user_display_kind=None,
                       persist_user_display_metadata=None, moa_config=None) -> dict
agent.interrupt(message=None, *, hard_cancel=False) / hard_interrupt(message=None)
agent.clear_interrupt(*, preserve_redirect=False) -> bool
agent.steer(text) -> bool / agent.redirect(text) -> bool
agent.switch_model(new_model, new_provider, api_key="", base_url="", api_mode="")
agent.reset_session_state(...) / agent.close() / agent.release_clients()
agent.get_activity_summary() / get_rate_limit_state() / commit_memory_session(...)
```

`run_conversation` returns a dict whose `final_response` and `messages` keys every caller consumes. Loop-internal module-level API used across the tree: `agent.conversation_loop.run_conversation(agent, ...)`, `agent.turn_context.build_turn_context(...)`, `agent.turn_finalizer.finalize_turn(agent, ...)`, `agent.iteration_budget.IterationBudget(max_total)`, `agent.system_prompt.build_system_prompt(agent, system_message=None)`, `agent.error_classifier.classify_api_error(...)`, `agent.tool_executor.execute_tool_calls_*(agent, ...)`.

## Internal Structure

`run_agent.py` is a facade. `AIAgent.__init__` forwards to `init_agent(self, ...)`; `run_conversation` forwards to `agent.conversation_loop.run_conversation(self, ...)` after binding relay/observability context; `_compress_context`, `_execute_tool_calls`, `_handle_max_iterations`, `_invalidate_system_prompt`, `_build_system_prompt` are the same pattern. Extracted modules reach back through `_ra()` (`import run_agent` at call time) so tests that patch `run_agent.handle_function_call` / `run_agent._set_interrupt` / `run_agent.OpenAI` still land.

All loop state lives as attributes on the `AIAgent` instance; the extracted functions take `agent` as their first positional argument and read it by attribute lookup. The whole loop is synchronous — `asyncio` appears only in `agent/deadline.py` and in platform adapters above this layer. One `AIAgent` serves one conversation; the gateway caches agents across turns, so per-turn flags (`_last_compaction_in_place`, `_last_compression_attempt_recorded`) are reset at the top of `run_conversation`.

The turn is three phases: `build_turn_context()` (stdio guard, rotated-session recovery, primary-runtime restore, auxiliary `set_runtime_main` binding, between-turns MCP tool refresh, system-prompt restore-or-build, preflight compaction estimate) → the `while` loop → `finalize_turn()`. The SessionDB row is deliberately created in `run_conversation` *after* the prompt is restored, not inside the prologue — creating it earlier writes `system_prompt=NULL` and costs the first turn its prefix-cache hit.

```python
while (api_call_count < agent.max_iterations and agent.iteration_budget.remaining > 0) \
        or agent._budget_grace_call:
    _apply_active_turn_redirect(...)          # drain a pending /redirect
    if agent._interrupt_requested: break      # "interrupted_by_user"
    api_call_count += 1
    if agent._budget_grace_call: agent._budget_grace_call = False
    elif not agent.iteration_budget.consume(): break   # "budget_exhausted"
    ... build api_messages → decorate cache → inner retry loop → assistant message ...
    if assistant_message.tool_calls: agent._execute_tool_calls(...)   # appends tool rows
    else: return text response
```

Inside the loop, one API attempt runs under `while retry_count < max_retries` with a fresh `TurnRetryState()` per outer iteration; each recovery branch (per-provider OAuth refresh, thinking-signature strip, image shrink, invalid-encrypted-content, llama.cpp grammar, 429 pool retry, long-context restart, length continuation, redirect restart) is a one-shot boolean, and the `restart_with_*` signals tell the loop to rebuild the request and re-issue the same logical iteration.

## Dependencies

- **Used by:** [cli.py](../../../cli.py) (`from run_agent import AIAgent`), [gateway/run.py](../../../gateway/run.py), [gateway/slash_commands.py](../../../gateway/slash_commands.py), [gateway/platforms/api_server.py](../../../gateway/platforms/api_server.py), [tui_gateway/server.py](../../../tui_gateway/server.py), [acp_adapter/session.py](../../../acp_adapter/session.py), [cron/scheduler.py](../../../cron/scheduler.py), [batch_runner.py](../../../batch_runner.py), [tools/delegate_tool.py](../../../tools/delegate_tool.py), [hermes_cli/oneshot.py](../../../hermes_cli/oneshot.py), [agent/background_review.py](../../../agent/background_review.py), [agent/curator.py](../../../agent/curator.py).
- **Uses:** [model_tools.py](../../../model_tools.py) (`get_tool_definitions`, `handle_function_call`, `check_toolset_requirements` — importing it is what triggers tool discovery), [tools/terminal_tool.py](../../../tools/terminal_tool.py) (`cleanup_vm`, `get_active_env`), [tools/interrupt.py](../../../tools/interrupt.py) (`set_interrupt`), [tools/browser_tool.py](../../../tools/browser_tool.py) (`cleanup_browser`), [hermes_constants.py](../../../hermes_constants.py) (`get_hermes_home`), [hermes_logging.py](../../../hermes_logging.py) (`set_session_context`), [hermes_cli/env_loader.py](../../../hermes_cli/env_loader.py), [agent/process_bootstrap.py](../../../agent/process_bootstrap.py) (lazy `OpenAI` proxy, `_install_safe_stdio`), [agent/model_metadata.py](../../../agent/model_metadata.py) (token estimation), [agent/usage_pricing.py](../../../agent/usage_pricing.py), [agent/redact.py](../../../agent/redact.py), [agent/trajectory.py](../../../agent/trajectory.py), [agent/display.py](../../../agent/display.py) (`KawaiiSpinner`). External: the `openai` SDK (deferred), `requests`/`httpx` via the client builders, `rich`/`prompt_toolkit` only above this layer.
- **Adjacent, owned elsewhere:** [agent/curator.py](../../../agent/curator.py) (skills page), [agent/memory_manager.py](../../../agent/memory_manager.py) (plugins page), [agent/auxiliary_client.py](../../../agent/auxiliary_client.py) (providers page).

## Notable Patterns / Gotchas

- **The system prompt is built once per session.** `build_system_prompt` caches on `agent._cached_system_prompt` and the only in-loop writer of `None` to it is `invalidate_system_prompt`, reached from `agent/conversation_compression.py` (plus the CLI's manual `/compress`). Layers are ordered stable → context → volatile precisely so a rebuild keeps the unchanged head inside the reused prefix. Anything that recomputes prompt inputs mid-turn is a cache bug, not a feature.
- **Compaction is the only sanctioned mid-conversation context mutation.** The pre-API steer drain proves the rule: it appends the steer marker to the last `tool` message, and when no tool message exists it *re-queues* the steer rather than injecting into a user message, because a synthetic user row mid-loop breaks alternation. Same reason `_EPHEMERAL_SCAFFOLDING_FLAGS` + `_is_ephemeral_scaffolding` keep `(empty)`/nudge/verification rows out of the durable transcript.
- **`_budget_grace_call` has no writer.** It is initialised `False` in `agent_init.py` and only read/cleared in the loop condition and body; nothing in the tree ever sets it `True` (same for `_budget_exhausted_injected`). The real one-extra-call behaviour lives in `finalize_turn`: on a clean budget exhaustion it calls `_handle_max_iterations`, which appends `MAX_ITERATIONS_SUMMARY_REQUEST` and makes a single toolless request. Do not read the loop condition as evidence that a grace turn happens.
- **`max_iterations` has two different defaults depending on entry point.** The `AIAgent`/`init_agent` signature says `90`; the gateway passes its own resolved value from `agent.max_turns` bridged through `HERMES_MAX_ITERATIONS` (default `500`), and `agent/iteration_budget.py`'s docstring documents that 500. Subagents get an independent budget capped at `delegation.max_iterations`, so parent+children can collectively exceed the parent's cap. `execute_code` turns are refunded via `IterationBudget.refund()`.
- **Cache decoration is per-attempt, not per-turn.** `_redecorate_prompt_cache_for_provider` strips and re-applies `cache_control` at the top of every retry attempt because `try_activate_fallback` changes the destination mid-turn and nine failover `continue` paths reuse the same `api_messages`. `reconstruct_static_prefix` is deliberately fail-closed: the rebuilt static tier is used only when the stored prompt literally starts with it, otherwise the request falls back to the legacy layout with the stored bytes untouched.
- **`resolve_prompt_cache_scope` and `_conversation_root_id` are intentionally different walks.** The cache scope follows the compression *lineage* root (rotation-stable, memoized per segment) while Portal attribution follows `parent_session_id` blindly and collapses `/branch` children and delegate trees. Never "deduplicate" them — collapsing forks would break the sibling/subagent isolation the cache key exists to provide.
- **Tool batches are segmented, not uniformly parallel.** `_plan_tool_batch_segments` splits into maximal contiguous runs of parallel-safe calls (read-only, non-overlapping file targets, opted-in MCP) separated by sequential barriers; mixed batches run segment-by-segment in emission order. `tool_may_have_side_effect` treats unknown/plugin/MCP tools as effect-capable by default, so a new tool is sequential until proven otherwise.
