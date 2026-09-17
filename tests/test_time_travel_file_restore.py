"""Integration test for time travel file rollback with real CheckpointManager."""

import os
from pathlib import Path
import pytest

from tools.checkpoint_manager import CheckpointManager
from hermes_state import SessionDB
from agent.conversation_loop import _apply_time_travel_rewind


class MockAgentForRewind:
    def __init__(self, session_db, session_id, checkpoint_mgr):
        self._session_db = session_db
        self.session_id = session_id
        self._checkpoint_mgr = checkpoint_mgr
        self._user_turn_count = 2
        self._api_call_count = 1
        self._pending_time_travel = None
        self._last_checkpoint_hash = None
        self._vprint = lambda *a, **k: None
        self.log_prefix = ""


def test_time_travel_real_checkpoint_restore(tmp_path, monkeypatch):
    """Verify that restore_files=True restores the exact snapshot associated with the target step,
    rather than falling back to the newest snapshot (checkpoints[0]).
    """
    base_dir = tmp_path / "checkpoints"
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    target_file = work_dir / "target.txt"

    # Set isolated CHECKPOINT_BASE
    monkeypatch.setattr("tools.checkpoint_manager.CHECKPOINT_BASE", base_dir)

    mgr = CheckpointManager(enabled=True)

    # Step 1: File is at VERSION-ONE
    target_file.write_text("VERSION-ONE\n")
    mgr.ensure_checkpoint(str(work_dir), "turn 1 initial")
    cp1 = mgr.get_latest_checkpoint_hash(str(work_dir))
    assert cp1 is not None

    # Step 2: Tool writes VERSION-TWO
    mgr.new_turn()
    target_file.write_text("VERSION-TWO\n")
    mgr.record_agent_write(str(target_file))
    mgr.ensure_checkpoint(str(work_dir), "turn 2 modification")
    cp2 = mgr.get_latest_checkpoint_hash(str(work_dir))
    assert cp2 is not None
    assert cp1 != cp2
    assert target_file.read_text() == "VERSION-TWO\n"

    # Set up SessionDB with step 1 having cp1 stamped in display_metadata
    db_path = tmp_path / "test.db"
    db = SessionDB(db_path=db_path)
    session_id = "test-restore-session"
    db.create_session(session_id, source="cli")

    db.append_message(
        session_id,
        "user",
        "turn 1",
        display_metadata={"checkpoint_hash": cp1},
    )
    db.append_message(session_id, "assistant", "turn 1 answer")
    db.append_message(
        session_id,
        "user",
        "turn 2",
        display_metadata={"checkpoint_hash": cp2},
    )
    db.append_message(session_id, "assistant", "turn 2 answer")

    # In-memory messages
    messages = [
        {"role": "user", "content": "turn 1", "display_metadata": {"checkpoint_hash": cp1}},
        {"role": "assistant", "content": "turn 1 answer"},
        {"role": "user", "content": "turn 2", "display_metadata": {"checkpoint_hash": cp2}},
        {"role": "assistant", "content": "turn 2 answer"},
    ]

    agent = MockAgentForRewind(db, session_id, mgr)
    agent._pending_time_travel = {
        "target_step": "1",
        "target_turn": 1,
        "target_cycle": 0,
        "findings": "Restoring to turn 1",
        "restore_files": True,
    }

    # Change working dir to work_dir during rewind
    old_cwd = os.getcwd()
    try:
        os.chdir(str(work_dir))
        _apply_time_travel_rewind(agent, messages, 1)
    finally:
        os.chdir(old_cwd)
    # CRITICAL ASSERTION: The file must be restored to VERSION-ONE, NOT stay at VERSION-TWO!
    assert target_file.read_text() == "VERSION-ONE\n"


def test_time_travel_no_blind_fallback_to_newest_snapshot(tmp_path, monkeypatch):
    """When a boundary has no checkpoint_hash and no matching timestamp,
    it must NOT restore checkpoints[0] (the newest snapshot).
    """
    base_dir = tmp_path / "checkpoints"
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    target_file = work_dir / "target.txt"

    monkeypatch.setattr("tools.checkpoint_manager.CHECKPOINT_BASE", base_dir)

    mgr = CheckpointManager(enabled=True)

    # Initial snapshot
    target_file.write_text("INITIAL\n")
    mgr.ensure_checkpoint(str(work_dir), "init")

    # Second snapshot (newest)
    mgr.new_turn()
    target_file.write_text("MODIFIED-AFTER-EXPLORATION\n")
    mgr.ensure_checkpoint(str(work_dir), "after exploration")

    # Set current disk content to something specific
    target_file.write_text("CURRENT-CONTENT\n")

    db_path = tmp_path / "test2.db"
    db = SessionDB(db_path=db_path)
    session_id = "test-no-blind-fallback"
    db.create_session(session_id, source="cli")
    # No checkpoint_hash stamped
    db.append_message(session_id, "user", "turn 1")

    messages = [{"role": "user", "content": "turn 1"}]

    agent = MockAgentForRewind(db, session_id, mgr)
    agent._pending_time_travel = {
        "target_step": "1",
        "target_turn": 1,
        "target_cycle": 0,
        "findings": "Rewind test",
        "restore_files": True,
    }

    old_cwd = os.getcwd()
    try:
        os.chdir(str(work_dir))
        _apply_time_travel_rewind(agent, messages, 1)
    finally:
        os.chdir(old_cwd)

    # It must NOT have restored "MODIFIED-AFTER-EXPLORATION" from checkpoints[0]!
    assert target_file.read_text() != "MODIFIED-AFTER-EXPLORATION\n"



def test_time_travel_tier3_isolation(tmp_path, monkeypatch):
    """Verify that agent._last_checkpoint_hash (from a subsequent turn's write)
    does NOT cause rewind to restore the newest snapshot when target step has no stamped hash.
    """
    base_dir = tmp_path / "checkpoints"
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    target_file = work_dir / "target.txt"

    monkeypatch.setattr("tools.checkpoint_manager.CHECKPOINT_BASE", base_dir)

    mgr = CheckpointManager(enabled=True)

    # Turn 1: VERSION-ONE
    target_file.write_text("VERSION-ONE\n")
    mgr.ensure_checkpoint(str(work_dir), "turn 1 start")
    cp1 = mgr.get_latest_checkpoint_hash(str(work_dir))

    # Turn 2: Tool writes VERSION-TWO
    mgr.new_turn()
    target_file.write_text("VERSION-TWO\n")
    mgr.ensure_checkpoint(str(work_dir), "before write_file")
    cp2 = mgr.get_latest_checkpoint_hash(str(work_dir))

    assert cp1 != cp2

    # Agent in live state has _last_checkpoint_hash pointing to cp2 (the turn 2 write)
    agent = MockAgentForRewind(None, None, mgr)
    agent._last_checkpoint_hash = cp2

    # Target step 1 has no hash stamped in display_metadata
    messages = [
        {"role": "user", "content": "turn 1"},
        {"role": "assistant", "content": "turn 1 answer"},
        {"role": "user", "content": "turn 2"},
        {"role": "assistant", "content": "turn 2 answer"},
    ]

    agent._pending_time_travel = {
        "target_step": "1",
        "target_turn": 1,
        "target_cycle": 0,
        "findings": "Restoring to turn 1",
        "restore_files": True,
    }

    old_cwd = os.getcwd()
    try:
        os.chdir(str(work_dir))
        _apply_time_travel_rewind(agent, messages, 1)
    finally:
        os.chdir(old_cwd)

    # Must restore to VERSION-ONE (via turn 1 start reason match), NOT stay at VERSION-TWO!
    assert target_file.read_text() == "VERSION-ONE\n"


def test_time_travel_tier4_sub_two_second_cadence(tmp_path, monkeypatch):
    """Verify that when turns are <2 seconds apart and only timestamps are available,
    it picks the target turn's checkpoint rather than newest checkpoint.
    """
    import time

    base_dir = tmp_path / "checkpoints"
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    target_file = work_dir / "target.txt"

    monkeypatch.setattr("tools.checkpoint_manager.CHECKPOINT_BASE", base_dir)

    mgr = CheckpointManager(enabled=True)

    t0 = time.time()
    # Turn 1
    target_file.write_text("VERSION-ONE\n")
    mgr.ensure_checkpoint(str(work_dir), "turn 1 custom")
    cp1 = mgr.get_latest_checkpoint_hash(str(work_dir))

    time.sleep(0.3)  # sub-2 second cadence

    # Turn 2
    mgr.new_turn()
    target_file.write_text("VERSION-TWO\n")
    mgr.ensure_checkpoint(str(work_dir), "turn 2 custom")
    cp2 = mgr.get_latest_checkpoint_hash(str(work_dir))

    assert cp1 != cp2

    agent = MockAgentForRewind(None, None, mgr)
    agent._last_checkpoint_hash = None

    messages = [
        {"role": "user", "content": "turn 1", "timestamp": str(t0)},
        {"role": "assistant", "content": "turn 1 answer"},
        {"role": "user", "content": "turn 2", "timestamp": str(t0 + 0.3)},
        {"role": "assistant", "content": "turn 2 answer"},
    ]

    agent._pending_time_travel = {
        "target_step": "1",
        "target_turn": 1,
        "target_cycle": 0,
        "findings": "Restoring to turn 1",
        "restore_files": True,
    }

    old_cwd = os.getcwd()
    try:
        os.chdir(str(work_dir))
        _apply_time_travel_rewind(agent, messages, 1)
    finally:
        os.chdir(old_cwd)

    # Must restore to VERSION-ONE, NOT stay at VERSION-TWO
    assert target_file.read_text() == "VERSION-ONE\n"
