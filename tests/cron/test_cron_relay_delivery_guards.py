"""Fire-time guards: stale Slack creation-thread routing + relay-fronted preflight.

Two related defects on relay-fronted deployments:

1. Jobs persisted before the synthetic-thread capture fix carry the creation
   message's own id as ``origin.thread_id``. At fire time ``deliver=origin``
   replayed it unconditionally, and the Slack origin-affinity re-attach put it
   back even on explicit ``slack:<chat_id>`` targets. Guard: when the resolved
   Slack chat IS the configured home chat, the origin thread is a stale
   per-message artifact — deliver top-level (home thread config still wins).

2. ``_preflight_check_delivery`` validated the ``slack:`` prefix against
   natively-configured platforms only; in relay-only topology that set is
   ``{relay}`` and the job was refused with "no gateway credentials configured"
   although fire-time routing (resolve_delivery_transport + fronts_platform)
   would have delivered it. Preflight must consult the relay's fronted set.

Platform-removal note: the relay-fronted preflight cases use ``teams`` (a
retained dynamic plugin platform) as the logical fronted platform and
``webhook`` (retained built-in) as a foreign platform, since the original
slack/telegram/discord stand-ins were removed from the Platform enum. The
stale-thread guard (case 1) is inherently Slack-specific and is kept as-is:
the guard code in cron/scheduler.py is still slack-scoped.
"""

from unittest.mock import MagicMock, patch

import pytest

from cron import scheduler as sched
from cron.scheduler import (
    _preflight_check_delivery,
    _resolve_single_delivery_target,
    cron_delivery_targets,
)


def _slack_home(monkeypatch, chat_id="D0BJTDCSR7C", thread_id=None):
    monkeypatch.setattr(sched, "_get_home_target_chat_id",
                        lambda p: chat_id if p == "slack" else None)
    monkeypatch.setattr(sched, "_get_home_target_thread_id",
                        lambda p: thread_id if p == "slack" else None)


SYNTH = "1755043010.123456"


class TestOriginThreadStaleGuard:
    def test_origin_thread_dropped_when_chat_is_home(self, monkeypatch):
        """deliver=origin, slack origin chat == home chat: creation thread is stale."""
        _slack_home(monkeypatch)
        job = {"origin": {"platform": "slack", "chat_id": "D0BJTDCSR7C",
                          "thread_id": SYNTH}}
        target = _resolve_single_delivery_target(job, "origin")
        assert target == {"platform": "slack", "chat_id": "D0BJTDCSR7C",
                          "thread_id": None}

    def test_origin_thread_kept_when_chat_not_home(self, monkeypatch):
        """A non-home Slack origin thread may be a genuine working thread: keep it."""
        _slack_home(monkeypatch, chat_id="D_OTHER_HOME")
        job = {"origin": {"platform": "slack", "chat_id": "C0AGENERAL",
                          "thread_id": "1755040000.000100"}}
        target = _resolve_single_delivery_target(job, "origin")
        assert target["thread_id"] == "1755040000.000100"

    def test_home_thread_config_still_wins(self, monkeypatch):
        """When the home target itself pins a thread, deliver there, not top-level."""
        _slack_home(monkeypatch, thread_id="1755000000.000001")
        job = {"origin": {"platform": "slack", "chat_id": "D0BJTDCSR7C",
                          "thread_id": SYNTH}}
        target = _resolve_single_delivery_target(job, "origin")
        assert target["thread_id"] == "1755000000.000001"

    def test_non_slack_origin_thread_untouched(self, monkeypatch):
        """Telegram forum-topic origins replay their thread verbatim."""
        _slack_home(monkeypatch)
        job = {"origin": {"platform": "telegram", "chat_id": "-1003941067111",
                          "thread_id": "2203"}}
        target = _resolve_single_delivery_target(job, "origin")
        assert target["thread_id"] == "2203"

    def test_explicit_target_no_reattach_when_chat_is_home(self, monkeypatch):
        """slack:<home_chat> must not inherit the stale creation thread."""
        _slack_home(monkeypatch)
        monkeypatch.setattr(
            "tools.send_message_tool.prepare_send_message_platforms", lambda: None)
        monkeypatch.setattr(
            "tools.send_message_tool.resolve_send_target",
            lambda platform, rest: (rest, None, None))
        job = {"origin": {"platform": "slack", "chat_id": "D0BJTDCSR7C",
                          "thread_id": SYNTH}}
        target = _resolve_single_delivery_target(job, "slack:D0BJTDCSR7C")
        assert target["thread_id"] is None

    def test_explicit_target_reattach_kept_for_non_home_chat(self, monkeypatch):
        """Origin-affinity re-attach is preserved for genuine non-home threads."""
        _slack_home(monkeypatch, chat_id="D_OTHER_HOME")
        monkeypatch.setattr(
            "tools.send_message_tool.prepare_send_message_platforms", lambda: None)
        monkeypatch.setattr(
            "tools.send_message_tool.resolve_send_target",
            lambda platform, rest: (rest, None, None))
        job = {"origin": {"platform": "slack", "chat_id": "C0AGENERAL",
                          "thread_id": "1755040000.000100"}}
        target = _resolve_single_delivery_target(job, "slack:C0AGENERAL")
        assert target["thread_id"] == "1755040000.000100"


def _gateway_config(connected_values):
    config = MagicMock()
    config.get_connected_platforms.return_value = [
        MagicMock(value=v) for v in connected_values
    ]
    return config


class TestPreflightRelayFronted:
    """Relay-fronted preflight, re-pathed to retained platforms: ``teams``
    (dynamic plugin platform) is the logical fronted platform, ``webhook``
    (built-in) stands in for a foreign one."""

    def test_relay_fronted_teams_accepted(self, monkeypatch):
        """Relay-only topology fronting teams: teams:CHAT passes preflight."""
        monkeypatch.setenv("GATEWAY_RELAY_PLATFORMS", "teams")
        with patch("gateway.config.load_gateway_config",
                   return_value=_gateway_config({"relay"})):
            assert _preflight_check_delivery(
                {"deliver": "teams:19:guid-abc"}) is None

    def test_unfronted_platform_still_rejected(self, monkeypatch):
        """The relay fronting teams does not whitelist other platforms."""
        monkeypatch.setenv("GATEWAY_RELAY_PLATFORMS", "teams")
        with patch("gateway.config.load_gateway_config",
                   return_value=_gateway_config({"relay"})):
            reason = _preflight_check_delivery({"deliver": "webhook:12345"})
            assert reason is not None
            assert "webhook" in reason

    def test_native_strictness_without_relay(self, monkeypatch):
        """No relay configured: the native credential check is unchanged."""
        monkeypatch.delenv("GATEWAY_RELAY_PLATFORMS", raising=False)
        with patch("gateway.config.load_gateway_config",
                   return_value=_gateway_config({"email"})):
            reason = _preflight_check_delivery(
                {"deliver": "teams:19:guid-abc"})
            assert reason is not None
            assert "teams" in reason

    def test_delivery_targets_include_relay_fronted(self, monkeypatch):
        """The UI dropdown source offers relay-fronted platforms."""
        monkeypatch.setenv("GATEWAY_RELAY_PLATFORMS", "teams")
        _slack_home(monkeypatch)
        monkeypatch.setattr(sched, "_iter_home_target_platforms",
                            lambda: ["teams", "webhook"])
        with patch("gateway.config.load_gateway_config",
                   return_value=_gateway_config({"relay"})):
            ids = {t["id"] for t in cron_delivery_targets()}
        assert "teams" in ids
        assert "webhook" not in ids
