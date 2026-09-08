# Module: `providers & transports`

One agent core, many incompatible inference backends. A `ProviderProfile` declares what a provider *is* (auth, endpoints, quirks); a `ProviderTransport` owns the data path for one wire protocol. Together they replace the 20+ boolean flags a transport would otherwise receive.

## Responsibilities

- Declare each inference backend once as a declarative `ProviderProfile`, discovered lazily from bundled, user, pip, and legacy locations.
- Convert OpenAI-shaped messages/tools to provider-native formats and normalize responses back to one shared `NormalizedResponse`, parking protocol-specific state (`call_id`, `thought_signature`, `anthropic_content_blocks`) in `provider_data` dicts rather than the shared type.
- Resolve and pool credentials, scope them per profile under multiplexing, route side-LLM (auxiliary) tasks, and meter rate limits, credits, and cost.

## Key Files

- [`providers/base.py`](../../../providers/base.py) — `ProviderProfile` dataclass + `OMIT_TEMPERATURE` sentinel.
- [`providers/__init__.py`](../../../providers/__init__.py) — the registry and lazy discovery.
- [`agent/transports/base.py`](../../../agent/transports/base.py) — `ProviderTransport` ABC.
- [`agent/transports/__init__.py`](../../../agent/transports/__init__.py) — `register_transport()` / `get_transport()`.
- [`agent/transports/types.py`](../../../agent/transports/types.py) — `NormalizedResponse`, `ToolCall`, `Usage`.
- [`agent/credential_pool.py`](../../../agent/credential_pool.py) — `CredentialPool`, `PooledCredential`, `load_pool()`.
- [`agent/auxiliary_client.py`](../../../agent/auxiliary_client.py) — side-LLM resolution chain.
- [`agent/secret_scope.py`](../../../agent/secret_scope.py) — fail-closed profile-scoped secret reads.

## Public API

**Registry** (`providers/__init__.py`) — `register_provider(profile)`, `get_provider_profile(name)` (resolves `_ALIASES` then `_REGISTRY`; `None` for unknown so callers fall back to generic), `list_providers()` (dedupes by `id(profile)`, memoized in `_PROVIDER_LIST_CACHE`, invalidated on every registration).

**Profile hooks** (`providers/base.py`), override in a subclass: `get_hostname()`, `prepare_messages()`, `build_extra_body()`, `build_api_kwargs_extras()` (returns the `(extra_body_additions, top_level_kwargs)` split — some providers want `extra_body.reasoning`, others top-level `reasoning_effort`), `fetch_models()`, `resolve_aux_model()`, `default_vision_model()`, `get_max_tokens(model)`.

**Transport** (`agent/transports/base.py`) — required: `api_mode`, `convert_messages()`, `convert_tools()`, `build_kwargs()`, `normalize_response()`. Optional with defaults: `validate_response()` → `True`, `extract_cache_stats()` → `None`, `map_finish_reason()` → identity.

**Model metadata** — `agent/model_metadata.py` (`_infer_provider_from_url()`, context-length resolution, endpoint disk cache), `agent/models_dev.py` (`fetch_models_dev()`, `ModelInfo`, `ProviderInfo`), `agent/image_routing.py` (`decide_image_input_mode()` → `native` | `text`).

### Model-provider plugins

`plugins/model-providers/*` — 36 bundled directories, each an `__init__.py` calling `register_provider(ProviderProfile(...))` at module load. Values below are those literally present in that plugin's profile; `-` means the profile does not set it and the dataclass default (`chat_completions` / `api_key`) applies.

| Provider | `api_mode` | `auth_type` |
|---|---|---|
| `anthropic`, `minimax` | `anthropic_messages` | `api_key` |
| `bedrock` | `bedrock_converse` | `aws_sdk` |
| `openai-codex`, `xai`, `actual`, `meta-ai` | `codex_responses` | `api_key` (`openai-codex`: `oauth_external`) |
| `gemini`, `commandcode` | `chat_completions` | `api_key` / `-` |
| `vertex` | `chat_completions` | `vertex` |
| `copilot-acp` | `copilot_acp` | `external_process` |
| `nous` | `-` | `oauth_device_code` |
| `qwen-oauth` | `-` | `oauth_external` |
| `copilot` | `-` | `copilot` |
| `openrouter`, `deepseek`, `gmi`, `fireworks`, `novita`, `deepinfra`, `upstage`, `nvidia`, `huggingface`, `arcee`, `kilocode`, `kimi-coding`, `alibaba`, `alibaba-coding-plan`, `azure-foundry`, `ai-gateway`, `custom`, `ollama-cloud`, `opencode-zen`, `stepfun`, `xiaomi`, `zai` | `-` | `api_key` for most |

`copilot` routes per-model rather than pinning one `api_mode` (see its module docstring).

## Internal Structure

**Discovery scan order** — `_discover_providers()` runs once (`_discovered` guard):

1. `_discover_entry_point_providers()` — pip entry points in group `hermes_agent.plugins`. Runs **first, i.e. lowest precedence**: because `register_provider()` is last-writer-wins, a bundled or `$HERMES_HOME` profile of the same name always beats a pip-installed one, so a third-party package cannot hijack `openrouter`. Gated by the same `plugins.enabled` allow-list as the general `PluginManager` — installed-but-not-enabled is never imported; `_requires_arguments()` skips `register(ctx)` general plugins, which belong to the `PluginManager`.
2. Bundled — `<repo>/plugins/model-providers/<name>/`, loaded as `plugins.model_providers.<name>`.
3. User — `$HERMES_HOME/plugins/model-providers/<name>/`, loaded as `_hermes_user_provider_<name>` so multiple profiles do not alias each other in `sys.modules`.
4. Legacy — `providers/<name>.py` via `pkgutil.iter_modules`, skipping `_`-prefixed and `base`.

**Transport selection** — `AIAgent._get_transport()` (`run_agent.py`) memoizes per `api_mode` in `_transport_cache`. `get_transport()` re-runs `_discover_transports()` on a *miss*, not only when the registry is empty, so a partially-imported registry cannot make a valid `api_mode` unavailable.

**Registered api_modes** — `chat_completions` → `ChatCompletionsTransport`, `codex_responses` → `ResponsesApiTransport`, `anthropic_messages` → `AnthropicTransport`, `bedrock_converse` → `BedrockTransport`. `agent_init.py` accepts a fifth value, `codex_app_server`, which has **no** `ProviderTransport`: it is a JSON-RPC subprocess runtime (`agent/transports/codex_app_server.py` + `codex_app_server_session.py`, driven by `agent/codex_runtime.py::run_codex_app_server_turn`), dispatched from `conversation_loop.py`.

**Provider adapters at `agent/`** — three representative shapes:

- `agent/bedrock_adapter.py` is the cleanest: it bypasses the OpenAI-compat layer for direct AWS SDK calls — `convert_messages_to_converse()`, `convert_tools_to_converse()`, `normalize_converse_response()`, credential-chain helpers (`has_aws_credentials()`, `resolve_bedrock_region()`) and stale-socket classifiers (`is_stale_connection_error()`, `is_streaming_access_denied_error()`). `agent/transports/bedrock.py` calls into these.
- `agent/anthropic_adapter.py` owns auth as well as format: `build_anthropic_client()`, `resolve_anthropic_token()`, `read_claude_code_credentials()`, `refresh_anthropic_oauth_pure()`, `convert_messages_to_anthropic()`, `convert_tools_to_anthropic()`. API keys go as `x-api-key`; `sk-ant-oat*` setup tokens use Bearer auth plus a beta header.
- `agent/codex_responses_adapter.py` is stateless pure conversion — every function takes data and returns data, no `AIAgent` reference — which is what makes it testable in isolation.

| Adapter | Role |
|---|---|
| [`agent/vertex_adapter.py`](../../../agent/vertex_adapter.py) | Vertex AI OpenAI-compat endpoint; `google-auth` service-account path, lazy import |
| [`agent/gemini_native_adapter.py`](../../../agent/gemini_native_adapter.py) | OpenAI-shaped facade over native `generateContent`; keeps `api_mode='chat_completions'` for `gemini` |
| [`agent/azure_identity_adapter.py`](../../../agent/azure_identity_adapter.py) | Keyless Entra ID via `DefaultAzureCredential`; `build_token_provider()` returns the zero-arg callable the OpenAI SDK invokes per request |
| [`agent/gemini_schema.py`](../../../agent/gemini_schema.py) | Strips tool schemas to Gemini's `Schema` subset (`_GEMINI_SCHEMA_ALLOWED_KEYS`) |
| [`agent/moonshot_schema.py`](../../../agent/moonshot_schema.py) | Rewrites to Moonshot's stricter JSON Schema dialect (every property needs `type`; `required` always present) |
| [`agent/lmstudio_reasoning.py`](../../../agent/lmstudio_reasoning.py) | Maps `reasoning_config` onto LM Studio's vocabulary, clamped to the model's published `allowed_options` |

**Credentials & routing** — `agent/credential_pool.py` (`CredentialPool`, `load_pool()`, `get_pool_strategy()`, `_mark_exhausted()`) seeds from env, Claude Code, PKCE, device-code, `gh_cli`, custom config, and manual entries. `agent/credential_sources.py` unifies only *removal* (`RemovalStep` / `register()` / `find_removal_step()`) so `hermes auth remove` survives the next `load_pool()`. `agent/credential_persistence.py` is the disk boundary: `_PERSISTABLE_PROVIDER_SOURCES` allow-lists `(provider, source)` pairs Hermes owns; anything else is treated as borrowed and stripped before `auth.json`. `agent/secret_scope.py` gives `set_secret_scope()` / `get_secret()` — when multiplexing is active and no scope is set, `get_secret()` raises `UnscopedSecretError` rather than falling back to `os.environ`. `agent/secret_sources/` resolves env-shaped secrets from external managers (`bitwarden.py`, `onepassword.py`, `command.py`) through the `SecretSource` ABC, orchestrated by `registry.py::apply_all()` (mapped beats bulk, first claim wins). `agent/proxy_sources/` ships the iron-proxy egress wrapper. `agent/command_token_source.py::build_command_token_provider()` mints a short-lived bearer from a configured `key_cmd`, cached until just before expiry. `agent/backend_identity.py` is the single owner of skip/dedup decisions: `FailureScope` classifies a failure against one of three axes, and `same_credential_surface()` / `same_endpoint()` / `same_deployment()` answer "same backend along the axis this failure invalidated?".

**Rate limits & usage** — `agent/rate_limit_tracker.py` parses `x-ratelimit-*` into `RateLimitState` / `RateLimitBucket` for `/usage`. `agent/nous_rate_guard.py` writes that state to a shared file so CLI, gateway, cron, and auxiliary sessions share one cooldown — a single 429 would otherwise cost 3 SDK retries × 3 Hermes retries per turn. `agent/credits_tracker.py` parses `x-nous-credits-*` into a validated `CreditsState`. `agent/account_usage.py` and `agent/billing_usage.py` build the dollar-denominated bars (`AccountUsageSnapshot`, `usage_model_from_account()`); `agent/usage_pricing.py` holds `CanonicalUsage`, `BillingRoute`, `PricingEntry`, `resolve_billing_route()`. `agent/aux_accounting.py` publishes session identity via a contextvar so aux usage (which has no session handle) reaches the ledger.

**Registry pattern for optional capabilities** — `agent/web_search_registry.py` + `plugins/web/{firecrawl,parallel,tavily,exa,searxng,brave_free,ddgs,xai,perplexity,keenable}`, `agent/browser_registry.py` + `plugins/browser/{browser_use,browserbase,firecrawl}`, `agent/image_gen_registry.py` + `plugins/image_gen/{deepinfra,fal,krea,meta-ai,openai,openai-codex,openrouter,xai}`, `agent/video_gen_registry.py` + `plugins/video_gen/{deepinfra,fal,xai}`, plus `agent/tts_registry.py` and `agent/transcription_registry.py`. All share one shape: module-level `_providers` plus a profile-keyed `_scoped_providers` (keyed by `hermes_home_key()`), a `threading.Lock`, `register_provider()` / `list_providers()` / `get_provider()` taking `scope=`, and `snapshot_registration()` / `restore_registration()` for atomic hot-reload.

*Selection among the registered ones:* explicit config wins **and ignores `is_available()`**, so the dispatcher surfaces a precise `"X_API_KEY is not set"` error instead of silently switching backends. Only the *unconfigured* fallback is availability-filtered: single-available-provider shortcut, then a `_LEGACY_PREFERENCE` tuple (`web_search_registry.py:159`, `browser_registry.py:163`) or a named legacy default (`fal` for image gen). Capability filters (`supports_search` / `supports_extract`) apply at every step. TTS and transcription add a built-ins-always-win rule: a name in `_BUILTIN_NAMES` is rejected at registration with a warning.

**Relay** — two unrelated subsystems share the word. `agent/relay_llm.py`, `agent/relay_runtime.py`, `agent/relay_tools.py` are **NeMo Relay** adapters (`RelayRuntime`, `RelayHostRegistry`, `RelaySession`, `managed_callback_guard`) binding lazily via `_load_nemo_relay()` → `importlib.import_module("nemo_relay")`. Separately, `docs/relay-connector-contract.md` documents the gateway↔connector WebSocket contract implemented by `gateway/relay/` (`RelayAdapter`, `CapabilityDescriptor`). Neither references the other.

## Dependencies

- **Used by:** `agent/agent_init.py` and `run_agent.py` (profile + transport selection), `agent/chat_completion_helpers.py`, `agent/model_metadata.py`, `hermes_cli/{auth,models,doctor,config,runtime_provider}.py`.
- **Uses:** `hermes_constants.get_hermes_home()` / `hermes_home_key()`, `hermes_cli.config`, `hermes_cli.plugins` (entry-point gating), `hermes_cli.urllib_security.open_credentialed_url` (in `fetch_models`), `agent.auxiliary_client`, `agent.portal_tags`.
- **Optional SDKs** arrive as extras, not core deps: `anthropic`, `exa`, `firecrawl`, `fal`, `edge-tts`, `modal`, `daytona`, `vercel`, lazily installed by `tools/lazy_deps.py`.

## Notable Patterns / Gotchas

- **`fetch_models()` returning `None` is a signal, not a failure.** Callers must fall back to the static catalog. URL resolution is `self.models_url` → caller `base_url` → `self.base_url + "/models"`, and it sets a `hermes-cli/<version>` User-Agent via `_profile_user_agent()` because some providers sit behind a WAF that 403s the default `Python-urllib` UA.
- **Strict providers 400 on Hermes-internal message fields.** `ChatCompletionsTransport.convert_messages()` is the last wire boundary and strips `tool_name`, `timestamp`, `api_content`, `effect_disposition`, Codex `call_id`/`response_item_id`, and every `_`-prefixed scaffolding marker; it also drops `tool_calls: []` and `tool_calls: null` on assistant messages. Permissive gateways ignore the extra keys, which masked this for months; a strict one poisons every later request in the session.
- **`extra_content` (Gemini `thought_signature`) is conditional.** Kept for Gemini-family targets (replay is required — omission 400s), stripped for everyone else, including non-Gemini models that inherited stale `extra_content` earlier in a mixed-provider session.
- **`supports_prompt_cache_key` is opt-in `False`.** Many OpenAI-compatible endpoints reject unknown top-level fields rather than ignoring them, so the profile must affirm the field explicitly.
- **`resolve_aux_model()` exists because `default_aux_model` rots.** A hardcoded id 404s once the provider retires it; the hook lets a provider query a live recommendation. Contract: must cache, must not raise, must return `""` when it has no answer.
- **`auxiliary` resolution order** (`agent/auxiliary_client.py::_resolve_auto_route`, wrapped by `_resolve_auto`) — per-task overrides from `auxiliary.<task>.{provider,model,base_url,api_key,api_mode,key_env}` win first (`_resolve_task_provider_model()`; `_get_auxiliary_task_config()` layers plugin-declared defaults *under* user config). Then: **(1)** the user's own main provider + main model, regardless of provider type — `auto` means "use my chat model for side tasks too"; **(2)** the configured per-task `fallback_chain`, then the main agent's `fallback_providers`; **(3)** `_get_provider_chain()` = `openrouter` → `nous` → `local/custom` → `api-key`. Unhealthy providers are skipped on a TTL-bounded cache. `openai-codex` is deliberately absent from step 3 — OpenAI gates it behind a shifting undocumented model allow-list. `"auto"` is normalized to `None` before the wire, or a provider returns 200 with `"the model 'auto' does not exist"` as the task output.
- **Model catalogs are volatile data.** Do not assert specific model ids as facts; the invariants that hold are structural — every catalog entry has a context-length path, and `agent/models_dev.py` resolves in-memory cache → disk cache (any age, served stale while a daemon thread refreshes) → network, quarantining a corrupt cache rather than blocking on the network.
- **Dependency pinning is stricter than the guide's table.** The load-bearing rule is that *every* direct dependency carries an upper bound, and in practice the manifest goes further than the documented `>=floor,<next_major` form: of 29 direct dependencies, 24 are exact `==X.Y.Z` pins and 5 remain ranged (`urllib3>=2.7.0,<3`, `fastapi>=0.104.0,<1`, `uvicorn[standard]>=0.24.0,<1`, `python-multipart>=0.0.9,<1`, `ptyprocess>=0.7.0,<1`). Its comment records the reason for the drift: tightened 2026-05-12 in response to the Mini Shai-Hulud worm that hit `mistralai` 2.4.6 on PyPI — a range like `>=2.3.0,<3` would have pulled the compromised release into every install before the quarantine. Real entries: `"openai==2.24.0"`, `"httpx[socks]==0.28.1"`, `"pydantic==2.13.4"`, and in extras `anthropic = ["anthropic==0.87.0"]`. Bump the pin **and** regenerate `uv.lock`. Scope rule: only packages every session needs are core; provider-specific SDKs go in an extra. *(No `git+` URLs exist in this checkout's `pyproject.toml`, and there is no `.github/workflows/` directory here, so the SHA-pinning rules for those could not be verified from source.)*
- **`smart_model_routing` is written, never read.** `hermes_cli/setup.py:3229` sets `["enabled"] = False` and `hermes_cli/config.py:1961` lists it as a known root key, but no `agent/`, `run_agent.py`, `tools/`, or `gateway/` site reads it. Treat it as inert config.
- **`get_provider_profile()` returning `None` is load-bearing** — it is what routes `ChatCompletionsTransport.build_kwargs()` to the legacy flag path instead of `_build_kwargs_from_profile()`. Known providers always take the profile path.
- **`supports_health_check` is the doctor's off-switch** (`providers/base.py:57`, read at `hermes_cli/doctor.py:899`/`:2512`): set it `False` for a provider with no `/models` endpoint so `hermes doctor` skips the probe rather than reporting a false failure.
- **The transport deliberately does not own the hard parts.** `agent/transports/base.py`'s module docstring scopes it to the data path only: client construction, streaming, credential refresh, prompt caching, interrupt handling, and retry logic all stay on `AIAgent`. A new per-provider quirk belongs on the profile, not in a transport branch.
- **`get_transport()` constructs a new instance on every call** (`return cls()`), so transports must stay stateless; the only reuse is `AIAgent._transport_cache`. Per-model behaviour therefore reaches a transport through `build_kwargs(**params)` or the `provider_profile` param, never through instance state.
- **Two vision flags that are easy to conflate** (`providers/base.py`): `supports_vision` defaults `False` (does the API accept image content inside *tool-result* messages natively), while `supports_vision_tool_messages` defaults `True` for back-compat and must be set `False` for providers that accept multimodal *user* messages but reject list-type tool content (Xiaomi MiMo answers 400 `"text is not set"`). `fallback_models` is what the `/model` picker shows when the live fetch fails, and per the field comment should list only agentic models that support tool calling.
