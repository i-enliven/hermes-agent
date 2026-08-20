"""Cron delivery for relay-fronted logical platforms.

Bug report: a deployment where email is fronted by the relay connector
(``GATEWAY_RELAY_PLATFORMS=email``) could not use ``deliver='email'``:

1. Resolution read ONLY the legacy ``EMAIL_HOME_ADDRESS`` env mirror, never
   the canonical ``platforms.email.home_channel`` block that ``/sethome``
   persists to config.yaml — so the target silently resolved to nothing and
   the job fell back to local-only.
2. Even with a resolved target, the delivery loop's native
   ``pconfig.enabled`` gate rejected the platform ("not configured/enabled")
   although ``resolve_delivery_transport`` had already produced a live relay
   transport that fronts it — a relay-fronted logical platform is
   deliberately NOT natively enabled (its credential lives in the connector).
"""

import asyncio
from concurrent.futures import Future
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cron import scheduler as sched
from cron.scheduler import (
    _deliver_result,
    _get_home_target_chat_id,
    _get_home_target_thread_id,
    _resolve_delivery_targets,
)
from gateway.config import HomeChannel, Platform


def _gateway_config_with_home(platform=Platform.EMAIL, chat_id="1517373704248758474",
                              thread_id=None):
    """A gateway config whose ONLY home-channel source is config.yaml."""
    home = HomeChannel(platform=platform, chat_id=chat_id, name="Home",
                      thread_id=thread_id)
    config = MagicMock()
    config.platforms = {}
    config.get_home_channel = lambda p: home if p == platform else None
    return config


def _clear_home_env(monkeypatch):
    for var in ("EMAIL_HOME_ADDRESS", "EMAIL_HOME_ADDRESS_THREAD_ID"):
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# Resolution: config.yaml home_channel fallback
# ---------------------------------------------------------------------------

class TestConfigHomeChannelFallback:
    def test_chat_id_falls_back_to_config_home_channel(self, monkeypatch):
        """Env mirror empty → the canonical config.yaml home_channel is used."""
        _clear_home_env(monkeypatch)
        with patch("gateway.config.load_gateway_config",
                   return_value=_gateway_config_with_home()):
            assert _get_home_target_chat_id("email") == "1517373704248758474"

    def test_env_mirror_still_wins_over_config(self, monkeypatch):
        """Operator env override keeps precedence over the config block."""
        monkeypatch.setenv("EMAIL_HOME_ADDRESS", "999")
        with patch("gateway.config.load_gateway_config",
                   return_value=_gateway_config_with_home()):
            assert _get_home_target_chat_id("email") == "999"

    def test_thread_id_falls_back_to_config_home_channel(self, monkeypatch):
        _clear_home_env(monkeypatch)
        with patch("gateway.config.load_gateway_config",
                   return_value=_gateway_config_with_home(thread_id="777")):
            assert _get_home_target_thread_id("email") == "777"

    def test_config_thread_not_used_when_chat_came_from_env(self, monkeypatch):
        """Thread affinity: a config thread_id must not be grafted onto an
        env-provided chat id (they may point at different conversations)."""
        monkeypatch.setenv("EMAIL_HOME_ADDRESS", "999")
        monkeypatch.delenv("EMAIL_HOME_ADDRESS_THREAD_ID", raising=False)
        with patch("gateway.config.load_gateway_config",
                   return_value=_gateway_config_with_home(thread_id="777")):
            assert _get_home_target_thread_id("email") is None

    def test_no_source_returns_empty(self, monkeypatch):
        _clear_home_env(monkeypatch)
        config = MagicMock()
        config.platforms = {}
        config.get_home_channel = lambda p: None
        with patch("gateway.config.load_gateway_config", return_value=config):
            assert _get_home_target_chat_id("email") == ""

    def test_config_load_failure_fails_safe(self, monkeypatch):
        _clear_home_env(monkeypatch)
        with patch("gateway.config.load_gateway_config",
                   side_effect=RuntimeError("boom")):
            assert _get_home_target_chat_id("email") == ""

    def test_deliver_email_resolves_via_config_home(self, monkeypatch):
        """End to end: deliver='email' on a job with no origin resolves a
        concrete target from the config.yaml home_channel alone."""
        _clear_home_env(monkeypatch)
        job = {"id": "j1", "deliver": "email"}
        with patch("gateway.config.load_gateway_config",
                   return_value=_gateway_config_with_home()):
            targets = _resolve_delivery_targets(job)
        assert targets == [{
            "platform": "email",
            "chat_id": "1517373704248758474",
            "thread_id": None,
        }]


# ---------------------------------------------------------------------------
# Delivery: relay transport must bypass the native enabled gate
# ---------------------------------------------------------------------------

class TestRelayDeliveryGate:
    def _relay_adapter(self):
        adapter = AsyncMock()
        adapter.fronts_platform = lambda p: p == Platform.EMAIL
        return adapter

    def _job(self):
        return {
            "id": "relay-job",
            "name": "Relay Job",
            "deliver": "email",
            "origin": {"platform": "email", "chat_id": "123"},
        }

    def _run(self, adapters, gateway_config):
        loop = MagicMock()
        loop.is_running.return_value = True

        def fake_run_coro(coro, _loop):
            future = Future()
            try:
                future.set_result(asyncio.run(coro))
            except BaseException as e:  # noqa: BLE001
                future.set_exception(e)
            return future

        router = MagicMock()

        async def _deliver_to_platform(target, content, metadata):
            return {"success": True, "raw_response": None}

        router._deliver_to_platform = _deliver_to_platform

        with patch("gateway.config.load_gateway_config",
                   return_value=gateway_config), \
             patch("cron.scheduler.load_config",
                   return_value={"cron": {"wrap_response": False}}), \
             patch("gateway.delivery.DeliveryRouter", return_value=router), \
             patch("asyncio.run_coroutine_threadsafe", side_effect=fake_run_coro):
            return _deliver_result(self._job(), "Nightly report.",
                                   adapters=adapters, loop=loop)

    def test_relay_fronted_platform_is_not_rejected(self, monkeypatch):
        """A live relay transport that fronts email must deliver even though
        platforms.email has no native config block at all."""
        _clear_home_env(monkeypatch)
        config = MagicMock()
        config.platforms = {}  # neither email nor relay configured natively
        config.get_home_channel = lambda p: None
        result = self._run({Platform.RELAY: self._relay_adapter()}, config)
        assert result is None  # None == delivered without errors

    def test_native_gate_preserved_without_relay(self, monkeypatch):
        """No relay transport → the historical configured/enabled gate stays."""
        _clear_home_env(monkeypatch)
        config = MagicMock()
        config.platforms = {}
        config.get_home_channel = lambda p: None
        result = self._run({}, config)
        assert result is not None
        assert "not configured/enabled" in result
