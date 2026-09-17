import os
from unittest.mock import MagicMock
import pytest
from hermes_state import SessionDB
from cli import HermesCLI


@pytest.fixture
def session_db(tmp_path):
    os.environ["HERMES_HOME"] = str(tmp_path / ".hermes")
    os.makedirs(tmp_path / ".hermes", exist_ok=True)
    db = SessionDB(db_path=tmp_path / ".hermes" / "test_sessions.db")
    yield db
    db.close()


def test_cli_rewind_command_execution(session_db, monkeypatch, capsys):
    cli = MagicMock(spec=HermesCLI)
    cli._session_db = session_db
    cli.session_id = "test-rewind-cli"
    cli.agent = MagicMock()
    session_db.create_session(session_id=cli.session_id, source="cli")

    # Add messages to DB
    session_db.append_message(cli.session_id, role="user", content="Turn 1 prompt")
    session_db.append_message(cli.session_id, role="assistant", content="Cycle 1.1 response")
    session_db.append_message(cli.session_id, role="user", content="Turn 2 prompt")
    session_db.append_message(cli.session_id, role="assistant", content="Cycle 2.1 response")

    cli.conversation_history = [
        {"role": "user", "content": "Turn 1 prompt"},
        {"role": "assistant", "content": "Cycle 1.1 response"},
        {"role": "user", "content": "Turn 2 prompt"},
        {"role": "assistant", "content": "Cycle 2.1 response"},
    ]

    # Test /rewind list
    HermesCLI._handle_rewind_command(cli, "/rewind list")
    captured = capsys.readouterr().out
    assert "Available steps for rewind:" in captured
    assert "Step 1:" in captured
    assert "Step 2:" in captured

    # Test /rewind 1 with confirmation bypass
    cli._confirm_destructive_slash = MagicMock(return_value=True)
    cli.rewind_to_step = lambda step, findings=None: HermesCLI.rewind_to_step(cli, step, findings=findings)

    HermesCLI._handle_rewind_command(cli, "/rewind 1 'Discovered in exploration'")
    captured = capsys.readouterr().out
    assert "Rewound to Step 1" in captured
    assert len(cli.conversation_history) == 1
    assert "Discovered in exploration" in cli.conversation_history[0]["content"]


def test_cli_rewind_to_cycle(session_db, capsys):
    cli = MagicMock(spec=HermesCLI)
    cli._session_db = session_db
    cli.session_id = "test-rewind-cycle"
    cli.agent = MagicMock()
    session_db.create_session(session_id=cli.session_id, source="cli")

    session_db.append_message(cli.session_id, role="user", content="Turn 1 prompt")
    session_db.append_message(cli.session_id, role="assistant", content="", tool_calls=[{"id": "t1", "function": {"name": "search"}}])
    session_db.append_message(cli.session_id, role="tool", tool_call_id="t1", content="results")
    session_db.append_message(cli.session_id, role="assistant", content="Cycle 1.2 noise")

    cli.conversation_history = [
        {"role": "user", "content": "Turn 1 prompt"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "t1", "function": {"name": "search"}}]},
        {"role": "tool", "tool_call_id": "t1", "content": "results"},
        {"role": "assistant", "content": "Cycle 1.2 noise"},
    ]

    HermesCLI.rewind_to_step(cli, "1.1", findings="Found key line")
    captured = capsys.readouterr().out
    assert "Rewound to Step 1.1" in captured
    assert len(cli.conversation_history) == 3
    assert "Found key line" in cli.conversation_history[-1]["content"]


def test_cli_rewind_now_inline_skip_strips_now(session_db, capsys):
    cli = MagicMock(spec=HermesCLI)
    cli._session_db = session_db
    cli.session_id = "test-rewind-skip"
    cli.agent = MagicMock()
    cli._confirm_destructive_slash = MagicMock(return_value="once")
    rewound_args = []
    cli.rewind_to_step = lambda step, findings=None: rewound_args.append((step, findings))

    HermesCLI._handle_rewind_command(cli, "/rewind 1 now")
    assert len(rewound_args) == 1
    step, findings = rewound_args[0]
    assert step == "1"
    assert findings is None  # 'now' was stripped and not treated as findings


def test_cli_rewind_lazy_history_hydration(session_db, capsys):
    cli = MagicMock(spec=HermesCLI)
    cli._session_db = session_db
    cli.session_id = "test-rewind-lazy"
    cli.agent = MagicMock()
    session_db.create_session(session_id=cli.session_id, source="cli")
    session_db.append_message(cli.session_id, role="user", content="Turn 1 prompt")
    session_db.append_message(cli.session_id, role="assistant", content="Cycle 1.1 response")
    session_db.append_message(cli.session_id, role="user", content="Turn 2 prompt")

    # Start with empty conversation_history (e.g. in slash worker)
    cli.conversation_history = []

    HermesCLI.rewind_to_step(cli, "1")
    captured = capsys.readouterr().out
    assert "Rewound to Step 1" in captured
    assert len(cli.conversation_history) == 1
    assert cli.conversation_history[0]["content"] == "Turn 1 prompt"


def test_confirm_destructive_slash_non_interactive_guard(monkeypatch, capsys):
    import sys
    cli = MagicMock(spec=HermesCLI)
    cli._app = None
    cli._split_destructive_skip = HermesCLI._split_destructive_skip
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr("cli.load_cli_config", lambda: {"approvals": {"destructive_slash_confirm": True}})

    res = HermesCLI._confirm_destructive_slash(cli, "rewind", "Rewind test", cmd_original="/rewind 1")
    assert res is None
    captured = capsys.readouterr().out
    assert "requires interactive confirmation" in captured

def test_cli_rewind_updates_context_tokens(session_db):
    from agent.model_metadata import estimate_messages_tokens_rough

    cli = MagicMock(spec=HermesCLI)
    cli._session_db = session_db
    cli.session_id = "test-rewind-tokens"
    session_db.create_session(session_id=cli.session_id, source="cli")
    session_db.append_message(cli.session_id, role="user", content="Turn 1 prompt")
    session_db.append_message(cli.session_id, role="assistant", content="Cycle 1.1 response")
    session_db.append_message(cli.session_id, role="user", content="Turn 2 prompt with extra text")
    session_db.append_message(cli.session_id, role="assistant", content="Cycle 2.1 response with extra text")

    cli.conversation_history = [
        {"role": "user", "content": "Turn 1 prompt"},
        {"role": "assistant", "content": "Cycle 1.1 response"},
        {"role": "user", "content": "Turn 2 prompt with extra text"},
        {"role": "assistant", "content": "Cycle 2.1 response with extra text"},
    ]

    compressor = MagicMock()
    compressor.last_prompt_tokens = 9999
    cli.agent = MagicMock()
    cli.agent.context_compressor = compressor

    HermesCLI.rewind_to_step(cli, "1")
    assert len(cli.conversation_history) == 1
    expected_tokens = estimate_messages_tokens_rough(cli.conversation_history)
    assert compressor.last_prompt_tokens == expected_tokens
    assert compressor.last_prompt_tokens < 9999
