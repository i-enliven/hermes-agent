"""Pairing store <-> allowlist consolidation (#23778).

Design (union + option-i mirror):
  * A pairing-store entry is a first-class authorization grant. A paired user
    is authorized regardless of any configured allowlist (union), because
    ``approve_code`` is reachable only by the trusted operator (CLI/dashboard),
    never by an inbound sender.
  * When an allowlist IS already configured for the platform, approving a
    pairing code ALSO writes the user into that allowlist env var (and revoking
    removes them), so the two stay a single operator-visible source of truth.
  * On an open gateway (no allowlist configured) approval does NOT create an
    allowlist — that would silently lock an open gateway. The pairing store
    remains the grant record, honored by the authz union.

Platform-removal cleanup note: the original suite exercised the removed
chat-messenger adapters (device-suffix JID aliasing, snapshot-vs-env
re-reads). Those platform-specific alias semantics no longer exist, so the
tests were re-pathed to the retained EMAIL platform, which owns
EMAIL_ALLOWED_USERS in the pairing mirror map. The live-adapter semantics
(revoke must deny immediately without restart) are covered by a local stub
adapter implementing the same live allowlist behavior the gateway authz
mixin delegates to.
"""

import os
from types import SimpleNamespace

import pytest

from gateway.session import Platform, SessionSource


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    for var in (
        "EMAIL_ALLOWED_USERS",
        "EMAIL_ALLOW_ALL_USERS",
        "GATEWAY_ALLOW_ALL_USERS",
        "GATEWAY_ALLOWED_USERS",
    ):
        monkeypatch.delenv(var, raising=False)


# --------------------------------------------------------------------------
# authz union: a paired user is authorized regardless of the allowlist
# --------------------------------------------------------------------------

def _make_runner(*, paired: bool):
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.pairing_store = SimpleNamespace(is_approved=lambda *_a, **_kw: paired)
    return runner


def _make_source(user_id: str = "pairme", chat_type: str = "dm"):
    return SessionSource(
        platform=Platform.EMAIL,
        chat_id="123",
        chat_type=chat_type,
        user_id=user_id,
        user_name="SomeHuman",
        is_bot=False,
    )


def test_paired_user_authorized_even_when_not_in_allowlist(monkeypatch):
    """Union semantics: pairing is a grant, honored alongside the allowlist."""
    runner = _make_runner(paired=True)
    monkeypatch.setenv("EMAIL_ALLOWED_USERS", "owner1,owner2")

    assert runner._is_user_authorized(_make_source("pairme")) is True


def test_unpaired_user_in_allowlist_still_authorized(monkeypatch):
    runner = _make_runner(paired=False)
    monkeypatch.setenv("EMAIL_ALLOWED_USERS", "owner1")

    assert runner._is_user_authorized(_make_source("owner1")) is True


# --------------------------------------------------------------------------
# B2 mirror: approval writes into the allowlist iff one is configured
# --------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path, monkeypatch):
    """A real PairingStore backed by a temp pairing dir."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    (tmp_path / ".hermes").mkdir(parents=True, exist_ok=True)
    import importlib

    import gateway.pairing as pairing_mod
    importlib.reload(pairing_mod)
    return pairing_mod.PairingStore()


def _approve_new_user(store, platform, user_id, user_name=""):
    code = store.generate_code(platform, user_id, user_name)
    assert code is not None
    return store.approve_code(platform, code)


def test_approval_adds_to_configured_allowlist(store, monkeypatch):
    """When an allowlist exists, approval appends the user to it (option i)."""
    monkeypatch.setenv("EMAIL_ALLOWED_USERS", "owner1")
    # save_env_value writes to .env under HERMES_HOME; patch it to capture.
    captured = {}
    import hermes_cli.config as cfg

    monkeypatch.setattr(cfg, "save_env_value",
                        lambda k, v: (captured.__setitem__(k, v),
                                      os.environ.__setitem__(k, v)))

    _approve_new_user(store, "email", "newuser99")

    assert captured.get("EMAIL_ALLOWED_USERS") == "owner1,newuser99"


def test_revoke_removes_from_allowlist(store, monkeypatch):
    monkeypatch.setenv("EMAIL_ALLOWED_USERS", "owner1,newuser99")
    saved = {}
    removed = []
    import hermes_cli.config as cfg

    monkeypatch.setattr(cfg, "save_env_value",
                        lambda k, v: (saved.__setitem__(k, v),
                                      os.environ.__setitem__(k, v)))
    monkeypatch.setattr(cfg, "remove_env_value", lambda k: removed.append(k))
    # Seed the approved list directly so revoke has something to remove.
    store._approve_user("email", "newuser99", "")

    assert store.revoke("email", "newuser99") is True
    assert saved.get("EMAIL_ALLOWED_USERS") == "owner1"


def test_revoke_sole_entry_denies_live_adapter_without_restart(
    store, monkeypatch,
):
    """Sole allowlist entry revoke must deny immediately on a live gateway.

    Persistence alone is not enough: adapters snapshot ``_allow_from`` at
    construction, and authz trusts ``dm_policy=allowlist`` when the env
    allowlist is gone. After revoke, intake and ``_is_user_authorized`` must
    both deny the sender without restarting the gateway.

    Re-pathed from the removed chat-messenger adapter: the stub below
    implements
    the same live allowlist semantics (env re-read, config-snapshot
    precedence, purgeable ``_allow_from``) that the gateway's revoke path
    (``_sync_live_adapter_allowlist_remove``) and the authz mixin rely on.
    """
    from gateway.config import GatewayConfig, PlatformConfig
    from gateway.run import GatewayRunner
    import gateway.run as gateway_run
    import hermes_cli.config as cfg

    monkeypatch.setenv("EMAIL_ALLOWED_USERS", "15551234567")
    monkeypatch.setattr(
        cfg,
        "save_env_value",
        lambda k, v: os.environ.__setitem__(k, v),
    )
    monkeypatch.setattr(
        cfg,
        "remove_env_value",
        lambda k: (os.environ.pop(k, None), True)[1],
    )

    class LiveAllowlistAdapter:
        """Stands in for a live own-policy adapter on the email platform."""

        enforces_own_access_policy = True

        def __init__(self):
            self.config = SimpleNamespace(
                extra={
                    "dm_policy": "allowlist",
                    "allow_from": ["15551234567"],
                }
            )
            self.platform = Platform.EMAIL
            self._dm_policy = "allowlist"
            self._dm_allowlist_source = "config"
            self._allow_from = {"15551234567"}

        def _live_dm_allow_from(self):
            """Re-read the env allowlist live (no construction snapshot)."""
            raw = (os.getenv("EMAIL_ALLOWED_USERS") or "").strip()
            return {uid.strip() for uid in raw.split(",") if uid.strip()}

        def _is_dm_intake_allowed(self, sender):
            return sender in self._live_dm_allow_from()

        def _is_dm_allowed(self, sender):
            return sender in self._live_dm_allow_from()

    adapter = LiveAllowlistAdapter()
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={
            Platform.EMAIL: PlatformConfig(
                enabled=True,
                extra={"dm_policy": "allowlist", "allow_from": ["15551234567"]},
            )
        }
    )
    runner.adapters = {Platform.EMAIL: adapter}
    runner.pairing_store = store
    runner.pairing_stores = {}
    monkeypatch.setattr(gateway_run, "_gateway_runner_ref", lambda: runner)

    store._approve_user("email", "15551234567", "")
    sender = "15551234567"
    assert adapter._is_dm_intake_allowed(sender) is True
    assert runner._is_user_authorized(
        SessionSource(
            platform=Platform.EMAIL,
            user_id=sender,
            chat_id=sender,
            user_name="revoked",
            chat_type="dm",
        )
    ) is True

    assert store.revoke("email", sender) is True
    assert store.is_approved("email", sender) is False
    assert os.environ.get("EMAIL_ALLOWED_USERS") in (None, "")
    assert "15551234567" not in (adapter._allow_from or set())
    assert adapter._is_dm_intake_allowed(sender) is False
    assert adapter._is_dm_allowed(sender) is False
    assert runner._is_user_authorized(
        SessionSource(
            platform=Platform.EMAIL,
            user_id=sender,
            chat_id=sender,
            user_name="revoked",
            chat_type="dm",
        )
    ) is False
