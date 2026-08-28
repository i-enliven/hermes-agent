"""
Single source of truth for provider identity in Hermes Agent.

Providers:
1. GitHub Copilot (copilot, copilot-acp)
2. User-defined config providers (providers: / custom_providers:)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from utils import base_url_host_matches, base_url_hostname

logger = logging.getLogger(__name__)


# -- Hermes overlay ----------------------------------------------------------

@dataclass(frozen=True)
class HermesOverlay:
    """Hermes-specific provider metadata."""

    transport: str = "openai_chat"        # openai_chat | anthropic_messages | codex_responses
    is_aggregator: bool = False
    auth_type: str = "api_key"            # api_key | oauth_device_code | oauth_external | external_process
    extra_env_vars: Tuple[str, ...] = ()  # env vars
    base_url_override: str = ""           # override base URL
    base_url_env_var: str = ""            # env var for user-custom base URL


HERMES_OVERLAYS: Dict[str, HermesOverlay] = {
    "copilot": HermesOverlay(
        transport="openai_chat",
        extra_env_vars=("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"),
        base_url_env_var="COPILOT_API_BASE_URL",
    ),
    "copilot-acp": HermesOverlay(
        transport="codex_responses",
        auth_type="external_process",
        base_url_override="acp://copilot",
        base_url_env_var="COPILOT_ACP_BASE_URL",
    ),
    "github-copilot": HermesOverlay(
        transport="openai_chat",
        extra_env_vars=("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"),
    ),
}


# -- Resolved provider -------------------------------------------------------

@dataclass
class ProviderDef:
    """Complete provider definition — merged from all sources."""

    id: str
    name: str
    transport: str                        # openai_chat | anthropic_messages | codex_responses
    api_key_env_vars: Tuple[str, ...]     # all env vars to check for API key
    base_url: str = ""
    base_url_env_var: str = ""
    is_aggregator: bool = False
    auth_type: str = "api_key"
    doc: str = ""
    source: str = ""                      # "hermes", "user-config"


# -- Aliases ------------------------------------------------------------------

ALIASES: Dict[str, str] = {
    "github": "copilot",
    "github-copilot": "copilot",
    "github-models": "copilot",
    "github-model": "copilot",
    "github-copilot-acp": "copilot-acp",
    "copilot-acp-agent": "copilot-acp",
    "ollama": "custom",
}


# -- Display labels -----------------------------------------------------------

_LABEL_OVERRIDES: Dict[str, str] = {
    "copilot": "GitHub Copilot",
    "copilot-acp": "GitHub Copilot ACP",
    "custom": "Custom endpoint",
}


# -- Transport → API mode mapping ---------------------------------------------

TRANSPORT_TO_API_MODE: Dict[str, str] = {
    "openai_chat": "chat_completions",
    "anthropic_messages": "anthropic_messages",
    "codex_responses": "codex_responses",
    "bedrock_converse": "bedrock_converse",
}


# -- Helper functions ---------------------------------------------------------

def normalize_provider(name: str) -> str:
    """Resolve aliases and normalise casing to a canonical provider id."""
    key = (name or "").strip().lower()
    return ALIASES.get(key, key)


def get_provider(name: str, *, allow_network: bool = True) -> Optional[ProviderDef]:
    """Look up a built-in provider by id or alias."""
    canonical = normalize_provider(name)
    overlay = HERMES_OVERLAYS.get(canonical)

    if overlay is not None:
        return ProviderDef(
            id=canonical,
            name=_LABEL_OVERRIDES.get(canonical, canonical),
            transport=overlay.transport,
            api_key_env_vars=overlay.extra_env_vars,
            base_url=overlay.base_url_override,
            base_url_env_var=overlay.base_url_env_var,
            is_aggregator=overlay.is_aggregator,
            auth_type=overlay.auth_type,
            source="hermes",
        )

    return None


def get_label(provider_id: str) -> str:
    """Get a human-readable display name for a provider."""
    canonical = normalize_provider(provider_id)
    if canonical in _LABEL_OVERRIDES:
        return _LABEL_OVERRIDES[canonical]
    pdef = get_provider(canonical)
    if pdef:
        return pdef.name
    return canonical


def is_aggregator(provider: str) -> bool:
    """Return True when the provider is a multi-model aggregator."""
    provider_norm = normalize_provider(provider or "")
    if provider_norm.startswith("custom:"):
        return True
    pdef = get_provider(provider_norm)
    return pdef.is_aggregator if pdef else False


_FLAT_NAMESPACE_RESELLERS: frozenset[str] = frozenset()


def is_routing_aggregator(provider: str) -> bool:
    """Return True only for TRUE routing aggregators."""
    provider_norm = normalize_provider(provider or "")
    if provider_norm in _FLAT_NAMESPACE_RESELLERS:
        return False
    return is_aggregator(provider_norm)


def is_official_openai_host(base_url: str) -> bool:
    """True when *base_url* points at OpenAI's official API host family."""
    return base_url_host_matches(base_url, "api.openai.com")


def host_mandated_api_mode(base_url: str = "") -> Optional[str]:
    """Return the wire protocol a specific endpoint requires, or None."""
    if not base_url:
        return None
    url_lower = base_url.rstrip("/").lower()
    hostname = base_url_hostname(base_url)
    if hostname == "api.anthropic.com" or url_lower.endswith("/anthropic") or url_lower.endswith("/messages"):
        return "anthropic_messages"
    if is_official_openai_host(base_url):
        return "codex_responses"
    return None


def determine_api_mode(provider: str, base_url: str = "", model: str = "") -> str:
    """Determine the API mode (wire protocol) for a provider/endpoint."""
    mandated = host_mandated_api_mode(base_url)
    if mandated is not None:
        return mandated

    pdef = get_provider(provider)
    if pdef is not None:
        return TRANSPORT_TO_API_MODE.get(pdef.transport, "chat_completions")

    return "chat_completions"


def resolve_user_provider(name: str, user_config: Dict[str, Any]) -> Optional[ProviderDef]:
    """Resolve a provider from the user's config.yaml providers: section."""
    if not user_config or not isinstance(user_config, dict):
        return None

    entry = user_config.get(name)
    if not isinstance(entry, dict):
        return None

    display_name = entry.get("name", "") or name
    api_url = entry.get("api", "") or entry.get("url", "") or entry.get("base_url", "") or ""
    key_env = entry.get("key_env", "") or ""
    transport = entry.get("transport", "openai_chat") or "openai_chat"

    env_vars: List[str] = []
    if key_env:
        env_vars.append(key_env)

    return ProviderDef(
        id=name,
        name=display_name,
        transport=transport,
        api_key_env_vars=tuple(env_vars),
        base_url=api_url,
        is_aggregator=False,
        auth_type="api_key",
        source="user-config",
    )


def custom_provider_slug(display_name: str, provider_key: str = "") -> str:
    """Build the stable custom: identity for a configured provider."""
    identity = str(provider_key or "").strip() or str(display_name or "").strip()
    normalized = identity.lower().replace(" ", "-")
    return normalized if normalized.startswith("custom:") else f"custom:{normalized}"


def custom_provider_aliases(
    display_name: str,
    provider_key: str = "",
) -> frozenset[str]:
    """Return every current and legacy identity accepted for one endpoint."""
    aliases: set[str] = set()
    for value in (display_name, provider_key):
        raw = str(value or "").strip().lower()
        if not raw:
            continue
        normalized = raw.replace(" ", "-")
        aliases.update({raw, normalized, custom_provider_slug(normalized)})
        if normalized.startswith("custom:"):
            suffix = normalized.split(":", 1)[1]
            if suffix:
                aliases.update({suffix, f"custom:{normalized}"})
    return frozenset(aliases)


def resolve_custom_provider(
    name: str,
    custom_providers: Optional[List[Dict[str, Any]]],
) -> Optional[ProviderDef]:
    """Resolve a provider from the user's config.yaml custom_providers list."""
    if not custom_providers or not isinstance(custom_providers, list):
        return None

    requested = (name or "").strip().lower()
    if not requested:
        return None

    bare_custom_fallback = requested == "custom"
    first_valid: Optional[Tuple[str, str, Tuple[str, ...], str]] = None

    for entry in custom_providers:
        if not isinstance(entry, dict):
            continue

        display_name = (entry.get("name") or "").strip()
        api_url = (
            entry.get("base_url", "")
            or entry.get("url", "")
            or entry.get("api", "")
            or ""
        ).strip()
        if not display_name or not api_url:
            continue

        key_env = (entry.get("key_env") or "").strip()
        provider_key = (entry.get("provider_key") or "").strip()
        env_vars: List[str] = []
        if key_env:
            env_vars.append(key_env)

        if first_valid is None:
            first_valid = (
                display_name,
                api_url,
                tuple(env_vars),
                custom_provider_slug(display_name, provider_key),
            )

        slug = custom_provider_slug(display_name, provider_key)
        if requested not in custom_provider_aliases(display_name, provider_key):
            continue

        return ProviderDef(
            id=slug,
            name=display_name,
            transport="openai_chat",
            api_key_env_vars=tuple(env_vars),
            base_url=api_url,
            is_aggregator=False,
            auth_type="api_key",
            source="user-config",
        )

    if bare_custom_fallback and first_valid:
        dname, aurl, denv, slug = first_valid
        return ProviderDef(
            id=slug,
            name=dname,
            transport="openai_chat",
            api_key_env_vars=denv,
            base_url=aurl,
            is_aggregator=False,
            auth_type="api_key",
            source="user-config",
        )

    return None


def resolve_provider_full(
    name: str,
    user_providers: Optional[Dict[str, Any]] = None,
    custom_providers: Optional[List[Dict[str, Any]]] = None,
) -> Optional[ProviderDef]:
    """Full resolution chain: built-in → user config."""
    canonical = normalize_provider(name)
    raw = (name or "").strip().lower()

    if user_providers:
        user_pdef = resolve_user_provider(raw, user_providers)
        if user_pdef is not None:
            return user_pdef

    if canonical != raw:
        try:
            from hermes_cli.auth import PROVIDER_REGISTRY as _AUTH_PROVIDER_REGISTRY
            _pcfg = _AUTH_PROVIDER_REGISTRY.get(raw)
            if _pcfg is not None:
                return ProviderDef(
                    id=_pcfg.id,
                    name=_pcfg.name,
                    transport="openai_chat",
                    api_key_env_vars=tuple(_pcfg.api_key_env_vars or ()),
                    base_url=_pcfg.inference_base_url or "",
                    source="hermes-auth-registry",
                )
        except Exception:
            pass

    pdef = get_provider(canonical)
    if pdef is not None:
        return pdef

    if user_providers:
        user_pdef = resolve_user_provider(canonical, user_providers)
        if user_pdef is not None:
            return user_pdef
        user_pdef = resolve_user_provider(raw, user_providers)
        if user_pdef is not None:
            return user_pdef

    custom_pdef = resolve_custom_provider(name, custom_providers)
    if custom_pdef is not None:
        return custom_pdef

    return None
