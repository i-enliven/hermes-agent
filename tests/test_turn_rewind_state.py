import pytest
from hermes_state import SessionDB


@pytest.fixture
def session_db(tmp_path):
    db_path = tmp_path / "state.db"
    db = SessionDB(db_path)
    yield db
    db.close()


def test_list_session_steps_and_boundaries(session_db):
    session_id = "test-session-1"
    session_db.create_session(session_id=session_id, source="cli")

    # Turn 1: User asks question
    session_db.append_message(session_id, role="user", content="Where is auth defined?")

    # Turn 1, Cycle 1: Assistant calls grep
    session_db.append_message(
        session_id,
        role="assistant",
        content="",
        tool_calls=[{"id": "tc1", "function": {"name": "grep_search", "arguments": "{}"}}],
    )
    session_db.append_message(
        session_id,
        role="tool",
        tool_call_id="tc1",
        tool_name="grep_search",
        content="auth.py: line 10",
    )

    # Turn 1, Cycle 2: Assistant calls view_file
    session_db.append_message(
        session_id,
        role="assistant",
        content="",
        tool_calls=[{"id": "tc2", "function": {"name": "view_file", "arguments": "{}"}}],
    )
    session_db.append_message(
        session_id,
        role="tool",
        tool_call_id="tc2",
        tool_name="view_file",
        content="def authenticate(): ...",
    )

    # Turn 2: User follow up
    session_db.append_message(session_id, role="user", content="Can you refactor it?")

    steps = session_db.list_session_steps(session_id)
    step_ids = [s["step"] for s in steps]
    assert step_ids == ["1", "1.1", "1.2", "2"]

    # Test boundary resolution
    b1 = session_db.resolve_step_boundary(session_id, "1")
    assert b1["step"] == "1"
    assert b1["type"] == "user_turn"

    b1_1 = session_db.resolve_step_boundary(session_id, "1.1")
    assert b1_1["step"] == "1.1"
    assert b1_1["type"] == "agent_cycle"


def test_rewind_to_agent_cycle(session_db):
    session_id = "test-session-2"
    session_db.create_session(session_id=session_id, source="cli")

    # Turn 1: User prompt
    u_id = session_db.append_message(session_id, role="user", content="Find bugs in module.")

    # Cycle 1.1: grep tool
    session_db.append_message(
        session_id,
        role="assistant",
        content="",
        tool_calls=[{"id": "tc1", "function": {"name": "grep_search", "arguments": "{}"}}],
    )
    t1_id = session_db.append_message(
        session_id,
        role="tool",
        tool_call_id="tc1",
        tool_name="grep_search",
        content="matches in a.py",
    )

    # Cycle 1.2: view_file (the noisy exploration that we will rollback)
    session_db.append_message(
        session_id,
        role="assistant",
        content="",
        tool_calls=[{"id": "tc2", "function": {"name": "view_file", "arguments": "{}"}}],
    )
    session_db.append_message(
        session_id,
        role="tool",
        tool_call_id="tc2",
        tool_name="view_file",
        content="1000 lines of noise...",
    )

    # Rewind to Step 1.1 with findings
    findings = "Module a.py has syntax error on line 42."
    result = session_db.rewind_to_step(session_id, "1.1", findings=findings)

    assert result["target_step"] == "1.1"
    assert result["rewound_count"] == 2  # Assistant 1.2 and Tool 1.2 soft-deleted

    # Verify active messages: only User, Assistant 1.1, Tool 1.1 remain active
    active_msgs = session_db.get_messages(session_id, include_inactive=False)
    assert len(active_msgs) == 3
    assert active_msgs[-1]["id"] == t1_id
    assert "syntax error on line 42" in active_msgs[-1]["content"]

    # Verify inactive messages still exist on disk
    all_msgs = session_db.get_messages(session_id, include_inactive=True)
    assert len(all_msgs) == 5
    inactive_msgs = [m for m in all_msgs if not m["active"]]
    assert len(inactive_msgs) == 2


def test_rewind_to_user_turn(session_db):
    session_id = "test-session-3"
    session_db.create_session(session_id=session_id, source="cli")

    session_db.append_message(session_id, role="user", content="First question")
    session_db.append_message(session_id, role="assistant", content="First answer")

    session_db.append_message(session_id, role="user", content="Second question")
    session_db.append_message(session_id, role="assistant", content="Second answer")

    # Rewind to Turn 1 with findings
    result = session_db.rewind_to_step(session_id, "1", findings="User preferred Python 3")
    assert result["target_step"] == "1"

    active_msgs = session_db.get_messages(session_id, include_inactive=False)
    assert len(active_msgs) == 1
    assert "User preferred Python 3" in active_msgs[0]["content"]


def test_list_steps_skips_compaction_summary(session_db):
    from agent.context_compressor import SUMMARY_PREFIX
    session_id = "test-session-compaction"
    session_db.create_session(session_id=session_id, source="cli")

    session_db.append_message(session_id, role="user", content="Real Turn 1")
    session_db.append_message(session_id, role="assistant", content="Answer 1")

    # Injected compaction summary message
    summary_text = SUMMARY_PREFIX + "\nCompacted history summary text..."
    session_db.append_message(
        session_id,
        role="user",
        content=summary_text,
        display_metadata={"display_kind": "compaction_summary"},
    )

    session_db.append_message(session_id, role="user", content="Real Turn 2")
    session_db.append_message(session_id, role="assistant", content="Answer 2")

    steps = session_db.list_session_steps(session_id)
    step_ids = [s["step"] for s in steps]
    # Compaction summary should be skipped, so Real Turn 2 is step "2"
    assert step_ids == ["1", "1.1", "2", "2.1"]
    assert steps[2]["preview"] == "Real Turn 2"


def test_idempotent_findings_injection_preserves_original_content(session_db):
    import json
    session_id = "test-session-idempotent"
    session_db.create_session(session_id=session_id, source="cli")

    session_db.append_message(session_id, role="user", content="Original user query")
    session_db.append_message(session_id, role="assistant", content="Answer 1")
    session_db.append_message(session_id, role="user", content="Question 2")

    # First rewind to Turn 1
    session_db.rewind_to_step(session_id, "1", findings="Finding A")
    active = session_db.get_messages(session_id, include_inactive=False)
    assert len(active) == 1
    assert "Original user query" in active[0]["content"]
    assert "Finding A" in active[0]["content"]

    dm = active[0].get("display_metadata") or {}
    if isinstance(dm, str):
        dm = json.loads(dm)
    assert dm.get("original_content") == "Original user query"

    # Second rewind to Turn 1 with different findings
    session_db.rewind_to_step(session_id, "1", findings="Finding B")
    active2 = session_db.get_messages(session_id, include_inactive=False)
    assert len(active2) == 1
    assert "Original user query" in active2[0]["content"]
    assert "Finding B" in active2[0]["content"]
    # Finding A should NOT be duplicated or stacked
    assert "Finding A" not in active2[0]["content"]
    dm2 = active2[0].get("display_metadata") or {}
    if isinstance(dm2, str):
        dm2 = json.loads(dm2)
    assert dm2.get("original_content") == "Original user query"



def test_list_session_steps_ignores_synthetic_user_turns(session_db):
    session_id = "test-session-synthetic"
    session_db.create_session(session_id=session_id, source="cli")

    # Turn 1: Real human prompt
    session_db.append_message(session_id, role="user", content="Hello")
    session_db.append_message(session_id, role="assistant", content="Hi")

    # Synthetic user turn (e.g. auto_continue or delegation notice)
    session_db.append_message(
        session_id,
        role="user",
        content="[Auto-continue recovery note]",
        display_kind="auto_continue",
    )
    session_db.append_message(session_id, role="assistant", content="Continuing...")

    # Turn 2: Real human prompt
    session_db.append_message(session_id, role="user", content="Next question")
    session_db.append_message(session_id, role="assistant", content="Answer 2")

    steps = session_db.list_session_steps(session_id)
    user_steps = [s for s in steps if s["type"] == "user_turn"]
    assert len(user_steps) == 2
    assert user_steps[0]["step"] == "1"
    assert user_steps[0]["preview"] == "Hello"
    assert user_steps[1]["step"] == "2"
    assert user_steps[1]["preview"] == "Next question"
