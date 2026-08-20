"""Tests for the home-thread target resolution used by /sethome.

A source thread id is kept as the home target unless a synthetic
thread-per-message id is detected. Without a message id to compare,
the thread is kept (never guess)."""

from types import SimpleNamespace

from gateway.config import Platform
from gateway.slash_commands import _home_thread_from_source


def _source(platform=Platform.EMAIL, thread_id=None, message_id=None):
    return SimpleNamespace(
        platform=platform, thread_id=thread_id, message_id=message_id
    )


class TestHomeThreadFromSource:
    def test_no_thread_returns_none(self):
        assert _home_thread_from_source(_source()) is None

    def test_no_message_id_keeps_thread(self):
        """Without a message id to compare, never guess: keep the thread."""
        src = _source(thread_id="1755040000.000100", message_id=None)
        assert _home_thread_from_source(src) == "1755040000.000100"
