from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent
from gateway.session import SessionSource


def _clear_auth_env(monkeypatch) -> None:
    for key in (
        "TELEGRAM_ALLOWED_USERS",
        "TELEGRAM_GROUP_ALLOWED_USERS",
        "DISCORD_ALLOWED_USERS",
        "WHATSAPP_ALLOWED_USERS",
        "SLACK_ALLOWED_USERS",
        "SIGNAL_ALLOWED_USERS",
        "SIGNAL_GROUP_ALLOWED_USERS",
        "TELEGRAM_GROUP_ALLOWED_CHATS",
        "EMAIL_ALLOWED_USERS",
        "SMS_ALLOWED_USERS",
        "MATTERMOST_ALLOWED_USERS",
        "MATRIX_ALLOWED_USERS",
        "DINGTALK_ALLOWED_USERS", "FEISHU_ALLOWED_USERS", "WECOM_ALLOWED_USERS",
        "QQ_ALLOWED_USERS", "QQ_GROUP_ALLOWED_USERS",
        "GATEWAY_ALLOWED_USERS",
        "TELEGRAM_ALLOW_ALL_USERS",
        "DISCORD_ALLOW_ALL_USERS",
        "WHATSAPP_ALLOW_ALL_USERS",
        "SLACK_ALLOW_ALL_USERS",
        "SIGNAL_ALLOW_ALL_USERS",
        "EMAIL_ALLOW_ALL_USERS",
        "SMS_ALLOW_ALL_USERS",
        "MATTERMOST_ALLOW_ALL_USERS",
        "MATRIX_ALLOW_ALL_USERS",
        "DINGTALK_ALLOW_ALL_USERS", "FEISHU_ALLOW_ALL_USERS", "WECOM_ALLOW_ALL_USERS",
        "QQ_ALLOW_ALL_USERS",
        "GATEWAY_ALLOW_ALL_USERS",
    ):
        monkeypatch.delenv(key, raising=False)


def _make_event(
    platform: Platform,
    user_id: str,
    chat_id: str,
    *,
    profile: str | None = None,
) -> MessageEvent:
    return MessageEvent(
        text="hello",
        message_id="m1",
        source=SessionSource(
            platform=platform,
            user_id=user_id,
            chat_id=chat_id,
            user_name="tester",
            chat_type="dm",
            profile=profile,
        ),
    )


def _make_runner(platform: Platform, config: GatewayConfig):
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.config = config
    adapter = SimpleNamespace(send=AsyncMock())
    runner.adapters = {platform: adapter}
    runner.pairing_store = MagicMock()
    runner.pairing_store.is_approved.return_value = False
    runner.pairing_store._is_rate_limited.return_value = False
    # Attributes required by _handle_message for the authorized-user path
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._update_prompts = {}
    runner.hooks = SimpleNamespace(dispatch=AsyncMock(return_value=None))
    runner._sessions = {}
    return runner, adapter


@pytest.mark.asyncio
async def test_local_unauthorized_dm_pairs_by_default(monkeypatch):
    """LOCAL is the retained pair-default chat platform in this fork.

    Without any allowlist, an unauthorized DM pairs: the pairing code is
    generated and sent to the sender.
    """
    _clear_auth_env(monkeypatch)
    config = GatewayConfig(
        platforms={Platform.LOCAL: PlatformConfig(enabled=True)},
    )
    runner, adapter = _make_runner(Platform.LOCAL, config)
    runner.pairing_store.generate_code.return_value = "ABC12DEF"

    result = await runner._handle_message(
        _make_event(
            Platform.LOCAL,
            "15551234567",
            "15551234567",
        )
    )

    assert result is None
    runner.pairing_store.generate_code.assert_called_once_with(
        "local",
        "15551234567",
        "tester",
    )
    adapter.send.assert_awaited_once()
    assert "ABC12DEF" in adapter.send.await_args.args[1]


@pytest.mark.asyncio
async def test_unauthorized_whatsapp_dm_can_be_ignored(monkeypatch):
    _clear_auth_env(monkeypatch)
    config = GatewayConfig(
        platforms={
            Platform.EMAIL: PlatformConfig(
                enabled=True,
                extra={"unauthorized_dm_behavior": "ignore"},
            ),
        },
    )
    runner, adapter = _make_runner(Platform.EMAIL, config)

    result = await runner._handle_message(
        _make_event(
            Platform.EMAIL,
            "15551234567@s.whatsapp.net",
            "15551234567@s.whatsapp.net",
        )
    )

    assert result is None
    runner.pairing_store.generate_code.assert_not_called()
    adapter.send.assert_not_awaited()


# ---------------------------------------------------------------------------
# Allowlist-configured platforms default to "ignore" for unauthorized users
# (#9337: Signal gateway sends pairing spam when allowlist is configured)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_signal_with_allowlist_ignores_unauthorized_dm(monkeypatch):
    """When SIGNAL_ALLOWED_USERS is set, unauthorized DMs are silently dropped.

    This is the primary regression test for #9337: before the fix, Signal
    would send pairing codes to ANY sender even when a strict allowlist was
    configured, spamming personal contacts with cryptic bot messages.
    """
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("SIGNAL_ALLOWED_USERS", "+15550000001")  # allowlist set

    config = GatewayConfig(
        platforms={Platform.EMAIL: PlatformConfig(enabled=True)},
    )
    runner, adapter = _make_runner(Platform.EMAIL, config)

    result = await runner._handle_message(
        _make_event(Platform.EMAIL, "+15559999999", "+15559999999")  # not in allowlist
    )

    assert result is None
    runner.pairing_store.generate_code.assert_not_called()
    adapter.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_telegram_with_allowlist_ignores_unauthorized_dm(monkeypatch):
    """Same behavior for Telegram: allowlist ⟹ ignore unauthorized DMs."""
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "111111111")

    config = GatewayConfig(
        platforms={Platform.EMAIL: PlatformConfig(enabled=True)},
    )
    runner, adapter = _make_runner(Platform.EMAIL, config)

    result = await runner._handle_message(
        _make_event(Platform.EMAIL, "999999999", "999999999")
    )

    assert result is None
    runner.pairing_store.generate_code.assert_not_called()
    adapter.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_global_allowlist_ignores_unauthorized_dm(monkeypatch):
    """GATEWAY_ALLOWED_USERS also triggers the 'ignore' behavior."""
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("GATEWAY_ALLOWED_USERS", "111111111")

    config = GatewayConfig(
        platforms={Platform.EMAIL: PlatformConfig(enabled=True)},
    )
    runner, adapter = _make_runner(Platform.EMAIL, config)

    result = await runner._handle_message(
        _make_event(Platform.EMAIL, "+15559999999", "+15559999999")
    )

    assert result is None
    runner.pairing_store.generate_code.assert_not_called()
    adapter.send.assert_not_awaited()


def test_allowlist_authorized_user_returns_ignore_for_unauthorized(monkeypatch):
    """_get_unauthorized_dm_behavior returns 'ignore' when allowlist is set.

    We test the resolver directly.  The full _handle_message path for
    authorized users is covered by the integration tests in this module.
    """
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("SIGNAL_ALLOWED_USERS", "+15550000001")

    config = GatewayConfig(
        platforms={Platform.EMAIL: PlatformConfig(enabled=True)},
    )
    runner, _adapter = _make_runner(Platform.EMAIL, config)

    behavior = runner._get_unauthorized_dm_behavior(Platform.EMAIL)
    assert behavior == "ignore"


def test_get_unauthorized_dm_behavior_no_allowlist_returns_pair(monkeypatch):
    """Without any allowlist, 'pair' is still the default for the retained
    pair-default chat platform (LOCAL)."""
    _clear_auth_env(monkeypatch)

    config = GatewayConfig(
        platforms={Platform.LOCAL: PlatformConfig(enabled=True)},
    )
    runner, _adapter = _make_runner(Platform.LOCAL, config)

    behavior = runner._get_unauthorized_dm_behavior(Platform.LOCAL)
    assert behavior == "pair"


def test_get_unauthorized_dm_behavior_email_no_allowlist_returns_ignore(monkeypatch):
    _clear_auth_env(monkeypatch)

    config = GatewayConfig(
        platforms={Platform.EMAIL: PlatformConfig(enabled=True)},
    )
    runner, _adapter = _make_runner(Platform.EMAIL, config)

    behavior = runner._get_unauthorized_dm_behavior(Platform.EMAIL)
    assert behavior == "ignore"


def test_qqbot_with_allowlist_ignores_unauthorized_dm(monkeypatch):
    """QQBOT is included in the allowlist-aware default (QQ_ALLOWED_USERS).

    Regression guard: the initial #9337 fix omitted QQBOT from the env map
    inside _get_unauthorized_dm_behavior, even though _is_user_authorized
    mapped it to QQ_ALLOWED_USERS.  Without QQBOT here, a QQ operator with a
    strict user allowlist would still get pairing codes sent to strangers.
    """
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("QQ_ALLOWED_USERS", "allowed-openid-1")

    config = GatewayConfig(
        platforms={Platform.EMAIL: PlatformConfig(enabled=True)},
    )
    runner, _adapter = _make_runner(Platform.EMAIL, config)

    behavior = runner._get_unauthorized_dm_behavior(Platform.EMAIL)
    assert behavior == "ignore"
