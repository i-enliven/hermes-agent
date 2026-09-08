# Module: `acp adapter`

Speaks the Agent Client Protocol so VS Code, Zed, and JetBrains can drive a Hermes
session as their editor agent. Remove it and every editor integration dies while
the CLI, TUI, and gateway keep working — this adapter is a thin protocol shell over
the same `AIAgent` everyone else uses, plus the editor-specific concerns of edit
approval, tool-call rendering, and session lineage.

## Responsibilities

- Implement the ACP agent side over stdio JSON-RPC: `initialize`, `new_session`,
  `load_session`, `resume_session`, `fork_session`, `list_sessions`, `prompt`,
  `cancel`, `set_session_model`, `set_session_mode`, `set_config_option`,
  `authenticate`.
- Map ACP sessions onto Hermes sessions, persisting them through the shared
  `SessionDB` so an editor session is resumable later from the CLI or TUI.
- Translate Hermes tool activity into ACP `tool_call` / `tool_call_update`
  notifications with editor-legible titles and fenced, truncated result bodies.
- Convert a model's `todo` output into an ACP `AgentPlanUpdate` so the editor
  renders a plan checklist rather than a raw tool dump.
- Route privileged operations through client approval: command permissions and
  proposed file edits, both surfaced as ACP permission requests.
- Advertise auth methods so an editor can drive provider/model selection through
  the terminal.
- Expose compression lineage under `_meta.hermes` without inventing new persisted
  state.

## Key Files

- [`acp_adapter/server.py`](../../../acp_adapter/server.py) — `HermesACPAgent(acp.Agent)` (`:566`), 2,484 LOC; every protocol handler.
- [`acp_adapter/session.py`](../../../acp_adapter/session.py) — `SessionState` (`:170`), `SessionManager` (`:186`); create/fork/persist/restore.
- [`acp_adapter/tools.py`](../../../acp_adapter/tools.py) — `get_tool_kind()`, `build_tool_title()`, and ~20 `_format_*_result()` renderers.
- [`acp_adapter/events.py`](../../../acp_adapter/events.py) — callback factories handed to `AIAgent`: `make_tool_progress_cb`, `make_thinking_cb`, `make_step_cb`, `make_message_cb`, plus `_build_plan_update_from_todo_result()`.
- [`acp_adapter/edit_approval.py`](../../../acp_adapter/edit_approval.py) — `EditProposal`, `build_edit_proposal()`, `should_auto_approve_edit()`, `maybe_require_edit_approval()`.
- [`acp_adapter/permissions.py`](../../../acp_adapter/permissions.py) — `_build_permission_options()`, `_map_outcome_to_hermes()`, `make_approval_callback()`.
- [`acp_adapter/auth.py`](../../../acp_adapter/auth.py) — `detect_provider()`, `build_auth_methods()`.
- [`acp_adapter/provenance.py`](../../../acp_adapter/provenance.py) — `build_session_provenance()` over the compression chain.
- [`acp_adapter/entry.py`](../../../acp_adapter/entry.py) — `main()` (`:220`), `_parse_args()` (`:119`), the stdio bootstrap.
- [`hermes_cli/subcommands/acp.py`](../../../hermes_cli/subcommands/acp.py) — `build_acp_parser()` wiring for `hermes acp`.

## Public API

The protocol surface is `HermesACPAgent`, subclassing `acp.Agent` from the
`agent-client-protocol` package — declared as the optional extra
`acp = ["agent-client-protocol==0.9.0"]` in `pyproject.toml:268` and imported as
`acp`. The server is driven by `acp.run_agent(agent, use_unstable_protocol=True)`
(`acp_adapter/entry.py:273`).

```python
# acp_adapter/session.py
class SessionManager:
    def __init__(self, agent_factory=None, db=None)   # both injectable for tests
    def create_session(self, cwd: str = ".") -> SessionState
    def get_session(self, session_id: str) -> Optional[SessionState]
    def fork_session(self, session_id: str, cwd: str = ".") -> Optional[SessionState]
    def list_sessions(self, cwd: str | None = None) -> List[Dict[str, Any]]
    def update_cwd(self, session_id: str, cwd: str) -> Optional[SessionState]
    def save_session(self, session_id: str) -> None
    def cleanup(self) -> None

# acp_adapter/edit_approval.py
def build_edit_proposal(tool_name, arguments) -> EditProposal | None
def should_auto_approve_edit(proposal, policy, cwd=None) -> bool
def maybe_require_edit_approval(tool_name, arguments) -> str | None

# acp_adapter/permissions.py
def make_approval_callback(...) -> Callable   # returns the agent-side approval hook
```

Entry points: console script `hermes-acp = "acp_adapter.entry:main"`
(`pyproject.toml:375`), the `hermes acp` subcommand, and `acp_adapter/__main__.py`
for `python -m acp_adapter`. Flags on `entry.py`: `--version`, `--check` (verify
ACP deps and adapter imports, then exit), `--setup` (interactive provider/model
setup for terminal auth), `--setup-browser` / `--yes` (idempotent agent-browser +
Playwright Chromium install into `~/.hermes/node/`).

## Internal Structure

**Transport is stdio, and stdout is sacred.** `entry.py` routes *all* logging to
stderr so the JSON-RPC channel stays clean, and it must import `hermes_bootstrap`
first — the comment at `entry.py:16` marks this load-bearing, because skipping it
leaves UTF-8 stdio unsetup on Windows (POSIX is unaffected).

**Sessions are in-memory with a durable backing.** `SessionManager` keeps
`self._sessions` under a `threading.Lock`, and `_persist()` / `_restore()` /
`_delete_persisted()` write through to `SessionDB` — `~/.hermes/state.db` by
default, created lazily on first use (`session.py:194`). `agent_factory` and `db`
are both constructor-injectable, which is how the test suite drives the adapter
without a live provider.

**Rendering is per-tool, not generic.** `tools.py` dispatches on tool name to a
specific formatter — `_format_read_file_result`, `_format_search_files_result`,
`_format_todo_result`, `_format_delegate_result`, `_format_edit_result`, and
friends — each shaping output for an editor pane, then `_truncate_text()` caps at
5,000 chars and `_fenced_text()` wraps in a language fence. `_tool_result_failed()`
classifies failure per tool, since a tool can return a `200`-shaped string that
still means failure.

**Two separate approval paths.** Command permissions go through
`permissions.make_approval_callback()`, which builds ACP permission options,
filters them by `_permission_option_supports_kind()`, and maps the client's chosen
option back via `_map_outcome_to_hermes()` against the set of option ids it
actually offered. File edits go through `edit_approval.maybe_require_edit_approval()`,
which builds an `EditProposal` from the pending `write_file` / `patch` arguments
(`_proposal_for_write_file`, `_proposal_for_patch_replace`,
`_proposal_for_patch_v4a`), consults the policy in `should_auto_approve_edit()`,
and blocks on `_is_sensitive_auto_approve_path()`. The requester is carried in a
`ContextVar` via `set_edit_approval_requester()` / `reset_edit_approval_requester()`
— per-connection, not process-global, so concurrent editor sessions cannot approve
each other's edits.

**Provenance is derived, never stored.** `provenance.py` walks the `sessions`
table's `parent_session_id` / `end_reason` columns to expose the compression
lineage under `_meta.hermes`, bounded by `_MAX_WALK = 100`. The ACP `session_id`
stays the stable public handle while the internal Hermes head rotates under
compaction, so a client can follow the chain without parsing status text or
reading `state.db`. Unknown `_meta` keys are ignored by existing ACP clients, which
is what makes the extension additive.

## Dependencies

- **Used by:** the `hermes acp` subcommand and the `hermes-acp` console script;
  `acp_adapter/edit_approval` is imported back out of the tool layer by
  `model_tools.py`'s dispatch path, so edit approval applies to non-editor surfaces
  too.
- **Uses:** `acp` (`agent-client-protocol`), `asyncio`, `contextvars`,
  `concurrent.futures`, `ThreadPoolExecutor`; `run_agent.py:AIAgent`;
  `hermes_state.py:SessionDB`; `model_tools.py`; `agent/codex_models.py` and the
  provider catalog for `set_session_model`; `hermes_cli` config for provider
  detection in `auth.py`.
- **Optional:** the whole adapter is gated behind the `[acp]` extra, so a base
  install carries none of it.

## Notable Patterns / Gotchas

- **Do not print to stdout.** Any stray `print()` in this package or in code it
  calls during a turn corrupts the JSON-RPC stream. Logging goes to stderr via the
  `entry.py` bootstrap; use the logger.
- **`hermes_bootstrap` must be the first import** in `entry.py` (`:16`). Reordering
  imports here is a Windows-only breakage that CI on Linux will not catch.
- **Two directions of ACP in this repo — do not confuse them.** `acp_adapter/` is
  Hermes as the *agent*, being driven by an editor. `agent/copilot_acp_client.py`
  (`_resolve_command`, `_acp_supported`, `_build_subprocess_env`) and
  `plugins/model-providers/copilot-acp/` are Hermes as the *client*, driving
  someone else's ACP agent as a model backend. Opposite roles, opposite code paths.
- **Approval state is context-scoped on purpose.** The edit-approval requester
  lives in a `ContextVar`, not a module global. Assigning it process-wide would let
  one editor window approve another's file writes.
- **Formatters are the contract, not the tool result.** An editor renders what
  `_format_*_result()` returns. Adding a tool without a formatter still works, but
  it renders as a raw dump — check `tools.py` when a new tool looks wrong in-editor
  but right in the CLI.
- **`--check` is the fast triage command.** It verifies the `[acp]` extra and
  adapter imports without starting a session, so a broken editor launch is
  distinguishable from a broken provider config.
- **The tests are the executable spec.** `tests/acp/` covers the protocol surface
  (`test_server.py`, `test_session.py`, `test_tools.py`, `test_events.py`), both
  approval paths (`test_edit_approval.py`, `test_permissions.py`,
  `test_approval_isolation.py`), and the tricky bits specifically —
  `test_ping_suppression.py`, `test_session_db_private_access.py`,
  `test_session_provenance.py`, `test_mcp_e2e.py`. `tests/acp_adapter/` holds the
  subcommand test. Run via `scripts/run_tests.sh tests/acp/`.
