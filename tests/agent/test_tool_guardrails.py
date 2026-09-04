"""Pure tool-call guardrail primitive tests."""

import json

from agent.tool_guardrails import (
    ToolCallGuardrailConfig,
    ToolCallGuardrailController,
    ToolCallSignature,
    canonical_tool_args,
    classify_tool_failure,
)


def test_tool_call_signature_hashes_canonical_nested_unicode_args_without_exposing_raw_args():
    args_a = {
        "z": [{"β": "☤", "a": 1}],
        "a": {"y": 2, "x": "secret-token-value"},
    }
    args_b = {
        "a": {"x": "secret-token-value", "y": 2},
        "z": [{"a": 1, "β": "☤"}],
    }

    assert canonical_tool_args(args_a) == canonical_tool_args(args_b)
    sig_a = ToolCallSignature.from_call("web_search", args_a)
    sig_b = ToolCallSignature.from_call("web_search", args_b)

    assert sig_a == sig_b
    assert len(sig_a.args_hash) == 64
    metadata = sig_a.to_metadata()
    assert metadata == {"tool_name": "web_search", "args_hash": sig_a.args_hash}
    assert "secret-token-value" not in json.dumps(metadata)
    assert "☤" not in json.dumps(metadata)




def test_config_parses_nested_warn_and_hard_stop_thresholds():
    cfg = ToolCallGuardrailConfig.from_mapping(
        {
            "warnings_enabled": False,
            "hard_stop_enabled": True,
            "warn_after": {
                "exact_failure": 3,
                "same_tool_failure": 4,
                "idempotent_no_progress": 5,
            },
            "hard_stop_after": {
                "exact_failure": 6,
                "same_tool_failure": 7,
                "idempotent_no_progress": 8,
            },
        }
    )

    assert cfg.warnings_enabled is False
    assert cfg.hard_stop_enabled is True
    assert cfg.exact_failure_warn_after == 3
    assert cfg.same_tool_failure_warn_after == 4
    assert cfg.no_progress_warn_after == 5
    assert cfg.exact_failure_block_after == 6
    assert cfg.same_tool_failure_halt_after == 7
    assert cfg.no_progress_block_after == 8


def test_default_repeated_identical_failed_call_warns_without_blocking():
    controller = ToolCallGuardrailController()
    args = {"query": "same"}

    decisions = []
    for _ in range(5):
        assert controller.before_call("web_search", args).action == "allow"
        decisions.append(
            controller.after_call("web_search", args, '{"error":"boom"}', failed=True)
        )

    assert decisions[0].action == "allow"
    assert [d.action for d in decisions[1:]] == ["warn", "warn", "warn", "warn"]
    assert {d.code for d in decisions[1:]} == {"repeated_exact_failure_warning"}
    assert controller.before_call("web_search", args).action == "allow"
    assert controller.halt_decision is None


def test_hard_stop_enabled_blocks_repeated_exact_failure_before_next_execution():
    controller = ToolCallGuardrailController(
        ToolCallGuardrailConfig(
            hard_stop_enabled=True,
            exact_failure_warn_after=2,
            exact_failure_block_after=2,
            same_tool_failure_halt_after=99,
        )
    )
    args = {"query": "same"}

    assert controller.before_call("web_search", args).action == "allow"
    first = controller.after_call("web_search", args, '{"error":"boom"}', failed=True)
    assert first.action == "allow"

    assert controller.before_call("web_search", args).action == "allow"
    second = controller.after_call("web_search", args, '{"error":"boom"}', failed=True)
    assert second.action == "warn"
    assert second.code == "repeated_exact_failure_warning"

    blocked = controller.before_call("web_search", args)
    assert blocked.action == "block"
    assert blocked.code == "repeated_exact_failure_block"
    assert blocked.count == 2














def test_mutating_or_unknown_tools_are_not_blocked_for_repeated_identical_success_output_by_default():
    controller = ToolCallGuardrailController(
        ToolCallGuardrailConfig(
            no_progress_warn_after=2,
            no_progress_block_after=2,
            sequence_repeat_warn_after=99,
        )
    )
    for _ in range(3):
        assert controller.before_call("write_file", {"path": "/tmp/x", "content": "x"}).action == "allow"
        assert controller.after_call("write_file", {"path": "/tmp/x", "content": "x"}, "ok", failed=False).action == "allow"
        assert controller.before_call("custom_tool", {"x": 1}).action == "allow"
        assert controller.after_call("custom_tool", {"x": 1}, "ok", failed=False).action == "allow"






# ── Per-turn runaway-loop caps (Claude Code v2.1.212, Week 29) ──────────────

from agent.tool_guardrails import LoopCapConfig  # noqa: E402






def test_loop_cap_zero_disables_and_junk_falls_back():
    # 0 is a legitimate "unlimited" value; negatives / junk fall back to default.
    assert LoopCapConfig.from_mapping({"max_web_searches": 0}).max_web_searches == 0
    assert LoopCapConfig.from_mapping({"max_web_searches": -5}).max_web_searches == 50
    assert LoopCapConfig.from_mapping({"max_subagents": "nope"}).max_subagents == 50


def test_web_search_cap_blocks_after_limit_regardless_of_hard_stop():
    # Loop caps fire even with hard_stop_enabled=False (the per-turn loop
    # detector's flag). Each distinct query avoids the loop detector so we know
    # the block came from the loop cap, not exact-failure repetition.
    controller = ToolCallGuardrailController(
        ToolCallGuardrailConfig(
            hard_stop_enabled=False,
            loop_caps=LoopCapConfig(max_web_searches=3),
        )
    )
    for i in range(3):
        assert controller.before_call("web_search", {"query": f"q{i}"}).action == "allow"
    decision = controller.before_call("web_search", {"query": "q4"})
    assert decision.action == "block"
    assert decision.code == "loop_web_search_cap"
    assert decision.should_halt is True


# ── Semantic paging loop tests ──────────────────────────────────────────────


def test_config_parses_semantic_paging_thresholds():
    cfg = ToolCallGuardrailConfig.from_mapping(
        {
            "warn_after": {
                "semantic_paging": 3,
            },
            "hard_stop_after": {
                "semantic_paging": 6,
            },
        }
    )
    assert cfg.paging_loop_warn_after == 3
    assert cfg.paging_loop_block_after == 6


def test_consecutive_offset_reads_trigger_paging_warning():
    controller = ToolCallGuardrailController(
        ToolCallGuardrailConfig(paging_loop_warn_after=4)
    )
    path = "gateway/run.py"
    offsets = [1, 51, 101, 151]

    for i, offset in enumerate(offsets):
        args = {"path": path, "offset": offset, "limit": 50}
        assert controller.before_call("read_file", args).action == "allow"
        decision = controller.after_call(
            "read_file", args, f"content for chunk {i}", failed=False
        )
        if i < 3:
            assert decision.action == "allow", f"Call {i+1} should be allowed"
        else:
            assert decision.action == "warn"
            assert decision.code == "semantic_paging_loop_warning"
            assert "Semantic paging loop detected" in decision.message
            assert "4 consecutive offset-increment reads" in decision.message
            assert "search_files" in decision.message


def test_interleaved_non_read_tool_resets_paging_streak():
    controller = ToolCallGuardrailController(
        ToolCallGuardrailConfig(paging_loop_warn_after=3)
    )
    path = "gateway/run.py"

    # Read 1
    controller.after_call("read_file", {"path": path, "offset": 1}, "c1", failed=False)
    # Read 2
    controller.after_call("read_file", {"path": path, "offset": 51}, "c2", failed=False)

    # Interleaved diagnostic tool call (search_files)
    controller.after_call(
        "search_files", {"path": path, "pattern": "def foo"}, "match", failed=False
    )

    # Read 3 (offset advances, but streak was reset)
    decision = controller.after_call(
        "read_file", {"path": path, "offset": 101}, "c3", failed=False
    )
    assert decision.action == "allow"

    # Read 4 (streak is now 2)
    decision = controller.after_call(
        "read_file", {"path": path, "offset": 151}, "c4", failed=False
    )
    assert decision.action == "allow"

    # Read 5 (streak is now 3 -> triggers warning)
    decision = controller.after_call(
        "read_file", {"path": path, "offset": 201}, "c5", failed=False
    )
    assert decision.action == "warn"
    assert decision.code == "semantic_paging_loop_warning"


def test_switching_target_file_resets_paging_streak():
    controller = ToolCallGuardrailController(
        ToolCallGuardrailConfig(paging_loop_warn_after=3)
    )
    # 2 reads on file A
    controller.after_call("read_file", {"path": "a.py", "offset": 1}, "c1", failed=False)
    controller.after_call("read_file", {"path": "a.py", "offset": 51}, "c2", failed=False)

    # Switch to file B
    controller.after_call("read_file", {"path": "b.py", "offset": 1}, "c3", failed=False)
    decision = controller.after_call("read_file", {"path": "b.py", "offset": 51}, "c4", failed=False)
    assert decision.action == "allow"


def test_backward_offset_jump_resets_paging_streak():
    controller = ToolCallGuardrailController(
        ToolCallGuardrailConfig(paging_loop_warn_after=3)
    )
    path = "a.py"
    controller.after_call("read_file", {"path": path, "offset": 1}, "c1", failed=False)
    controller.after_call("read_file", {"path": path, "offset": 51}, "c2", failed=False)

    # Jump backwards to offset 10 -> resets streak to 1
    decision = controller.after_call("read_file", {"path": path, "offset": 10}, "c3", failed=False)
    assert decision.action == "allow"

    # Advance from 10 to 30 -> streak is 2
    decision = controller.after_call("read_file", {"path": path, "offset": 30}, "c4", failed=False)
    assert decision.action == "allow"


def test_paging_loop_halt_and_block_when_hard_stop_enabled():
    controller = ToolCallGuardrailController(
        ToolCallGuardrailConfig(
            hard_stop_enabled=True,
            paging_loop_warn_after=2,
            paging_loop_block_after=4,
        )
    )
    path = "large.py"

    # Read 1: allow
    assert controller.before_call("read_file", {"path": path, "offset": 1}).action == "allow"
    d1 = controller.after_call("read_file", {"path": path, "offset": 1}, "c1", failed=False)
    assert d1.action == "allow"

    # Read 2: warn
    assert controller.before_call("read_file", {"path": path, "offset": 51}).action == "allow"
    d2 = controller.after_call("read_file", {"path": path, "offset": 51}, "c2", failed=False)
    assert d2.action == "warn"
    assert d2.code == "semantic_paging_loop_warning"

    # Read 3: warn
    assert controller.before_call("read_file", {"path": path, "offset": 101}).action == "allow"
    d3 = controller.after_call("read_file", {"path": path, "offset": 101}, "c3", failed=False)
    assert d3.action == "warn"

    # Read 4: halt
    assert controller.before_call("read_file", {"path": path, "offset": 151}).action == "allow"
    d4 = controller.after_call("read_file", {"path": path, "offset": 151}, "c4", failed=False)
    assert d4.action == "halt"
    assert d4.code == "semantic_paging_loop_halt"
    assert d4.should_halt is True

    # Read 5: before_call blocks execution
    d5 = controller.before_call("read_file", {"path": path, "offset": 201})
    assert d5.action == "block"
    assert d5.code == "semantic_paging_loop_block"
    assert d5.allows_execution is False
