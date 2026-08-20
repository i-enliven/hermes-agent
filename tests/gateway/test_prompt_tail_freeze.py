"""Byte-stable gateway system prompts (the ephemeral session-context pin).

The composed system prompt used to change bytes nearly every gateway turn:
the "## Current Session Context" block was re-rendered from live platform
state per message (thread renames, voice-channel member/speaking state,
one-shot onboarding and auto-reset notes).  Every byte change re-keys the
provider prompt cache AND changes the gateway agent-cache signature, forcing
a full AIAgent rebuild per message.

The fix pins the rendered session-context bytes per session keyed by a hash
of the exact renderer inputs (``_ephemeral_change_key``) and relocates
must-deliver per-turn facts onto the current user message (the api_content
sidecar), so a key hit reuses the pinned bytes verbatim.

The maintained invariant — every rendered input appears in the change key —
is guarded by the parity test below.
"""

from __future__ import annotations

import hashlib
from unittest.mock import MagicMock

import pytest

from gateway.config import GatewayConfig, HomeChannel, Platform, PlatformConfig
from gateway.session import (
    SessionContext,
    SessionSource,
    build_session_context_prompt,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_runner(**attrs):
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner._session_ephemeral_pin = {}
    runner._session_vc_last = {}
    runner._pending_turn_sidecar_notes = {}
    runner._session_model_overrides = {}
    runner._session_reasoning_overrides = {}
    runner.adapters = {}
    runner.session_store = MagicMock()
    for key, value in attrs.items():
        setattr(runner, key, value)
    return runner


def _make_context(
    *,
    platform: Platform = Platform.EMAIL,
    chat_id: str = "111222333",
    chat_name: str = "general",
    chat_type: str = "channel",
    thread_id: str | None = "444555666",
    parent_chat_id: str | None = "111222333",
    chat_topic: str | None = "ops chatter",
    user_name: str | None = "pix",
    user_id: str | None = "9001",
    guild_id: str | None = "777888999",
    message_id: str | None = "1357",
    shared_multi_user: bool = False,
    connected: list[Platform] | None = None,
    home_channels: dict | None = None,
) -> SessionContext:
    source = SessionSource(
        platform=platform,
        chat_id=chat_id,
        chat_name=chat_name,
        chat_type=chat_type,
        user_id=user_id,
        user_name=user_name,
        thread_id=thread_id,
        chat_topic=chat_topic,
        parent_chat_id=parent_chat_id,
        scope_id=guild_id,
        message_id=message_id,
    )
    connected = connected if connected is not None else [Platform.EMAIL, Platform.RELAY]
    if home_channels is None:
        home_channels = {
            Platform.EMAIL: HomeChannel(
                platform=Platform.EMAIL, chat_id="111222333", name="general"
            ),
        }
    return SessionContext(
        source=source,
        connected_platforms=connected,
        home_channels=home_channels,
        shared_multi_user_session=shared_multi_user,
    )


def _key(runner, context, redact_pii=False):
    return runner._ephemeral_change_key(context, redact_pii)  # noqa: SLF001


def _render(context, redact_pii=False):
    return build_session_context_prompt(context, redact_pii=redact_pii)


# ---------------------------------------------------------------------------
# 1. Parity: key <-> render (the maintained invariant)
# ---------------------------------------------------------------------------

class TestEphemeralChangeKeyParity:
    # Single-field mutations spanning every rendered input.  For each:
    # if the rendered bytes change, the key MUST change (staleness guard).
    _MUTATIONS = [
        ("chat_name", dict(chat_name="renamed-thread")),
        ("chat_topic", dict(chat_topic="new topic")),
        ("chat_topic_cleared", dict(chat_topic=None)),
        ("thread_id", dict(thread_id="000111222")),
        ("thread_cleared", dict(thread_id=None, parent_chat_id=None)),
        ("chat_type", dict(chat_type="group")),
        ("user_name", dict(user_name="somebody-else")),
        ("user_name_cleared", dict(user_name=None)),
        ("user_id", dict(user_name=None, user_id="1234")),
        ("shared_multi_user", dict(shared_multi_user=True)),
        ("guild_id", dict(guild_id="123123123")),
        ("parent_chat_id", dict(parent_chat_id="999000111")),
        ("chat_id", dict(chat_id="999999999", parent_chat_id="999999999")),
        ("platform", dict(platform=Platform.RELAY)),
        ("connected_platforms", dict(connected=[Platform.RELAY])),
        (
            "home_channel_renamed",
            dict(
                home_channels={
                    Platform.EMAIL: HomeChannel(
                        platform=Platform.EMAIL, chat_id="111222333", name="ops-home"
                    )
                }
            ),
        ),
        (
            "home_channel_added",
            dict(
                home_channels={
                    Platform.EMAIL: HomeChannel(
                        platform=Platform.EMAIL, chat_id="111222333", name="general"
                    ),
                    Platform.RELAY: HomeChannel(
                        platform=Platform.RELAY, chat_id="relay1", name="relay-home"
                    ),
                }
            ),
        ),
        ("message_id_cleared", dict(message_id=None)),
    ]

    def test_rendered_field_mutation_changes_key(self):
        """For every mutation of a rendered input: if the rendered bytes
        change, the key MUST change too (staleness guard)."""
        runner = _make_runner()
        base = _make_context()
        base_bytes = _render(base)
        base_key = _key(runner, base)
        for label, kwargs in self._MUTATIONS:
            mutated = _make_context(**kwargs)
            if _render(mutated) != base_bytes:
                assert _key(runner, mutated) != base_key, (
                    f"mutation {label!r} changed rendered bytes but not the key"
                )

    def test_redact_pii_flip_changes_key(self, monkeypatch):
        # PII redaction only rewrites bytes on pii-safe platforms; the key
        # must react wherever the render does.  Pin the source platform into
        # the pii-safe set so redaction actually applies regardless of whether
        # the owning plugin has registered itself in this (possibly
        # standalone) test process.
        import gateway.session as _gs

        safe = _gs._PII_SAFE_PLATFORMS | {Platform.EMAIL}
        monkeypatch.setattr(_gs, "_PII_SAFE_PLATFORMS", safe)

        runner = _make_runner()
        ctx = _make_context(platform=Platform.EMAIL, thread_id=None, parent_chat_id=None)
        assert _render(ctx, False) != _render(ctx, True)
        assert _key(runner, ctx, False) != _key(runner, ctx, True)

    def test_note_byte_stable_across_turns_in_one_session(self):
        """Within one session (gate state constant), the platform note
        must be byte-stable turn over turn — the pin returns the identical
        object, so the composed system prompt cannot drift mid-conversation."""
        runner = _make_runner()

        def _ctx():
            return _make_context(
                platform=Platform.EMAIL,
                chat_id="C123",
                thread_id=None,
                parent_chat_id=None,
                guild_id=None,
            )

        t1 = runner._pinned_session_context_prompt(_ctx(), False, "sk-email")  # noqa: SLF001
        t2 = runner._pinned_session_context_prompt(_ctx(), False, "sk-email")  # noqa: SLF001
        t3 = runner._pinned_session_context_prompt(_ctx(), False, "sk-email")  # noqa: SLF001
        assert t2 is t1 and t3 is t1
        assert hashlib.sha256(t1.encode()).hexdigest() == hashlib.sha256(t3.encode()).hexdigest()


# ---------------------------------------------------------------------------
# 2. The pin: reuse verbatim on hit, exactly one legit bust on change
# ---------------------------------------------------------------------------

class TestSessionContextPin:
    def test_pin_hit_returns_identical_object(self):
        runner = _make_runner()
        ctx = _make_context()
        first = runner._pinned_session_context_prompt(ctx, False, "sk")  # noqa: SLF001
        second = runner._pinned_session_context_prompt(_make_context(), False, "sk")  # noqa: SLF001
        # Identity, not just equality: the pinned bytes are reused verbatim,
        # immunizing against renderer nondeterminism.
        assert second is first


# ---------------------------------------------------------------------------
# 3. Two-turn byte test: composed system prompt sha256 + codex cache key
# ---------------------------------------------------------------------------

def _compose(context_prompt: str) -> str:
    """Compose base + ephemeral exactly like conversation_loop does."""
    base = "BASE IDENTITY PROMPT\n" + "x" * 8000
    return (base + "\n\n" + context_prompt).strip()


class TestComposedPromptByteStability:
    def test_turn2_equals_turn3_sha256(self):
        runner = _make_runner()
        name = "Fixing the flaky deploy"
        t2 = _compose(
            runner._pinned_session_context_prompt(  # noqa: SLF001
                _make_context(chat_name=name), False, "sk"
            )
        )
        t3 = _compose(
            runner._pinned_session_context_prompt(  # noqa: SLF001
                _make_context(chat_name=name), False, "sk"
            )
        )
        assert hashlib.sha256(t2.encode()).hexdigest() == hashlib.sha256(t3.encode()).hexdigest()


# ---------------------------------------------------------------------------
# 4. Voice-channel sidecar note: Discord voice was removed from this fork,
#    so the note helper is a no-op.
# ---------------------------------------------------------------------------

def test_voice_channel_sidecar_note_is_noop():
    runner = _make_runner()
    source = SessionSource(
        platform=Platform.EMAIL, chat_id="c1", chat_type="channel", user_id="u1"
    )
    event = _vc_event()
    assert runner._voice_channel_sidecar_note(event, source, "sk") is None  # noqa: SLF001


def _vc_event():
    from types import SimpleNamespace

    return SimpleNamespace(raw_message=SimpleNamespace(guild_id="777"))


# ---------------------------------------------------------------------------
# 5. Sidecar note staging: one-shot per turn
# ---------------------------------------------------------------------------

class TestSidecarNoteStaging:
    def test_set_then_consume_once(self):
        runner = _make_runner()
        runner._set_pending_turn_sidecar_notes("sk", ["[System note: reset]"])  # noqa: SLF001
        assert runner._consume_pending_turn_sidecar_notes("sk") == ["[System note: reset]"]  # noqa: SLF001
        assert runner._consume_pending_turn_sidecar_notes("sk") == []  # noqa: SLF001


# ---------------------------------------------------------------------------
# 6. Connected platforms: stable order
# ---------------------------------------------------------------------------

class TestConnectedPlatformsOrder:
    def test_sorted_regardless_of_insertion_order(self):
        cfg_a = GatewayConfig(
            platforms={
                Platform.EMAIL: PlatformConfig(enabled=True, token="e"),
                Platform.LOCAL: PlatformConfig(enabled=True, token="l"),
            }
        )
        cfg_b = GatewayConfig(
            platforms={
                Platform.LOCAL: PlatformConfig(enabled=True, token="l"),
                Platform.EMAIL: PlatformConfig(enabled=True, token="e"),
            }
        )
        assert cfg_a.get_connected_platforms() == cfg_b.get_connected_platforms()
        values = [p.value for p in cfg_a.get_connected_platforms()]
        assert values == sorted(values)
