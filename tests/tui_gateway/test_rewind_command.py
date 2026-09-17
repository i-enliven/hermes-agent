"""Tests for /rewind handling in tui_gateway."""

from __future__ import annotations

import importlib
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from hermes_state import SessionDB


@pytest.fixture()
def hermes_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    yield home


@pytest.fixture()
def server(hermes_home):
    with patch.dict(
        "sys.modules",
        {
            "hermes_cli.env_loader": MagicMock(),
            "hermes_cli.banner": MagicMock(),
        },
    ):
        mod = importlib.import_module("tui_gateway.server")

    methods = dict(mod._methods)
    yield mod
    mod._methods.clear()
    mod._methods.update(methods)
    mod._sessions.clear()
    mod._pending.clear()
    mod._answers.clear()
    mod._db = None


@pytest.fixture()
def db(hermes_home):
    return SessionDB(db_path=hermes_home / "state.db")


@pytest.fixture()
def session_with_history(server, db):
    sid = "sid-rewind"
    session_key = "tui-rewind-1"
    db.create_session(session_key, source="tui")
    for i in range(1, 4):
        db.append_message(session_key, "user", f"question {i}")
        db.append_message(session_key, "assistant", f"answer {i}")
    history = db.get_messages_as_conversation(session_key)
    agent = MagicMock()
    agent._memory_manager = MagicMock()
    agent._last_flushed_db_idx = len(history)
    agent._user_turn_count = 3
    agent._api_call_count = 3
    s = {
        "session_key": session_key,
        "history": list(history),
        "history_lock": threading.Lock(),
        "history_version": 0,
        "running": False,
        "agent": agent,
        "attached_images": [],
        "cols": 120,
    }
    server._sessions[sid] = s
    server._db = db
    return sid, session_key, s, agent


def _call(server, method, **params):
    return server._methods[method](1, params)


def test_rewind_list(server, session_with_history):
    sid, session_key, s, agent = session_with_history
    resp = _call(server, "command.dispatch", session_id=sid, name="rewind", arg="list")
    assert resp["result"]["type"] == "exec"
    out = resp["result"]["output"]
    assert "Available steps for rewind:" in out
    assert "Step 1:" in out
    assert "Step 2:" in out
    assert "Step 3:" in out


def test_rewind_step_with_findings(server, session_with_history, db):
    sid, session_key, s, agent = session_with_history
    resp = _call(server, "command.dispatch", session_id=sid, name="rewind", arg="1 'Key finding'")
    assert resp["result"]["type"] == "exec"
    out = resp["result"]["output"]
    assert "Rewound to step 1" in out
    # Memory manager notified
    agent._memory_manager.on_session_switch.assert_called_once_with(
        session_key, parent_session_id="", reset=False, rewound=True
    )
    # Check agent counters reset to target turn
    assert agent._user_turn_count == 1
    assert agent._api_call_count == 0
    assert agent._last_flushed_db_idx == len(s["history"])

    # Check in-memory history truncated
    assert len(s["history"]) == 1
    assert "Key finding" in s["history"][0]["content"]
    # Check DB truncated
    active = db.get_messages(session_key, include_inactive=False)
    assert len(active) == 1
    assert "Key finding" in active[0]["content"]


def test_rewind_cycle_counter_sync(server, session_with_history, db):
    sid, session_key, s, agent = session_with_history
    resp = _call(server, "command.dispatch", session_id=sid, name="rewind", arg="2.1")
    assert resp["result"]["type"] == "exec"
    out = resp["result"]["output"]
    assert "Rewound to step 2.1" in out
    assert agent._user_turn_count == 2
    assert agent._api_call_count == 1
    assert agent._last_flushed_db_idx == len(s["history"])

def test_rewind_updates_context_tokens_and_emits_session_info(server, session_with_history, monkeypatch):
    from agent.model_metadata import estimate_messages_tokens_rough

    sid, session_key, s, agent = session_with_history
    compressor = MagicMock()
    compressor.last_prompt_tokens = 8888
    compressor.context_length = 100000
    agent.context_compressor = compressor

    emitted_events = []
    monkeypatch.setattr(server, "_emit", lambda ev, target_sid, payload: emitted_events.append((ev, target_sid, payload)))

    resp = _call(server, "command.dispatch", session_id=sid, name="rewind", arg="1")
    assert resp["result"]["type"] == "exec"

    expected_tokens = estimate_messages_tokens_rough(s["history"])
    assert compressor.last_prompt_tokens == expected_tokens
    assert compressor.last_prompt_tokens < 8888

    info_events = [payload for ev, target_sid, payload in emitted_events if ev == "session.info" and target_sid == sid]
    assert len(info_events) >= 1
    usage = info_events[-1].get("usage", {})
    assert usage.get("context_used") == expected_tokens
