import pytest
from tools.time_travel_tool import parse_step_identifier, time_travel_tool
from tools.registry import registry


class DummyAgent:
    def __init__(self, turn=2, cycle=3):
        self._user_turn_count = turn
        self._api_call_count = cycle
        self._pending_time_travel = None


def test_parse_step_identifier():
    assert parse_step_identifier("1") == (1, 0)
    assert parse_step_identifier("1.2") == (1, 2)
    assert parse_step_identifier("10.5") == (10, 5)
    assert parse_step_identifier("invalid") is None
    assert parse_step_identifier("") is None
    assert parse_step_identifier("1.2.3") is None


def test_time_travel_tool_validation():
    agent = DummyAgent(turn=2, cycle=3)

    # Invalid format
    res = time_travel_tool("foo", "findings", agent=agent)
    assert "Invalid target_step format" in res

    # Empty findings
    res = time_travel_tool("1.1", "", agent=agent)
    assert "findings' must not be empty" in res

    # Target turn >= 1
    res = time_travel_tool("0", "findings", agent=agent)
    assert "Target turn must be >= 1" in res

    # Target step not in the past (future turn)
    res = time_travel_tool("3", "findings", agent=agent)
    assert "not in the past" in res

    # Target step not in the past (same turn, future cycle)
    res = time_travel_tool("2.4", "findings", agent=agent)
    assert "not in the past" in res

    # Target step not in the past (current step)
    res = time_travel_tool("2.3", "findings", agent=agent)
    assert "not in the past" in res

    # Valid rewind to past turn
    res = time_travel_tool("1", "Found info in turn 1", agent=agent)
    assert "Time travel initiated to step '1'" in res
    assert agent._pending_time_travel is not None
    assert agent._pending_time_travel["target_step"] == "1"
    assert agent._pending_time_travel["target_turn"] == 1
    assert agent._pending_time_travel["target_cycle"] == 0
    assert agent._pending_time_travel["findings"] == "Found info in turn 1"

    # Valid rewind to past cycle in current turn
    res = time_travel_tool("2.1", "Found info in cycle 2.1", agent=agent)
    assert "Time travel initiated to step '2.1'" in res
    assert agent._pending_time_travel["target_step"] == "2.1"
    assert agent._pending_time_travel["target_turn"] == 2
    assert agent._pending_time_travel["target_cycle"] == 1


def test_time_travel_registered():
    entry = registry.get_entry("time_travel")
    assert entry is not None
    assert entry.name == "time_travel"
    assert entry.toolset == "time_travel"
    assert entry.emoji == "⏳"
    assert "target_step" in entry.schema["parameters"]["properties"]
    assert "findings" in entry.schema["parameters"]["properties"]


def test_time_travel_core_and_not_deferrable():
    from toolsets import _HERMES_CORE_TOOLS
    from tools.tool_search import is_deferrable_tool_name

    assert "time_travel" in _HERMES_CORE_TOOLS
    assert is_deferrable_tool_name("time_travel") is False


def test_time_travel_reality_check_with_db(tmp_path):
    from hermes_state import SessionDB

    db = SessionDB(db_path=tmp_path / "test.db")
    session_id = "test-reality-check"
    db.create_session(session_id, source="cli")
    db.append_message(session_id, "user", "turn 1 prompt")
    db.append_message(session_id, "assistant", "turn 1 response")

    agent = DummyAgent(turn=7, cycle=4)
    agent._session_db = db
    agent.session_id = session_id

    # Step 5 is mathematically in the past (5 < 7), but does not exist in DB
    res = time_travel_tool("5", "Found info", agent=agent)
    assert "Step '5' not found in active session" in res
    assert agent._pending_time_travel is None

    # Step 1 actually exists
    res = time_travel_tool("1", "Found info", agent=agent)
    assert "Time travel initiated to step '1'" in res
    assert agent._pending_time_travel is not None
    assert agent._pending_time_travel["target_step"] == "1"


def test_time_travel_reality_check_with_messages():
    messages = [
        {"role": "user", "content": "turn 1 prompt"},
        {"role": "assistant", "content": "turn 1 response"},
    ]
    agent = DummyAgent(turn=5, cycle=2)
    agent.messages = messages

    # Step 4 is mathematically in the past (4 < 5), but does not exist in messages
    res = time_travel_tool("4", "Found info", agent=agent)
    assert "Step '4' not found in active conversation history" in res
    assert agent._pending_time_travel is None

    # Step 1 exists
    res = time_travel_tool("1", "Found info", agent=agent)
    assert "Time travel initiated to step '1'" in res
    assert agent._pending_time_travel is not None



def test_time_travel_reality_check_db_and_messages_divergence(tmp_path):
    """When a step exists in SessionDB but is not present in in-memory messages
    (e.g., compacted or pruned), time_travel_tool must reject the call and NOT
    stage pending rollback.
    """
    from hermes_state import SessionDB

    db = SessionDB(db_path=tmp_path / "test_div.db")
    session_id = "test-div"
    db.create_session(session_id, source="cli")
    db.append_message(session_id, "user", "turn 1")
    db.append_message(session_id, "assistant", "turn 1 answer")
    db.append_message(session_id, "user", "turn 2")
    db.append_message(session_id, "assistant", "turn 2 answer")
    db.append_message(session_id, "user", "turn 3")
    db.append_message(session_id, "assistant", "turn 3 answer")

    # In-memory history only has turn 3 (turns 1 and 2 were compacted away)
    messages = [
        {"role": "system", "content": "Compacted history summary"},
        {"role": "user", "content": "turn 3"},
        {"role": "assistant", "content": "turn 3 answer"},
    ]

    agent = DummyAgent(turn=4, cycle=1)
    agent._session_db = db
    agent.session_id = session_id
    agent.messages = messages

    # Step 2 exists in SessionDB, but is not findable in active messages
    res = time_travel_tool("2", "Found info", agent=agent, messages=messages)
    assert "Error: Step '2' not found in active conversation history" in res
    assert agent._pending_time_travel is None
