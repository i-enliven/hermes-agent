"""#64674 — multiplex primary gateway must not fail forever without bot tokens.

When gateway.multiplex_profiles is on and a platform token lives only in a
secondary profile's .env, the default-profile primary adapter used to start
with an empty token, log "No bot token configured", and queue an infinite
reconnect loop. Secondary profiles already load under _profile_runtime_scope;
this suite locks the complementary primary-path fixes:

1. load_gateway_config_for_runner reloads under the default profile secret scope
   when multiplex is on (so default .env tokens resolve like secondary loads).

Fork note: this fork deleted all token-based third-party messengers, so
``PLATFORM_TOKEN_ENV_NAMES`` is empty and ``_platform_has_bot_credential``
always returns True. The primary-startup empty-token skip (item 2) and the
reconnect empty-token drop (item 3) are inert for every retained platform
(email/api_server/webhook/msgraph_webhook/relay authenticate via other means),
and their dedicated tests were pruned.
"""
from __future__ import annotations

import pytest

from gateway.config import GatewayConfig, Platform


class TestLoadGatewayConfigForRunner:
    def test_unscoped_when_multiplex_off(self, tmp_path, monkeypatch):
        from gateway import run as run_mod

        home = tmp_path / "home"
        home.mkdir()
        (home / ".env").write_text("API_SERVER_KEY=from-default-env\n", encoding="utf-8")
        (home / "config.yaml").write_text("gateway:\n  multiplex_profiles: false\n", encoding="utf-8")
        monkeypatch.setenv("HERMES_HOME", str(home))
        monkeypatch.delenv("API_SERVER_KEY", raising=False)

        # Without multiplex, dotenv is still loaded into os.environ by the
        # normal env loader in real gateways; here we only assert the helper
        # returns a non-multiplex config without requiring a scope.
        cfg = run_mod.load_gateway_config_for_runner()
        assert cfg.multiplex_profiles is False

    def test_scoped_reload_still_sees_container_api_server_env(self, tmp_path, monkeypatch):
        """#69379 — container-env API_SERVER_* visible during the scoped reload.

        Docker/systemd deployments enable the api_server platform via the
        process environment (compose ``environment:`` block), not the profile
        ``.env``. The multiplex runner reload happens inside the default
        profile's secret scope; the listener settings are on the global
        allowlist (deployment config, not profile secrets) so they must stay
        visible there — while API_SERVER_KEY (a credential) still resolves
        through the profile scope.
        """
        from agent import secret_scope as ss
        from gateway import run as run_mod

        home = tmp_path / "home"
        home.mkdir()
        # Credentials belong in the profile .env; listener settings do not.
        (home / ".env").write_text(
            "API_SERVER_KEY=profile-scoped-key-0123456789abcdef\n",
            encoding="utf-8",
        )
        (home / "config.yaml").write_text(
            "gateway:\n  multiplex_profiles: true\n", encoding="utf-8"
        )
        monkeypatch.setenv("HERMES_HOME", str(home))
        # Listener settings live ONLY in os.environ — the Docker compose case.
        monkeypatch.setenv("API_SERVER_ENABLED", "true")
        monkeypatch.setenv("API_SERVER_HOST", "0.0.0.0")
        monkeypatch.setenv("API_SERVER_PORT", "8642")
        monkeypatch.delenv("API_SERVER_KEY", raising=False)
        monkeypatch.setattr(run_mod, "get_hermes_home", lambda: home)
        monkeypatch.setattr(run_mod, "_hermes_home", home)
        # Model the real multiplexed gateway: run.py flips the runtime flag
        # before the runner reload, making any installed scope authoritative.
        ss.set_multiplex_active(True)

        cfg = run_mod.load_gateway_config_for_runner()

        assert cfg.multiplex_profiles is True
        # api_server present: key from the profile scope, listener settings
        # from the container environment via the global allowlist.
        api = cfg.platforms.get(Platform.API_SERVER)
        assert api is not None, (
            "api_server should be enabled from container env even inside "
            "the scoped runner reload (#69379)"
        )
        assert api.enabled is True
        assert api.extra.get("key") == "profile-scoped-key-0123456789abcdef"
        assert api.extra.get("host") == "0.0.0.0"
        assert api.extra.get("port") == 8642
