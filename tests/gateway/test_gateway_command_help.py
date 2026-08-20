"""Gateway command help rendering tests.

The Telegram platform was removed from this fork, so
``gateway.run._telegramize_command_mentions`` is now an identity no-op:
help/commands output is returned with skill-command mentions untouched.
"""

import pytest

from gateway.config import Platform
from gateway.platforms.base import MessageEvent
from gateway.session import SessionSource


def _make_event(text: str, platform: Platform) -> MessageEvent:
    return MessageEvent(
        text=text,
        source=SessionSource(
            platform=platform,
            chat_id="chat-1",
            user_id="user-1",
            user_name="tester",
            chat_type="dm",
        ),
    )


def _make_runner():
    from gateway.run import GatewayRunner

    return object.__new__(GatewayRunner)


@pytest.mark.asyncio
async def test_help_returns_skill_command_mentions_unchanged(monkeypatch):
    """With Telegram removed, /help must not rewrite skill-command mentions —
    uppercase/hyphenated slash names are returned exactly as registered."""
    monkeypatch.setattr(
        "agent.skill_commands.get_skill_commands",
        lambda: {
            "/Linear": {"description": "Open Linear"},
            "/Custom-Thing": {"description": "Run a custom thing"},
        },
    )

    result = await _make_runner()._handle_help_command(
        _make_event("/help", Platform.EMAIL)
    )

    assert "`/Linear`" in result
    assert "`/Custom-Thing`" in result
    # No Telegram-style lowercasing/underscore rewriting applies anymore.
    assert "`/linear`" not in result
    assert "`/custom_thing`" not in result


@pytest.mark.asyncio
async def test_commands_returns_skill_command_mentions_unchanged(monkeypatch):
    """Paginated /commands output likewise leaves skill-command mentions alone."""
    monkeypatch.setattr(
        "agent.skill_commands.get_skill_commands",
        lambda: {"/Linear": {"description": "Open Linear"}},
    )

    result = await _make_runner()._handle_commands_command(
        _make_event("/commands 999", Platform.EMAIL)
    )

    assert "`/Linear`" in result
    assert "`/linear`" not in result
