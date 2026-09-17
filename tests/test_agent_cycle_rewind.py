from unittest.mock import MagicMock
import pytest
from agent.conversation_loop import _apply_time_travel_rewind
from agent.model_metadata import estimate_messages_tokens_rough
from hermes_state import SessionDB


class MockAgent:
    def __init__(self, session_db=None, session_id=None):
        self._user_turn_count = 1
        self._api_call_count = 3
        self._pending_time_travel = None
        self._session_db = session_db
        self.session_id = session_id
        self.log_prefix = ""
        self._checkpoint_mgr = None

    def _vprint(self, *args, **kwargs):
        pass


def test_apply_time_travel_rewind_to_cycle():
    agent = MockAgent()
    compressor = MagicMock()
    compressor.last_prompt_tokens = 9999
    agent.context_compressor = compressor
    agent._pending_time_travel = {
        "target_step": "1.1",
        "target_turn": 1,
        "target_cycle": 1,
        "findings": "Extracted key function foo() from bar.py:12",
        "restore_files": False,
    }

    messages = [
        {"role": "system", "content": "You are Hermes."},
        {"role": "user", "content": "Please analyze repo."},
        # Cycle 1.1
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "function": {"name": "grep_search"}}],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "Matches in bar.py"},
        # Cycle 1.2
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c2", "function": {"name": "view_file"}}],
        },
        {"role": "tool", "tool_call_id": "c2", "content": "1000 lines of file data"},
        # Cycle 1.3
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c3", "function": {"name": "time_travel"}}],
        },
        {"role": "tool", "tool_call_id": "c3", "content": "Time travel initiated"},
    ]

    new_api_call_count = _apply_time_travel_rewind(agent, messages, api_call_count=3)

    assert new_api_call_count == 1
    assert len(messages) == 4
    # Messages kept: system, user, assistant 1.1, tool 1.1
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert messages[2]["role"] == "assistant"
    assert messages[3]["role"] == "tool"
    expected_tokens = estimate_messages_tokens_rough(messages)
    assert compressor.last_prompt_tokens == expected_tokens
    assert compressor.last_prompt_tokens < 9999
    assert "Extracted key function foo() from bar.py:12" in messages[3]["content"]


def test_apply_time_travel_rewind_to_user_turn():
    agent = MockAgent()
    agent._pending_time_travel = {
        "target_step": "1",
        "target_turn": 1,
        "target_cycle": 0,
        "findings": "Summary of preliminary search",
        "restore_files": False,
    }

    messages = [
        {"role": "system", "content": "You are Hermes."},
        {"role": "user", "content": "User question"},
        {"role": "assistant", "content": "Assistant draft"},
        {"role": "tool", "tool_call_id": "t1", "content": "Tool output"},
    ]

    new_api_call_count = _apply_time_travel_rewind(agent, messages, api_call_count=2)

    assert new_api_call_count == 0
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert "Summary of preliminary search" in messages[1]["content"]


def test_apply_time_travel_with_session_db(tmp_path):
    db_path = tmp_path / "state.db"
    db = SessionDB(db_path)
    session_id = "test-agent-cycle-sess"
    db.create_session(session_id=session_id, source="cli")

    u_id = db.append_message(session_id, role="user", content="Where is config?")
    a1_id = db.append_message(
        session_id,
        role="assistant",
        content="",
        tool_calls=[{"id": "tc1", "function": {"name": "grep_search"}}],
    )
    t1_id = db.append_message(session_id, role="tool", tool_call_id="tc1", content="config.yaml")
    a2_id = db.append_message(
        session_id,
        role="assistant",
        content="",
        tool_calls=[{"id": "tc2", "function": {"name": "read_file"}}],
    )
    t2_id = db.append_message(session_id, role="tool", tool_call_id="tc2", content="raw yaml 500 lines")

    agent = MockAgent(session_db=db, session_id=session_id)
    agent._pending_time_travel = {
        "target_step": "1.1",
        "target_turn": 1,
        "target_cycle": 1,
        "findings": "config has timeout: 30",
        "restore_files": False,
    }

    messages = [
        {"role": "system", "content": "System"},
        {"role": "user", "content": "Where is config?"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "tc1", "function": {"name": "grep_search"}}]},
        {"role": "tool", "tool_call_id": "tc1", "content": "config.yaml"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "tc2", "function": {"name": "read_file"}}]},
        {"role": "tool", "tool_call_id": "tc2", "content": "raw yaml 500 lines"},
    ]

    new_count = _apply_time_travel_rewind(agent, messages, api_call_count=2)
    assert new_count == 1
    assert len(messages) == 4

    # Verify SessionDB has soft-deleted cycle 1.2
    active_msgs = db.get_messages(session_id, include_inactive=False)
    assert len(active_msgs) == 3
    assert "timeout: 30" in active_msgs[-1]["content"]

    db.close()


def test_apply_time_travel_restore_files_invokes_checkpoint_manager():
    from unittest.mock import MagicMock
    agent = MockAgent()
    mock_mgr = MagicMock()
    mock_mgr.list_checkpoints.return_value = [{"hash": "abc123def456", "timestamp": "2026-09-18T00:00:00Z"}]
    agent._checkpoint_mgr = mock_mgr

    agent._pending_time_travel = {
        "target_step": "1",
        "target_turn": 1,
        "target_cycle": 0,
        "findings": "Revert to start",
        "restore_files": True,
    }

    messages = [
        {"role": "user", "content": "Initial request", "display_metadata": {"checkpoint_hash": "abc123def456"}},
        {"role": "assistant", "content": "Modified files"},
    ]

    _apply_time_travel_rewind(agent, messages, api_call_count=1)

    mock_mgr.restore.assert_called_once()
    args, kwargs = mock_mgr.restore.call_args
    assert kwargs.get("commit_hash") == "abc123def456"
    assert kwargs.get("safe") is True



def test_apply_time_travel_rewind_aborts_without_db_mutation_if_messages_missing_step(tmp_path):
    """If target_step cannot be located in the in-memory messages list,
    _apply_time_travel_rewind must abort early and NOT soft-delete SessionDB rows.
    """
    db_path = tmp_path / "test_abort.db"
    db = SessionDB(db_path=db_path)
    session_id = "test-session-abort"
    db.create_session(session_id=session_id, source="cli")

    for i in range(1, 4):
        db.append_message(session_id, role="user", content=f"Turn {i}")
        db.append_message(session_id, role="assistant", content=f"Resp {i}")

    # Messages in memory only has Turn 3
    messages = [
        {"role": "system", "content": "Summary of prior turns"},
        {"role": "user", "content": "Turn 3"},
        {"role": "assistant", "content": "Resp 3"},
    ]

    agent = MockAgent()
    agent._session_db = db
    agent.session_id = session_id
    agent._user_turn_count = 3
    agent._api_call_count = 1

    agent._pending_time_travel = {
        "target_step": "2",
        "target_turn": 2,
        "target_cycle": 0,
        "findings": "Should not apply",
        "restore_files": False,
    }

    _apply_time_travel_rewind(agent, messages, api_call_count=1)

    # In-memory messages must not be mutated
    assert len(messages) == 3
    assert not any("Should not apply" in m.get("content", "") for m in messages)

    # SessionDB must NOT have soft-deleted any messages!
    active_in_db = [m for m in db.get_messages_as_conversation(session_id)]
    assert len(active_in_db) == 6
    db.close()


def test_apply_time_travel_rewind_updates_user_turn_count():
    agent = MockAgent()
    agent._user_turn_count = 5
    agent._api_call_count = 3

    messages = [
        {"role": "user", "content": "Turn 1"},
        {"role": "assistant", "content": "Resp 1"},
        {"role": "user", "content": "Turn 2"},
        {"role": "assistant", "content": "Resp 2"},
    ]

    agent._pending_time_travel = {
        "target_step": "1",
        "target_turn": 1,
        "target_cycle": 0,
        "findings": "Back to turn 1",
        "restore_files": False,
    }

    _apply_time_travel_rewind(agent, messages, api_call_count=2)

    assert agent._user_turn_count == 1
    assert agent._api_call_count == 0
    assert len(messages) == 1
    assert "Back to turn 1" in messages[0]["content"]
