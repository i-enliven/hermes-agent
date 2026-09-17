"""Tests for gateway /rewind slash command dispatch and handling."""

from __future__ import annotations
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
import pytest
from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent
from gateway.session import SessionEntry, SessionSource, build_session_key
from hermes_state import SessionDB


def _make_source() -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        user_id="u1",
        chat_id="c1",
        user_name="tester",
        chat_type="dm",
    )


def _make_event(text: str) -> MessageEvent:
    return MessageEvent(text=text, source=_make_source(), message_id="m1")


@pytest.fixture
def session_db(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    db = SessionDB(db_path=tmp_path / "state.db")
    yield db
    db.close()


@pytest.mark.asyncio
async def test_gateway_rewind_list(session_db, monkeypatch):
    from gateway.run import GatewayRunner
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig()
    
    session_id = "test-gw-rewind"
    session_db.create_session(session_id, source="telegram")
    session_db.append_message(session_id, "user", "Turn 1")
    session_db.append_message(session_id, "assistant", "Answer 1")

    session_entry = SessionEntry(
        session_key=build_session_key(_make_source()),
        session_id=session_id,
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="dm",
    )
    fake_store = MagicMock()
    fake_store.session_db = session_db
    runner.session_store = fake_store
    mock_store = MagicMock()
    mock_store._store = fake_store
    mock_store.get_or_create_session = AsyncMock(return_value=session_entry)
    runner._async_session_store = mock_store

    event = _make_event("/rewind list")
    out = await runner._handle_rewind_command(event)
    assert "Available steps for rewind:" in out
    assert "Step 1:" in out


@pytest.mark.asyncio
async def test_gateway_rewind_step(session_db, monkeypatch):
    from gateway.run import GatewayRunner
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig()
    runner._evict_cached_agent = MagicMock()
    
    session_id = "test-gw-rewind-step"
    session_db.create_session(session_id, source="telegram")
    session_db.append_message(session_id, "user", "Turn 1")
    session_db.append_message(session_id, "assistant", "Answer 1")
    session_db.append_message(session_id, "user", "Turn 2")
    session_db.append_message(session_id, "assistant", "Answer 2")

    session_entry = SessionEntry(
        session_key=build_session_key(_make_source()),
        session_id=session_id,
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="dm",
    )
    fake_store = MagicMock()
    fake_store.session_db = session_db
    runner.session_store = fake_store
    mock_store = MagicMock()
    mock_store._store = fake_store
    mock_store.get_or_create_session = AsyncMock(return_value=session_entry)
    runner._async_session_store = mock_store

    event = _make_event("/rewind 1 'Key finding'")
    out = await runner._handle_rewind_command(event)
    assert "Rewound session to Step 1" in out
    runner._evict_cached_agent.assert_called_once()
    active = session_db.get_messages(session_id, include_inactive=False)
    assert len(active) == 1
    assert "Key finding" in active[0]["content"]
    from agent.model_metadata import estimate_messages_tokens_rough
    conv = session_db.get_messages_as_conversation(session_id, repair_alternation=True)
    assert session_entry.last_prompt_tokens == estimate_messages_tokens_rough(conv)
    assert session_entry.last_prompt_tokens > 0
