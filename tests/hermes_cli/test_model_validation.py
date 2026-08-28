"""Tests for provider-aware `/model` validation in hermes_cli.models."""

from unittest.mock import MagicMock, patch

from hermes_cli.models import (
    copilot_model_api_mode,
    fetch_github_model_catalog,
    fetch_api_models,
    github_model_reasoning_efforts,
    normalize_copilot_model_id,
    normalize_provider,
    probe_api_models,
    provider_label,
    provider_model_ids,
    validate_requested_model,
)


# -- helpers -----------------------------------------------------------------

FAKE_API_MODELS = [
    "anthropic/claude-opus-4.6",
    "anthropic/claude-sonnet-4.5",
    "openai/gpt-5.4-pro",
    "openai/gpt-5.4",
    "google/gemini-3-pro-preview",
]


def _validate(model, provider="custom", api_models=FAKE_API_MODELS, **kw):
    """Shortcut: call validate_requested_model with mocked API."""
    probe_payload = {
        "models": api_models,
        "probed_url": "http://localhost:11434/v1/models",
        "resolved_base_url": kw.get("base_url", "") or "http://localhost:11434/v1",
        "suggested_base_url": None,
        "used_fallback": False,
    }
    with patch("hermes_cli.models.fetch_api_models", return_value=api_models), \
         patch("hermes_cli.models.probe_api_models", return_value=probe_payload):
        return validate_requested_model(model, provider, **kw)


# -- normalize_provider ------------------------------------------------------

class TestNormalizeProvider:

    def test_known_aliases(self):
        assert normalize_provider("github") == "copilot"
        assert normalize_provider("github-copilot") == "copilot"
        assert normalize_provider("github-models") == "copilot"
        assert normalize_provider("github-copilot-acp") == "copilot-acp"
        assert normalize_provider("copilot-acp-agent") == "copilot-acp"
        assert normalize_provider("ollama") == "custom"


class TestProviderLabel:
    def test_known_labels_and_auto(self):
        assert provider_label("copilot") == "GitHub Copilot"
        assert provider_label("copilot-acp") == "GitHub Copilot ACP"
        assert provider_label("custom") == "Custom endpoint"
        assert provider_label("auto") == "Auto"


# -- provider_model_ids ------------------------------------------------------

class TestProviderModelIds:

    def test_custom_provider_passes_anthropic_mode_for_versioned_proxy_catalog(self):
        with patch(
            "hermes_cli.config.load_config",
            return_value={
                "model": {
                    "provider": "custom",
                    "base_url": "http://localhost:6655/anthropic/v1",
                    "api_key": "proxy-key",
                }
            },
        ), patch(
            "hermes_cli.models.fetch_api_models",
            return_value=["enterprise-claude"],
        ) as mock_fetch:
            assert provider_model_ids("custom") == ["enterprise-claude"]

        mock_fetch.assert_called_once_with(
            "proxy-key",
            "http://localhost:6655/anthropic/v1",
            api_mode="anthropic_messages",
        )


# -- fetch_api_models --------------------------------------------------------

class TestFetchApiModels:
    def test_returns_none_when_no_base_url(self):
        assert fetch_api_models("key", None) is None

    def test_probe_api_models_tries_v1_fallback(self):
        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"data": [{"id": "fallback-model"}]}'

        calls = []

        def fake_urlopen(req, timeout=5.0, **kw):
            calls.append(req.full_url)
            if req.full_url == "http://localhost:11434/v1/models":
                return _Resp()
            raise Exception("404")

        with patch("hermes_cli.models._urlopen_model_catalog_request", side_effect=fake_urlopen):
            probe = probe_api_models("key", "http://localhost:11434")

        assert probe["models"] == ["fallback-model"]
        assert probe["resolved_base_url"] == "http://localhost:11434/v1"
        assert probe["suggested_base_url"] == "http://localhost:11434/v1"
        assert probe["used_fallback"] is True
        assert calls == [
            "http://localhost:11434/models",
            "http://localhost:11434/v1/models",
        ]


# -- github_model_reasoning_efforts ------------------------------------------

class TestGithubReasoningEfforts:
    def test_gpt5_models_expose_minimal_through_high(self):
        assert github_model_reasoning_efforts("gpt-5.4") == ["minimal", "low", "medium", "high"]
        assert github_model_reasoning_efforts("gpt-5-mini") == ["minimal", "low", "medium", "high"]

    def test_o_series_expose_low_medium_high(self):
        assert github_model_reasoning_efforts("o3") == ["low", "medium", "high"]
        assert github_model_reasoning_efforts("openai/o1") == ["low", "medium", "high"]

    def test_non_reasoning_models_return_empty_list(self):
        assert github_model_reasoning_efforts("claude-sonnet-4.5") == []


# -- normalize_copilot_model_id / copilot_model_api_mode ----------------------

class TestCopilotNormalization:
    def test_normalizes_aliases(self):
        assert normalize_copilot_model_id("openai/gpt-5-mini") == "gpt-5-mini"
        assert normalize_copilot_model_id("anthropic/claude-sonnet-4.5") == "claude-sonnet-4.5"

    def test_api_mode_resolution(self):
        assert copilot_model_api_mode("gpt-5.4") == "codex_responses"
        assert copilot_model_api_mode("gpt-5-mini") == "chat_completions"
        assert copilot_model_api_mode("claude-sonnet-4.5") == "chat_completions"


# -- validate_requested_model: format checks ---------------------------------

class TestValidateFormatChecks:
    def test_empty_string_rejected(self):
        result = validate_requested_model("", "custom")
        assert result["accepted"] is False
        assert result["persist"] is False
        assert result["recognized"] is False
        assert "empty" in result["message"]

    def test_whitespace_rejected(self):
        result = validate_requested_model("  ", "custom")
        assert result["accepted"] is False
        assert result["persist"] is False
        assert result["recognized"] is False

    def test_spaces_rejected(self):
        result = validate_requested_model("gpt 5", "custom")
        assert result["accepted"] is False
        assert result["persist"] is False
        assert result["recognized"] is False
        assert "spaces" in result["message"]


# -- validate_requested_model: live API 404 (not in catalog) -----------------

class TestValidateApiNotFound:
    def test_unknown_model_rejected_when_api_responds(self):
        result = _validate("openai/nonexistent-model-xyz", "custom")
        assert result["accepted"] is True
        assert result["persist"] is True
        assert result["recognized"] is False


# -- validate_requested_model: live API fallback (network down) ---------------

class TestValidateApiFallback:
    def test_unreachable_api_warns_but_accepts(self):
        with patch("hermes_cli.models.fetch_api_models", return_value=None), \
             patch("hermes_cli.models.probe_api_models", return_value={"models": None, "probed_url": "http://localhost:11434/models"}):
            result = validate_requested_model("gpt-5.4", "copilot")
        assert result["accepted"] is True
        assert result["persist"] is True


# -- probe_api_models — Cloudflare UA mitigation --------------------------------

class TestProbeApiModelsUserAgent:

    def _make_mock_response(self, body: bytes):
        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read = MagicMock(return_value=body)
        return mock_resp

    def test_probe_sends_hermes_user_agent(self):
        body = b'{"data":[{"id":"claude-opus-4.7"}]}'
        with patch(
            "hermes_cli.models._urlopen_model_catalog_request",
            return_value=self._make_mock_response(body),
        ) as mock_urlopen:
            result = probe_api_models("sk-test", "https://example.com/v1")

        assert result["models"] == ["claude-opus-4.7"]
        req = mock_urlopen.call_args[0][0]
        ua = req.get_header("User-agent")
        assert ua, "probe_api_models must send a User-Agent header"
        assert ua.startswith("hermes-cli/"), (
            f"User-Agent must advertise hermes-cli, got {ua!r}"
        )
        assert not ua.startswith("Python-urllib")

    def test_probe_user_agent_sent_without_api_key(self):
        body = b'{"data":[]}'
        with patch(
            "hermes_cli.models._urlopen_model_catalog_request",
            return_value=self._make_mock_response(body),
        ) as mock_urlopen:
            probe_api_models(None, "https://example.com/v1")

        req = mock_urlopen.call_args[0][0]
        ua = req.get_header("User-agent")
        assert ua and ua.startswith("hermes-cli/")
        assert req.get_header("Authorization") is None
