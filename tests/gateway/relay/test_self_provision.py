"""Unit tests for relay connector config readers + provision primitives.

Covers the generic relay connector contract: the ``_provision_url`` URL
derivation, the ``relay_connection_auth`` credential reader, and the
``relay_display_name`` brand suppression. The connector HTTP POST is
monkeypatched (the cross-repo E2E exercises the real /relay/provision); these
prove the in-process env wiring and config-reader behaviour.
"""

from __future__ import annotations

import pytest

import gateway.relay as relay


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in (
        "GATEWAY_RELAY_URL",
        "GATEWAY_RELAY_ID",
        "GATEWAY_RELAY_SECRET",
        "GATEWAY_RELAY_DELIVERY_KEY",
        "GATEWAY_RELAY_ENDPOINT",
        "GATEWAY_RELAY_ROUTE_KEYS",
        "GATEWAY_RELAY_PLATFORM",
        "GATEWAY_RELAY_BOT_ID",
    ):
        monkeypatch.delenv(k, raising=False)
    # Never read config.yaml off disk in these tests.
    monkeypatch.setattr("gateway.run._load_gateway_config", lambda: {}, raising=False)


# ─────────────────────────── config readers ───────────────────────────


def test_provision_url_maps_ws_to_http():
    assert relay._provision_url("wss://c.example/relay") == "https://c.example/relay/provision"
    assert relay._provision_url("ws://c.example/relay") == "http://c.example/relay/provision"
    assert relay._provision_url("https://c.example") == "https://c.example/relay/provision"


def test_connection_auth_pinned_secret():
    """A gateway with a pinned per-gateway secret authenticates with it."""
    import os
    os.environ["GATEWAY_RELAY_ID"] = "gw-pinned"
    os.environ["GATEWAY_RELAY_SECRET"] = "deadbeef"
    assert relay.relay_connection_auth() == ("gw-pinned", "deadbeef")


# ─────────────────────────── displayName (Phase 1 parity, gg#171) ───────────────────────────


def test_relay_display_name_suppresses_stock_brand(monkeypatch):
    """The default 'Hermes Agent' brand is identical on every install — forwarding
    it would shadow the connector's linked-owner fallback (which actually
    disambiguates) with a uniform label. Only customized names are forwarded."""
    import os
    os.environ.pop("GATEWAY_RELAY_DISPLAY_NAME", None)

    class _Skin:
        def get_branding(self, key, fallback=""):
            return "Hermes Agent" if key == "agent_name" else fallback

    monkeypatch.setattr("hermes_cli.skin_engine.get_active_skin", lambda: _Skin())
    assert relay.relay_display_name() is None
