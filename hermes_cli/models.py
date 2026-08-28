"""
Canonical model catalogs and lightweight validation helpers.

Add, remove, or reorder entries here — both `hermes setup` and
`hermes` provider-selection will pick up the change automatically.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import threading
import urllib.parse
import urllib.request
import urllib.error
import time
from difflib import get_close_matches
from pathlib import Path
from typing import Any, NamedTuple, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from typing import TypeGuard

from hermes_cli import __version__ as _HERMES_VERSION
from hermes_cli.urllib_security import open_credentialed_url
from utils import base_url_host_matches

logger = logging.getLogger(__name__)

# Identify ourselves so endpoints fronted by Cloudflare's Browser Integrity
# Check (error 1010) don't reject the default ``Python-urllib/*`` signature.
_HERMES_USER_AGENT = f"hermes-cli/{_HERMES_VERSION}"

COPILOT_BASE_URL = "https://api.githubcopilot.com"
COPILOT_MODELS_URL = f"{COPILOT_BASE_URL}/models"
COPILOT_EDITOR_VERSION = "vscode/1.104.1"
COPILOT_REASONING_EFFORTS_GPT5 = ["minimal", "low", "medium", "high"]
COPILOT_REASONING_EFFORTS_O_SERIES = ["low", "medium", "high"]


def _urlopen_model_catalog_request(req: urllib.request.Request, *, timeout: float, ssl_context=None):
    """Open catalog requests without forwarding headers across origins."""
    return open_credentialed_url(req, timeout=timeout, ssl_context=ssl_context)


def _custom_provider_ssl_context(base_url: str):
    """Build an ``ssl.SSLContext`` from a custom provider's TLS settings.

    Mirrors the httpx/requests TLS resolution so the urllib ``/models``
    discovery probe honors a provider's ``ssl_ca_cert`` / ``ssl_verify``
    instead of falling back to the process-wide ``SSL_CERT_FILE`` / certifi
    bundle. Returns None when no per-provider TLS override applies, so the
    caller keeps urllib's default policy for public/unconfigured endpoints.
    """
    if not base_url:
        return None
    try:
        from hermes_cli.config import get_custom_provider_tls_settings

        tls = get_custom_provider_tls_settings(base_url)
        if not tls:
            return None
        import ssl

        if tls.get("ssl_verify") is False:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return ctx
        ca = tls.get("ssl_ca_cert")
        if isinstance(ca, str) and ca and os.path.isfile(ca):
            return ssl.create_default_context(cafile=ca)
    except Exception:
        return None  # never break discovery on a TLS-config lookup
    return None


_PROVIDER_MODELS: dict[str, list[str]] = {
    "copilot-acp": [
        "copilot-acp",
    ],
    "copilot": [
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5-mini",
        "gpt-5.3-codex",
        "gpt-5.2-codex",
        "gpt-4.1",
        "gpt-4o",
        "gpt-4o-mini",
        "claude-sonnet-4.6",
        "claude-sonnet-5",
        "claude-sonnet-4",
        "claude-sonnet-4.5",
        "claude-haiku-4.5",
        "gemini-3.1-pro-preview",
        "gemini-3-pro-preview",
        "gemini-3-flash-preview",
        "gemini-2.5-pro",
    ],
}


class ProviderEntry(NamedTuple):
    slug: str
    label: str
    tui_desc: str   # detailed description for `hermes model` TUI


CANONICAL_PROVIDERS: list[ProviderEntry] = [
    ProviderEntry("copilot",        "GitHub Copilot",           "GitHub Copilot (Uses GITHUB_TOKEN or gh auth token)"),
    ProviderEntry("copilot-acp",    "GitHub Copilot ACP",       "GitHub Copilot ACP (Spawns copilot --acp --stdio)"),
]

# Auto-extend CANONICAL_PROVIDERS with any provider registered in providers/
_canonical_slugs = {p.slug for p in CANONICAL_PROVIDERS}
try:
    from providers import list_providers as _list_providers_for_canonical
    for _pp in _list_providers_for_canonical():
        if _pp.name in _canonical_slugs:
            continue
        if _pp.auth_type in {"oauth_device_code", "oauth_external", "external_process", "aws_sdk", "copilot", "vertex"}:
            continue  # non-api-key flows need bespoke picker UX; skip auto-inject
        _label = _pp.display_name or _pp.name
        _desc = _pp.description or f"{_label} (direct API)"
        CANONICAL_PROVIDERS.append(ProviderEntry(_pp.name, _label, _desc))
        _canonical_slugs.add(_pp.name)
except Exception:
    pass

# Derived dicts — used throughout the codebase
_PROVIDER_LABELS = {p.slug: p.label for p in CANONICAL_PROVIDERS}
_PROVIDER_LABELS["custom"] = "Custom endpoint"  # special case: not a named provider

PROVIDER_GROUPS: dict[str, tuple[str, str, list[str]]] = {
    "copilot":  ("GitHub Copilot",  "GitHub token API or copilot --acp process",       ["copilot", "copilot-acp"]),
}

_SLUG_TO_GROUP: dict[str, str] = {
    slug: gid for gid, (_label, _desc, members) in PROVIDER_GROUPS.items() for slug in members
}


def provider_group_for_slug(slug: str) -> str:
    """Return the group_id a provider slug belongs to, or "" if ungrouped."""
    return _SLUG_TO_GROUP.get(str(slug or "").strip().lower(), "")


def group_providers(slugs):
    """Fold a flat ordered slug iterable into picker rows by provider group."""
    seen: set[str] = set()
    group_members: dict[str, list[str]] = {}
    for gid, (_label, _desc, members) in PROVIDER_GROUPS.items():
        present = [m for m in members if m in set(slugs)]
        if present:
            group_members[gid] = present

    rows = []
    emitted_groups: set[str] = set()
    for slug in slugs:
        s = str(slug or "").strip().lower()
        if not s or s in seen:
            continue
        seen.add(s)
        gid = _SLUG_TO_GROUP.get(s, "")
        if not gid:
            rows.append({"kind": "single", "slug": s})
            continue
        if gid in emitted_groups:
            continue
        emitted_groups.add(gid)
        members = group_members.get(gid, [s])
        if len(members) <= 1:
            rows.append({"kind": "single", "slug": members[0]})
        else:
            label, desc, _ = PROVIDER_GROUPS[gid]
            rows.append(
                {"kind": "group", "group_id": gid, "label": label,
                 "description": desc, "members": list(members)}
            )
    return rows


_PROVIDER_ALIASES = {
    "github": "copilot",
    "github-copilot": "copilot",
    "github-models": "copilot",
    "github-model": "copilot",
    "github-copilot-acp": "copilot-acp",
    "copilot-acp-agent": "copilot-acp",
    "ollama": "custom",
}

_KNOWN_PROVIDER_NAMES = frozenset(_PROVIDER_LABELS.keys()) | frozenset(_PROVIDER_ALIASES.keys())


def get_default_model_for_provider(provider: str) -> str:
    """Return a default model for a provider, or "" if unknown."""
    models = _PROVIDER_MODELS.get(provider, [])
    return models[0] if models else ""


_pricing_cache: dict[str, dict[str, dict[str, str]]] = {}
_FAILED_CATALOG_TTL_SECONDS = 120.0
_pricing_cache_retry_after: dict[str, float] = {}


def _cached_catalog(cache_key: str) -> Optional[dict[str, dict[str, Any]]]:
    cached = _pricing_cache.get(cache_key)
    if cached is None:
        return None
    retry_after = _pricing_cache_retry_after.get(cache_key)
    if retry_after is not None and time.monotonic() >= retry_after:
        _pricing_cache.pop(cache_key, None)
        _pricing_cache_retry_after.pop(cache_key, None)
        return None
    return cached


def _cache_catalog(
    cache_key: str,
    catalog: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    _pricing_cache[cache_key] = catalog
    if not catalog:
        _pricing_cache_retry_after[cache_key] = time.monotonic() + _FAILED_CATALOG_TTL_SECONDS
    else:
        _pricing_cache_retry_after.pop(cache_key, None)
    return catalog


def fetch_models_with_pricing(
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: float = 8.0,
    *,
    force_refresh: bool = False,
    include_sale_original: bool = False,
) -> dict[str, dict[str, Any]]:
    cache_key = (base_url or "").rstrip("/")
    if not force_refresh:
        cached = _cached_catalog(cache_key)
        if cached is not None:
            return cached

    url = cache_key + "/v1/models"
    headers: dict[str, str] = {
        "Accept": "application/json",
        "User-Agent": _HERMES_USER_AGENT,
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        req = urllib.request.Request(url, headers=headers)
        with _urlopen_model_catalog_request(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode())
    except Exception:
        return _cache_catalog(cache_key, {})

    result: dict[str, dict[str, Any]] = {}
    for item in payload.get("data", []):
        mid = item.get("id")
        pricing = item.get("pricing")
        if mid and isinstance(pricing, dict):
            entry: dict[str, Any] = {
                "prompt": str(pricing.get("prompt", "")),
                "completion": str(pricing.get("completion", "")),
            }
            if pricing.get("input_cache_read"):
                entry["input_cache_read"] = str(pricing["input_cache_read"])
            if pricing.get("input_cache_write"):
                entry["input_cache_write"] = str(pricing["input_cache_write"])
            result[mid] = entry

    return _cache_catalog(cache_key, result)


def get_pricing_for_provider(provider: str, *, force_refresh: bool = False) -> list[tuple[str, str]]:
    """Return model list for provider."""
    normalized = normalize_provider(provider)
    live = provider_model_ids(normalized)
    if live:
        return [(m, "") for m in live]
    models = _PROVIDER_MODELS.get(normalized, [])
    return [(m, "") for m in models]


def _provider_keys(provider: str) -> set[str]:
    key = (provider or "").strip().lower()
    normalized = normalize_provider(provider)
    return {k for k in (key, normalized) if k}


def _provider_catalog_names(provider: str) -> tuple[str, ...]:
    """Active picker models for provider."""
    return tuple(_PROVIDER_MODELS.get(provider, []))


def _model_in_provider_catalog(name_lower: str, providers: set[str]) -> bool:
    return any(
        name_lower == model.lower()
        for provider in providers
        for model in _provider_catalog_names(provider)
    )


_BORROWED_MODEL_PROVIDERS: frozenset[str] = frozenset()


def _resolve_static_model_alias(
    name_lower: str,
    current_keys: set[str],
) -> Optional[tuple[str, str]]:
    try:
        from hermes_cli.model_switch import MODEL_ALIASES
    except Exception:
        return None

    identity = MODEL_ALIASES.get(name_lower)
    if identity is None:
        return None

    family = identity.family

    def _match(provider: str) -> Optional[str]:
        models = _PROVIDER_MODELS.get(provider, [])
        if not models:
            return None
        prefix = family.lower()
        for model in models:
            if model.lower().startswith(prefix):
                return model
        return None

    for provider in current_keys:
        if matched := _match(provider):
            return provider, matched

    for provider in _PROVIDER_MODELS:
        if (
            provider in current_keys
            or provider in _BORROWED_MODEL_PROVIDERS
        ):
            continue
        if matched := _match(provider):
            return provider, matched

    return None


def detect_static_provider_for_model(
    model_name: str,
    current_provider: str,
) -> Optional[tuple[str, str]]:
    name = (model_name or "").strip()
    if not name:
        return None

    name_lower = name.lower()
    current_keys = _provider_keys(current_provider)

    alias_match = _resolve_static_model_alias(name_lower, current_keys)
    if alias_match:
        return alias_match

    resolved_provider = _PROVIDER_ALIASES.get(name_lower, name_lower)
    if resolved_provider != "custom":
        default_models = _PROVIDER_MODELS.get(resolved_provider, [])
        if (
            resolved_provider in _PROVIDER_LABELS
            and default_models
            and resolved_provider not in current_keys
        ):
            return (
                resolved_provider,
                get_default_model_for_provider(resolved_provider) or default_models[0],
            )

    if _model_in_provider_catalog(name_lower, current_keys):
        return None

    _is_custom_current = (
        current_provider == "custom"
        or current_provider.startswith("custom:")
    )
    for pid in _PROVIDER_MODELS:
        if (
            pid in current_keys
            or pid in _BORROWED_MODEL_PROVIDERS
        ):
            continue
        if _is_custom_current:
            continue
        if any(name_lower == m.lower() for m in _provider_catalog_names(pid)):
            return (pid, name)

    return None


def detect_provider_for_model(
    model_name: str,
    current_provider: str,
) -> Optional[tuple[str, str]]:
    name = (model_name or "").strip()
    if not name:
        return None

    static_match = detect_static_provider_for_model(name, current_provider)
    if static_match:
        return static_match
    return None


def normalize_provider(provider: Optional[str]) -> str:
    normalized = (provider or "copilot").strip().lower()
    return _PROVIDER_ALIASES.get(normalized, normalized)


def provider_label(provider: Optional[str]) -> str:
    original = (provider or "copilot").strip()
    normalized = original.lower()
    if normalized == "auto":
        return "Auto"
    normalized = normalize_provider(normalized)
    return _PROVIDER_LABELS.get(normalized, original or "GitHub Copilot")


_OPENAI_FAST_MODE_PREFIXES: tuple[str, ...] = (
    "gpt-",
    "o1",
    "o3",
    "o4",
)


def _is_openai_fast_model(model_id: Optional[str]) -> bool:
    raw = _strip_vendor_prefix(str(model_id or ""))
    base = raw.split(":")[0]
    if not base:
        return False
    if "codex" in base:
        return False
    return any(base.startswith(prefix) for prefix in _OPENAI_FAST_MODE_PREFIXES)


def _strip_vendor_prefix(model_id: str) -> str:
    raw = str(model_id or "").strip().lower()
    if "/" in raw:
        raw = raw.split("/", 1)[1]
    return raw


def model_supports_fast_mode(model_id: Optional[str]) -> bool:
    from agent.model_metadata import is_grok_46_family

    return (
        _is_anthropic_fast_model(model_id)
        or _is_openai_fast_model(model_id)
        or is_grok_46_family(str(model_id or ""))
    )


def _is_anthropic_fast_model(model_id: Optional[str]) -> bool:
    raw = _strip_vendor_prefix(str(model_id or ""))
    base = raw.split(":")[0]
    if not base.startswith("claude-"):
        return False
    return "opus-4-6" in base or "opus-4.6" in base


def resolve_fast_mode_overrides(model_id: Optional[str]) -> dict[str, Any] | None:
    if not model_supports_fast_mode(model_id):
        return None
    if _is_anthropic_fast_model(model_id):
        return {"speed": "fast"}
    return {"service_tier": "priority"}


def _resolve_copilot_catalog_api_key() -> str:
    try:
        from hermes_cli.auth import resolve_api_key_provider_credentials

        creds = resolve_api_key_provider_credentials("copilot")
        api_key = str(creds.get("api_key") or "").strip()
        if api_key:
            return api_key
    except Exception:
        pass

    try:
        from hermes_cli.auth import read_credential_pool
        from hermes_cli.copilot_auth import (
            exchange_copilot_token,
            validate_copilot_token,
        )

        for entry in read_credential_pool("copilot"):
            if not isinstance(entry, dict):
                continue
            raw = str(entry.get("access_token") or "").strip()
            if not raw:
                continue
            valid, _ = validate_copilot_token(raw)
            if not valid:
                continue
            try:
                api_token, _expires_at = exchange_copilot_token(raw)
            except Exception:
                continue
            if api_token:
                return api_token
    except Exception:
        pass

    return ""


def _model_dedup_key(model_id: str) -> str:
    key = str(model_id).strip().lower()
    try:
        from hermes_cli.model_search import model_alias_canonical
        return model_alias_canonical(key)
    except Exception:
        return key


def _get_model_config_dict() -> dict:
    try:
        from hermes_cli.config import load_config
        cfg = load_config()
        m = cfg.get("model", {})
        return m if isinstance(m, dict) else {}
    except Exception:
        return {}


def _get_custom_base_url() -> str:
    m = _get_model_config_dict()
    return str(m.get("base_url", "") or "").strip()


def _base_url_looks_like_anthropic_messages(base_url: Optional[str]) -> bool:
    normalized = (base_url or "").strip().lower()
    return normalized.endswith("/v1/messages") or "/anthropic" in normalized


def provider_model_ids(provider: Optional[str], *, force_refresh: bool = False) -> list[str]:
    """Return the best known model catalog for a provider."""
    normalized = normalize_provider(provider)
    if normalized in {"copilot", "copilot-acp"}:
        try:
            live = _fetch_github_models(_resolve_copilot_catalog_api_key())
            if live:
                return live
        except Exception:
            pass
        if normalized == "copilot-acp":
            return list(_PROVIDER_MODELS.get("copilot", []))
    if normalized == "custom":
        base_url = _get_custom_base_url()
        if base_url:
            model_cfg = _get_model_config_dict()
            api_key = (
                str(model_cfg.get("api_key", "") or "").strip()
                or os.getenv("CUSTOM_API_KEY", "")
                or os.getenv("OPENAI_API_KEY", "")
            )
            api_mode = "anthropic_messages" if _base_url_looks_like_anthropic_messages(base_url) else None
            live = fetch_api_models(api_key, base_url, api_mode=api_mode)
            if live:
                return live

    # ── Profile-based generic live fetch (all simple api-key providers) ──
    try:
        from providers import get_provider_profile
        from hermes_cli.auth import resolve_api_key_provider_credentials

        _p = get_provider_profile(normalized)
        if _p and _p.auth_type == "api_key" and _p.base_url:
            try:
                creds = resolve_api_key_provider_credentials(normalized)
                api_key = str(creds.get("api_key") or "").strip()
                base_url = str(creds.get("base_url") or "").strip()
            except Exception:
                api_key, base_url = "", _p.base_url
            if not base_url:
                base_url = _p.base_url
            if api_key:
                live = _p.fetch_models(api_key=api_key, base_url=base_url or None)
                if live:
                    curated = list(_PROVIDER_MODELS.get(normalized, [])) or list(
                        _p.fallback_models or ()
                    )
                    if curated:
                        primary, secondary = curated, live
                        merged = list(primary)
                        merged_lower = {_model_dedup_key(m) for m in primary}
                        for m in secondary:
                            if _model_dedup_key(m) not in merged_lower:
                                merged.append(m)
                                merged_lower.add(_model_dedup_key(m))
                        return merged
                    return live
            if _p.fallback_models:
                return list(_p.fallback_models)
    except Exception:
        pass

    return list(_PROVIDER_MODELS.get(normalized, []))


_PROVIDER_MODELS_CACHE_TTL = 3600  # 1h
_PROVIDER_MODELS_STALE_SERVE_MAX = 7 * 24 * 3600  # 7d

_swr_refresh_inflight: set = set()
_swr_refresh_lock = threading.Lock()


def _spawn_swr_refresh(cache_key: str, refresh_fn=None) -> None:
    with _swr_refresh_lock:
        if cache_key in _swr_refresh_inflight:
            return
        _swr_refresh_inflight.add(cache_key)

    def _default_refresh():
        live = provider_model_ids(cache_key, force_refresh=True)
        if not live:
            return None
        return {
            "fp": _credential_fingerprint(cache_key),
            "at": time.time(),
            "models": list(live),
        }

    def _refresh() -> None:
        try:
            entry = (refresh_fn or _default_refresh)()
            if entry:
                cache = _load_provider_models_cache()
                cache[cache_key] = entry
                _save_provider_models_cache(cache)
        except Exception:
            pass
        finally:
            with _swr_refresh_lock:
                _swr_refresh_inflight.discard(cache_key)

    threading.Thread(
        target=_refresh,
        name=f"model-cache-swr-{cache_key}",
        daemon=True,
    ).start()


def _provider_models_cache_path() -> Path:
    from hermes_constants import get_hermes_home
    return get_hermes_home() / "provider_models_cache.json"


_cache_file_lock = threading.Lock()


def _load_provider_models_cache() -> dict[str, dict[str, Any]]:
    try:
        path = _provider_models_cache_path()
        if not path.exists():
            return {}
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_provider_models_cache(cache: dict[str, dict[str, Any]]) -> None:
    try:
        from utils import atomic_json_write
        path = _provider_models_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json_write(path, cache, indent=None)
    except Exception:
        pass


def update_provider_cache_entry(provider: str, models: list[str]) -> None:
    if not provider or not models:
        return
    normalized = normalize_provider(provider)
    fp = _credential_fingerprint(normalized)
    with _cache_file_lock:
        cache = _load_provider_models_cache()
        cache[normalized] = {"fp": fp, "at": time.time(), "models": list(models)}
        _save_provider_models_cache(cache)


def _credential_fingerprint(provider: str) -> str:
    import hashlib
    normalized = normalize_provider(provider)
    try:
        from hermes_cli.auth import PROVIDER_REGISTRY
        pcfg = PROVIDER_REGISTRY.get(normalized)
        env_names = pcfg.api_key_env_vars if pcfg else ()
    except Exception:
        env_names = ()

    tokens = [f"provider={normalized}"]
    for name in env_names:
        tokens.append(f"{name}={os.getenv(name, '')}")
    blob = "|".join(tokens).encode("utf-8", errors="replace")
    return hashlib.blake2b(blob, digest_size=8).hexdigest()


def cached_provider_model_ids(
    provider: Optional[str],
    *,
    force_refresh: bool = False,
    cache_only: bool = False,
    ttl_seconds: int = _PROVIDER_MODELS_CACHE_TTL,
) -> list[str]:
    normalized = normalize_provider(provider)
    if not normalized:
        return []

    fp = _credential_fingerprint(normalized)
    cache = _load_provider_models_cache()
    entry = cache.get(normalized)
    now = time.time()

    if cache_only:
        if force_refresh or not _cache_entry_valid(entry, fp):
            return []
        if now - entry["at"] >= _PROVIDER_MODELS_STALE_SERVE_MAX:
            return []
        return list(entry["models"])

    if not force_refresh and _cache_entry_valid(entry, fp):
        age = now - entry["at"]
        if age < ttl_seconds:
            return list(entry["models"])
        if age < _PROVIDER_MODELS_STALE_SERVE_MAX:
            _spawn_swr_refresh(normalized)
            return list(entry["models"])

    live = provider_model_ids(normalized, force_refresh=force_refresh)
    if live:
        cache[normalized] = {"fp": fp, "at": now, "models": list(live)}
        _save_provider_models_cache(cache)
        return list(live)

    if _cache_entry_valid(entry, fp):
        return list(entry["models"])
    return list(live or [])


def clear_provider_models_cache(provider: Optional[str] = None) -> None:
    try:
        if provider is None:
            path = _provider_models_cache_path()
            if path.exists():
                path.unlink()
            return
        cache = _load_provider_models_cache()
        normalized = normalize_provider(provider) or provider or ""
        if normalized in cache:
            del cache[normalized]
            _save_provider_models_cache(cache)
    except Exception:
        pass


def _payload_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        data = payload.get("data", [])
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
    return []


def copilot_default_headers(*, is_agent_turn: bool = True) -> dict[str, str]:
    try:
        from hermes_cli.copilot_auth import copilot_request_headers
        return copilot_request_headers(is_agent_turn=is_agent_turn)
    except ImportError:
        return {
            "Editor-Version": COPILOT_EDITOR_VERSION,
            "User-Agent": "HermesAgent/1.0",
            "Openai-Intent": "conversation-edits",
            "x-initiator": "agent" if is_agent_turn else "user",
        }


def _copilot_catalog_item_is_text_model(item: dict[str, Any]) -> bool:
    model_id = str(item.get("id") or "").strip()
    if not model_id:
        return False
    if item.get("model_picker_enabled") is False:
        return False
    capabilities = item.get("capabilities")
    if isinstance(capabilities, dict):
        model_type = str(capabilities.get("type") or "").strip().lower()
        if model_type and model_type != "chat":
            return False
    supported_endpoints = item.get("supported_endpoints")
    if isinstance(supported_endpoints, list):
        normalized_endpoints = {
            str(endpoint).strip()
            for endpoint in supported_endpoints
            if str(endpoint).strip()
        }
        if normalized_endpoints and not normalized_endpoints.intersection(
            {"/chat/completions", "/responses", "/v1/messages"}
        ):
            return False
    return True


_github_model_catalog_cache: Optional[list[dict[str, Any]]] = None
_github_model_catalog_cache_key: Optional[str] = None
_github_model_catalog_cache_time: float = 0.0
_GITHUB_MODEL_CATALOG_CACHE_TTL = 300  # 5 minutes


def fetch_github_model_catalog(
    api_key: Optional[str] = None, timeout: float = 5.0
) -> Optional[list[dict[str, Any]]]:
    global _github_model_catalog_cache, _github_model_catalog_cache_key
    global _github_model_catalog_cache_time

    if (
        _github_model_catalog_cache is not None
        and _github_model_catalog_cache_key == api_key
        and (time.monotonic() - _github_model_catalog_cache_time) < _GITHUB_MODEL_CATALOG_CACHE_TTL
    ):
        return copy.deepcopy(_github_model_catalog_cache)

    attempts: list[dict[str, str]] = []
    if api_key:
        attempts.append({
            **copilot_default_headers(),
            "Authorization": f"Bearer {api_key}",
        })
    attempts.append(copilot_default_headers())

    for headers in attempts:
        req = urllib.request.Request(COPILOT_MODELS_URL, headers=headers)
        try:
            with _urlopen_model_catalog_request(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode())
                items = _payload_items(data)
                models: list[dict[str, Any]] = []
                seen_ids: set[str] = set()
                for item in items:
                    if not _copilot_catalog_item_is_text_model(item):
                        continue
                    model_id = str(item.get("id") or "").strip()
                    if not model_id or model_id in seen_ids:
                        continue
                    seen_ids.add(model_id)
                    models.append(item)
                if models:
                    _github_model_catalog_cache = copy.deepcopy(models)
                    _github_model_catalog_cache_key = api_key
                    _github_model_catalog_cache_time = time.monotonic()
                    return models
        except Exception:
            continue
    return None


_copilot_context_cache: dict[str, int] = {}
_copilot_context_cache_time: float = 0.0
_COPILOT_CONTEXT_CACHE_TTL = 3600  # 1 hour


def get_copilot_model_context(model_id: str, api_key: Optional[str] = None) -> Optional[int]:
    global _copilot_context_cache, _copilot_context_cache_time
    if _copilot_context_cache and (time.time() - _copilot_context_cache_time < _COPILOT_CONTEXT_CACHE_TTL):
        if model_id in _copilot_context_cache:
            return _copilot_context_cache[model_id]
        return None

    catalog = fetch_github_model_catalog(api_key=api_key)
    if not catalog:
        return None

    cache: dict[str, int] = {}
    for item in catalog:
        mid = str(item.get("id") or "").strip()
        if not mid:
            continue
        caps = item.get("capabilities") or {}
        limits = caps.get("limits") or {}
        max_prompt = limits.get("max_prompt_tokens")
        if isinstance(max_prompt, int) and max_prompt > 0:
            cache[mid] = max_prompt

    _copilot_context_cache = cache
    _copilot_context_cache_time = time.time()
    return cache.get(model_id)


def _is_github_models_base_url(base_url: Optional[str]) -> bool:
    normalized = (base_url or "").strip().rstrip("/").lower()
    return (
        normalized.startswith(COPILOT_BASE_URL)
        or normalized.startswith("https://models.github.ai/inference")
        or normalized.startswith("https://models.inference.ai.azure.com")
    )


def _fetch_github_models(api_key: Optional[str] = None, timeout: float = 5.0) -> Optional[list[str]]:
    catalog = fetch_github_model_catalog(api_key=api_key, timeout=timeout)
    if not catalog:
        return None
    return [item.get("id", "") for item in catalog if item.get("id")]


_COPILOT_MODEL_ALIASES = {
    "openai/gpt-5": "gpt-5-mini",
    "openai/gpt-5-chat": "gpt-5-mini",
    "openai/gpt-5-mini": "gpt-5-mini",
    "openai/gpt-5-nano": "gpt-5-mini",
    "openai/gpt-4.1": "gpt-4.1",
    "openai/gpt-4.1-mini": "gpt-4.1",
    "openai/gpt-4.1-nano": "gpt-4.1",
    "openai/gpt-4o": "gpt-4o",
    "openai/gpt-4o-mini": "gpt-4o-mini",
    "openai/o1": "gpt-5.2",
    "openai/o1-mini": "gpt-5-mini",
    "openai/o1-preview": "gpt-5.2",
    "openai/o3": "gpt-5.3-codex",
    "openai/o3-mini": "gpt-5-mini",
    "openai/o4-mini": "gpt-5-mini",
    "anthropic/claude-opus-4.6": "claude-opus-4.6",
    "anthropic/claude-sonnet-5": "claude-sonnet-5",
    "anthropic/claude-sonnet-4.6": "claude-sonnet-4.6",
    "anthropic/claude-sonnet-4": "claude-sonnet-4",
    "anthropic/claude-sonnet-4.5": "claude-sonnet-4.5",
    "anthropic/claude-haiku-4.5": "claude-haiku-4.5",
    "claude-sonnet-5": "claude-sonnet-5",
    "claude-opus-4-6": "claude-opus-4.6",
    "claude-sonnet-4-6": "claude-sonnet-4.6",
    "claude-sonnet-4-0": "claude-sonnet-4",
    "claude-sonnet-4-5": "claude-sonnet-4.5",
    "claude-haiku-4-5": "claude-haiku-4.5",
}


def _copilot_catalog_ids(
    catalog: Optional[list[dict[str, Any]]] = None,
    api_key: Optional[str] = None,
) -> set[str]:
    if catalog is None and api_key:
        catalog = fetch_github_model_catalog(api_key=api_key)
    if not catalog:
        return set()
    return {
        str(item.get("id") or "").strip()
        for item in catalog
        if str(item.get("id") or "").strip()
    }


def normalize_copilot_model_id(
    model_id: Optional[str],
    *,
    catalog: Optional[list[dict[str, Any]]] = None,
    api_key: Optional[str] = None,
) -> str:
    raw = str(model_id or "").strip()
    if not raw:
        return ""

    catalog_ids = _copilot_catalog_ids(catalog=catalog, api_key=api_key)
    alias = _COPILOT_MODEL_ALIASES.get(raw)
    if alias:
        return alias

    candidates = [raw]
    if "/" in raw:
        candidates.append(raw.split("/", 1)[1].strip())

    if raw.endswith("-mini"):
        candidates.append(raw[:-5])
    if raw.endswith("-nano"):
        candidates.append(raw[:-5])
    if raw.endswith("-chat"):
        candidates.append(raw[:-5])

    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        if candidate in _COPILOT_MODEL_ALIASES:
            return _COPILOT_MODEL_ALIASES[candidate]
        if candidate in catalog_ids:
            return candidate

    if "/" in raw:
        return raw.split("/", 1)[1].strip()
    return raw


def _github_reasoning_efforts_for_model_id(model_id: str) -> list[str]:
    raw = (model_id or "").strip().lower()
    if raw.startswith(("openai/o1", "openai/o3", "openai/o4", "o1", "o3", "o4")):
        return list(COPILOT_REASONING_EFFORTS_O_SERIES)
    normalized = normalize_copilot_model_id(model_id).lower()
    if normalized.startswith("gpt-5"):
        return list(COPILOT_REASONING_EFFORTS_GPT5)
    return []


def _should_use_copilot_responses_api(model_id: str) -> bool:
    match = re.match(r"^gpt-(\d+)", model_id)
    if not match:
        return False
    major = int(match.group(1))
    return major >= 5 and not model_id.startswith("gpt-5-mini")


def copilot_model_api_mode(
    model_id: Optional[str],
    *,
    catalog: Optional[list[dict[str, Any]]] = None,
    api_key: Optional[str] = None,
) -> str:
    if catalog is None and api_key:
        catalog = fetch_github_model_catalog(api_key=api_key)

    normalized = normalize_copilot_model_id(model_id, catalog=catalog, api_key=api_key)
    if not normalized:
        return "chat_completions"

    if _should_use_copilot_responses_api(normalized):
        return "codex_responses"

    return "chat_completions"


def github_model_reasoning_efforts(
    model_id: Optional[str],
    *,
    catalog: Optional[list[dict[str, Any]]] = None,
    api_key: Optional[str] = None,
) -> list[str]:
    normalized = normalize_copilot_model_id(model_id, catalog=catalog, api_key=api_key)
    if not normalized:
        return []

    catalog_entry = None
    if catalog is not None:
        catalog_entry = next((item for item in catalog if item.get("id") == normalized), None)
    elif api_key:
        fetched_catalog = fetch_github_model_catalog(api_key=api_key)
        if fetched_catalog:
            catalog_entry = next((item for item in fetched_catalog if item.get("id") == normalized), None)

    if catalog_entry is not None:
        capabilities = catalog_entry.get("capabilities")
        if isinstance(capabilities, dict):
            supports = capabilities.get("supports")
            if isinstance(supports, dict):
                efforts = supports.get("reasoning_effort")
                if isinstance(efforts, list):
                    normalized_efforts = [
                        str(effort).strip().lower()
                        for effort in efforts
                        if str(effort).strip()
                    ]
                    return list(dict.fromkeys(normalized_efforts))
            return []
        legacy_capabilities = {
            str(capability).strip().lower()
            for capability in catalog_entry.get("capabilities", [])
            if str(capability).strip()
        }
        if "reasoning" not in legacy_capabilities:
            return []

    return _github_reasoning_efforts_for_model_id(str(model_id or normalized))


def probe_api_models(
    api_key: Optional[str],
    base_url: Optional[str],
    timeout: float = 5.0,
    api_mode: Optional[str] = None,
    request_headers: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    normalized = (base_url or "").strip().rstrip("/")
    if not normalized:
        return {
            "models": None,
            "probed_url": None,
            "resolved_base_url": "",
            "suggested_base_url": None,
            "used_fallback": False,
        }

    if _is_github_models_base_url(normalized):
        models = _fetch_github_models(api_key=api_key, timeout=timeout)
        return {
            "models": models,
            "probed_url": COPILOT_MODELS_URL,
            "resolved_base_url": COPILOT_BASE_URL,
            "suggested_base_url": None,
            "used_fallback": False,
        }

    if normalized.endswith("/v1"):
        alternate_base = normalized[:-3].rstrip("/")
    else:
        alternate_base = normalized + "/v1"

    candidates: list[tuple[str, bool]] = [(normalized, False)]
    if alternate_base and alternate_base != normalized:
        candidates.append((alternate_base, True))

    tried: list[str] = []
    headers: dict[str, str] = {"User-Agent": _HERMES_USER_AGENT}
    if api_key and api_mode == "anthropic_messages":
        headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
    elif api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if normalized.startswith(COPILOT_BASE_URL):
        headers.update(copilot_default_headers())
    if isinstance(request_headers, dict):
        from hermes_cli.config import normalize_extra_headers
        headers.update(normalize_extra_headers(request_headers))

    _ssl_context = _custom_provider_ssl_context(normalized)
    for candidate_base, is_fallback in candidates:
        url = candidate_base.rstrip("/") + "/models"
        tried.append(url)
        req = urllib.request.Request(url, headers=headers)
        _open_kwargs: dict[str, Any] = {"timeout": timeout}
        if _ssl_context is not None:
            _open_kwargs["ssl_context"] = _ssl_context
        try:
            with _urlopen_model_catalog_request(req, **_open_kwargs) as resp:
                data = json.loads(resp.read().decode())
                return {
                    "models": [m.get("id", "") for m in data.get("data", [])],
                    "probed_url": url,
                    "resolved_base_url": candidate_base.rstrip("/"),
                    "suggested_base_url": candidate_base if is_fallback else None,
                    "used_fallback": is_fallback,
                }
        except Exception:
            continue

    return {
        "models": None,
        "probed_url": tried[0] if tried else normalized.rstrip("/") + "/models",
        "resolved_base_url": normalized,
        "suggested_base_url": alternate_base if alternate_base != normalized else None,
        "used_fallback": False,
    }


def fetch_api_models(
    api_key: Optional[str],
    base_url: Optional[str],
    timeout: float = 5.0,
    api_mode: Optional[str] = None,
    headers: Optional[dict[str, str]] = None,
) -> Optional[list[str]]:
    return probe_api_models(
        api_key,
        base_url,
        timeout=timeout,
        api_mode=api_mode,
        request_headers=headers,
    ).get("models")


def _custom_endpoint_fingerprint(
    api_key: Optional[str],
    api_mode: Optional[str],
    headers: Optional[dict[str, str]],
) -> str:
    import hashlib
    blob = "|".join((
        api_key or "",
        api_mode or "",
        json.dumps(headers or {}, sort_keys=True),
    )).encode("utf-8", errors="replace")
    return hashlib.blake2b(blob, digest_size=8).hexdigest()


def _cache_entry_valid(entry: Any, fp: str) -> "TypeGuard[dict[str, Any]]":
    return (
        isinstance(entry, dict)
        and entry.get("fp") == fp
        and isinstance(entry.get("models"), list)
        and bool(entry["models"])
        and isinstance(entry.get("at"), (int, float))
        and not isinstance(entry.get("at"), bool)
    )


def cached_fetch_api_models(
    api_key: Optional[str],
    base_url: Optional[str],
    *,
    timeout: float = 5.0,
    api_mode: Optional[str] = None,
    headers: Optional[dict[str, str]] = None,
    force_refresh: bool = False,
    cache_only: bool = False,
    ttl_seconds: int = _PROVIDER_MODELS_CACHE_TTL,
) -> Optional[list[str]]:
    normalized_url = str(base_url or "").strip().rstrip("/").lower()
    if not normalized_url:
        if cache_only:
            return None
        return fetch_api_models(
            api_key, base_url, timeout=timeout, api_mode=api_mode, headers=headers
        )

    cache_key = f"custom:{normalized_url}"
    fp = _custom_endpoint_fingerprint(api_key, api_mode, headers)
    cache = _load_provider_models_cache()
    entry = cache.get(cache_key)
    now = time.time()

    if cache_only:
        if force_refresh or not _cache_entry_valid(entry, fp):
            return None
        if now - entry["at"] >= _PROVIDER_MODELS_STALE_SERVE_MAX:
            return None
        return list(entry["models"])

    if not force_refresh and _cache_entry_valid(entry, fp):
        age = now - entry["at"]
        if age < ttl_seconds:
            return list(entry["models"])
        if age < _PROVIDER_MODELS_STALE_SERVE_MAX:
            def _refresh_custom():
                live = fetch_api_models(
                    api_key, base_url,
                    timeout=timeout, api_mode=api_mode, headers=headers,
                )
                if not live:
                    return None
                return {"fp": fp, "at": time.time(), "models": list(live)}

            _spawn_swr_refresh(cache_key, _refresh_custom)
            return list(entry["models"])

    live = fetch_api_models(
        api_key, base_url, timeout=timeout, api_mode=api_mode, headers=headers
    )
    if live:
        cache[cache_key] = {"fp": fp, "at": now, "models": list(live)}
        _save_provider_models_cache(cache)
        return list(live)

    if _cache_entry_valid(entry, fp):
        return list(entry["models"])
    return live


def validate_requested_model(
    model_name: str,
    provider: Optional[str],
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    api_mode: Optional[str] = None,
) -> dict[str, Any]:
    requested = (model_name or "").strip()
    normalized = normalize_provider(provider)
    requested_for_lookup = requested
    if normalized == "copilot":
        requested_for_lookup = normalize_copilot_model_id(
            requested,
            api_key=api_key,
        ) or requested

    if not requested:
        return {
            "accepted": False,
            "persist": False,
            "recognized": False,
            "message": "Model name cannot be empty.",
        }

    if any(ch.isspace() for ch in requested):
        return {
            "accepted": False,
            "persist": False,
            "recognized": False,
            "message": "Model names cannot contain spaces.",
        }

    if normalized == "custom" or normalized.startswith("custom:"):
        if api_mode == "anthropic_messages":
            probe = probe_api_models(api_key, base_url, api_mode=api_mode)
        else:
            probe = probe_api_models(api_key, base_url)
        api_models = probe.get("models")
        if api_models is not None:
            if requested_for_lookup in set(api_models):
                return {
                    "accepted": True,
                    "persist": True,
                    "recognized": True,
                    "message": None,
                }

            auto = get_close_matches(requested_for_lookup, api_models, n=1, cutoff=0.9)
            if auto:
                return {
                    "accepted": True,
                    "persist": True,
                    "recognized": True,
                    "corrected_model": auto[0],
                    "message": f"Auto-corrected `{requested}` → `{auto[0]}`",
                }

            suggestions = get_close_matches(requested, api_models, n=3, cutoff=0.5)
            suggestion_text = ""
            if suggestions:
                suggestion_text = "\n  Similar models: " + ", ".join(f"`{s}`" for s in suggestions)

            message = (
                f"Note: `{requested}` was not found in this custom endpoint's model listing "
                f"({probe.get('probed_url')}). It may still work if the server supports hidden or aliased models."
                f"{suggestion_text}"
            )
            if probe.get("used_fallback"):
                message += (
                    f"\n  Endpoint verification succeeded after trying `{probe.get('resolved_base_url')}`. "
                    f"Consider saving that as your base URL."
                )

            return {
                "accepted": True,
                "persist": True,
                "recognized": False,
                "message": message,
            }

        message = (
            f"Note: could not reach this custom endpoint's model listing at `{probe.get('probed_url')}`. "
            f"Hermes will still save `{requested}`, but the endpoint should expose `/models` for verification."
        )
        if api_mode == "anthropic_messages":
            message += (
                "\n  Many Anthropic-compatible proxies do not implement the Models API "
                "(GET /v1/models).  The model name has been accepted without verification."
            )
        if probe.get("suggested_base_url"):
            message += f"\n  If this server expects `/v1`, try base URL: `{probe.get('suggested_base_url')}`"

        return {
            "accepted": api_mode == "anthropic_messages",
            "persist": True,
            "recognized": False,
            "message": message,
        }

    # Anthropic Messages API: many proxies don't implement /v1/models.
    if api_mode == "anthropic_messages":
        api_models = fetch_api_models(api_key, base_url, api_mode=api_mode)
        if api_models is not None:
            if requested_for_lookup in set(api_models):
                return {
                    "accepted": True,
                    "persist": True,
                    "recognized": True,
                    "message": None,
                }
            auto = get_close_matches(requested_for_lookup, api_models, n=1, cutoff=0.9)
            if auto:
                return {
                    "accepted": True,
                    "persist": True,
                    "recognized": True,
                    "corrected_model": auto[0],
                    "message": f"Auto-corrected `{requested}` → `{auto[0]}`",
                }
        return {
            "accepted": True,
            "persist": True,
            "recognized": False,
            "message": (
                f"Note: could not verify `{requested}` against this endpoint's "
                f"model listing.  Many Anthropic-compatible proxies do not "
                f"implement GET /v1/models.  The model name has been accepted "
                f"without verification."
            ),
        }

    # Probe the live API to check if the model actually exists
    api_models = fetch_api_models(api_key, base_url)
    if api_models is not None:
        if requested_for_lookup in set(api_models):
            return {
                "accepted": True,
                "persist": True,
                "recognized": True,
                "message": None,
            }
        else:
            auto = get_close_matches(requested_for_lookup, api_models, n=1, cutoff=0.9)
            if auto:
                return {
                    "accepted": True,
                    "persist": True,
                    "recognized": True,
                    "corrected_model": auto[0],
                    "message": f"Auto-corrected `{requested}` → `{auto[0]}`",
                }

            suggestions = get_close_matches(requested, api_models, n=3, cutoff=0.5)
            suggestion_text = ""
            if suggestions:
                suggestion_text = "\n  Similar models: " + ", ".join(f"`{s}`" for s in suggestions)

            if _model_in_provider_catalog(
                requested_for_lookup.lower(), _provider_keys(normalized)
            ):
                return {
                    "accepted": True,
                    "persist": True,
                    "recognized": True,
                    "message": (
                        f"Note: `{requested}` was not found in the live /v1/models listing "
                        f"but exists in the curated catalog — accepted."
                    ),
                }

        return {
            "accepted": False,
            "persist": False,
            "recognized": False,
            "message": (
                f"Model `{requested}` was not found in this provider's model listing."
                f"{suggestion_text}"
            ),
        }

    provider_label = _PROVIDER_LABELS.get(normalized, normalized)
    try:
        catalog_models = provider_model_ids(normalized)
    except Exception:
        catalog_models = []

    if catalog_models:
        catalog_lower = {m.lower(): m for m in catalog_models}
        if requested_for_lookup.lower() in catalog_lower:
            return {
                "accepted": True,
                "persist": True,
                "recognized": True,
                "message": None,
            }
        catalog_lower_list = list(catalog_lower.keys())
        auto = get_close_matches(
            requested_for_lookup.lower(), catalog_lower_list, n=1, cutoff=0.9
        )
        if auto:
            corrected = catalog_lower[auto[0]]
            return {
                "accepted": True,
                "persist": True,
                "recognized": True,
                "corrected_model": corrected,
                "message": f"Auto-corrected `{requested}` → `{corrected}`",
            }
        suggestions = get_close_matches(
            requested_for_lookup.lower(), catalog_lower_list, n=3, cutoff=0.5
        )
        suggestion_text = ""
        if suggestions:
            suggestion_text = "\n  Similar models: " + ", ".join(
                f"`{catalog_lower[s]}`" for s in suggestions
            )
        return {
            "accepted": True,
            "persist": True,
            "recognized": False,
            "message": (
                f"Note: `{requested}` was not found in the {provider_label} curated catalog "
                f"and the /models endpoint was unreachable.{suggestion_text}"
                f"\n  The model may still work if it exists on the provider."
            ),
        }

    return {
        "accepted": True,
        "persist": True,
        "recognized": False,
        "message": (
            f"Note: could not reach the {provider_label} API to validate `{requested}`. "
            f"If the service isn't down, this model may not be valid."
        ),
    }