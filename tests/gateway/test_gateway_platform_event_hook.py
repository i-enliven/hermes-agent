"""Tests for the ``gateway_platform_event`` observer hook (#64176's observer half).

Covers the normalized-envelope pattern that replaces raw-SDK handler args:
* only ``gateway_platform_event`` is registered in ``VALID_HOOKS`` (no inert
  hook surface without a concrete fire site)
* the adapter forwards normalized events to a runner-owned callback; the runner
  performs the authoritative post-auth check before invoking plugins
* ``_handle_gateway_platform_event`` fires ``gateway_platform_event`` with a
  stable ``{platform, event_type, payload}`` envelope, gated on the same
  authorization decision as inbound gateway traffic (unauthorized events never
  fire), and swallows errors so the observer can't break the adapter
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gateway.run import GatewayRunner
from hermes_cli.plugins import VALID_HOOKS

_repo = str(Path(__file__).resolve().parents[2])
if _repo not in sys.path:
    sys.path.insert(0, _repo)


def _source(user_id="777"):
    """A minimal inbound event source carrying a user id for auth gating."""
    return SimpleNamespace(user_id=user_id)


@pytest.fixture(autouse=True)
def _observer_available(monkeypatch):
    """Most fire-site tests exercise the subscribed path explicitly."""
    monkeypatch.setattr("hermes_cli.lifecycle.has_hook", lambda _name: True)


class TestHookRegistration:
    def test_gateway_platform_event_registered_reserved_absent(self):
        """register_hook rejects names not in VALID_HOOKS, so the implemented
        hook must be present. The reserved gateway_* names are deliberately
        absent (no inert surface without a concrete fire site); lock that in."""
        assert "gateway_platform_event" in VALID_HOOKS
        assert "gateway_session_titled" not in VALID_HOOKS
        assert "gateway_message_delivered" not in VALID_HOOKS
        assert "gateway_thread_created" not in VALID_HOOKS


class TestRunnerDispatch:
    def test_authorized_event_routes_normalized_envelope(self):
        runner = object.__new__(GatewayRunner)
        runner._is_user_authorized = lambda source: source.user_id == "777"
        invoke = MagicMock()
        source = _source()
        event = {
            "platform": "email",
            "event_type": "reaction",
            "payload": {"chat_id": "123", "message_id": "456", "emojis": ["x"]},
        }

        with patch("hermes_cli.lifecycle.invoke_hook", invoke):
            asyncio.run(runner._handle_gateway_platform_event(event, source))

        invoke.assert_called_once_with("gateway_platform_event", **event)

    def test_unauthorized_event_never_reaches_hooks(self):
        runner = object.__new__(GatewayRunner)
        runner._is_user_authorized = lambda source: False
        invoke = MagicMock()
        source = _source()

        with patch("hermes_cli.lifecycle.invoke_hook", invoke):
            asyncio.run(runner._handle_gateway_platform_event(
                {"platform": "email", "event_type": "reaction", "payload": {}},
                source,
            ))

        invoke.assert_not_called()

    def test_skips_dispatch_when_no_subscriber(self):
        runner = object.__new__(GatewayRunner)
        authorized = MagicMock(return_value=True)
        runner._is_user_authorized = authorized
        invoke = MagicMock()
        source = _source()

        with patch("hermes_cli.lifecycle.has_hook", return_value=False), patch(
            "hermes_cli.lifecycle.invoke_hook", invoke
        ):
            asyncio.run(runner._handle_gateway_platform_event(
                {"platform": "email", "event_type": "reaction", "payload": {}},
                source,
            ))

        authorized.assert_not_called()
        invoke.assert_not_called()

    def test_plugin_layer_error_is_isolated(self):
        runner = object.__new__(GatewayRunner)
        runner._is_user_authorized = lambda source: True
        invoke = MagicMock(side_effect=RuntimeError("plugin boom"))
        source = _source()

        with patch("hermes_cli.lifecycle.invoke_hook", invoke):
            asyncio.run(runner._handle_gateway_platform_event(
                {"platform": "email", "event_type": "reaction", "payload": {}},
                source,
            ))  # no raise
