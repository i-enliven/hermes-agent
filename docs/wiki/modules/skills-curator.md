# Module: `skills & curator`

Skills are Hermes' procedural memory — markdown playbooks the agent loads on demand
and can author itself — and the Curator is the background janitor that ages,
consolidates, and archives the ones the agent wrote. Remove this module and the
agent stops compounding: every session starts from zero, and the skills directory
fills with half-finished playbooks nobody retires.

## Responsibilities

- Serve two skill surfaces with different activation rules: bundled (`skills/`,
  loadable by default) and optional (`optional-skills/`, shipped but inert until
  installed).
- Parse `SKILL.md` frontmatter and expose each skill as a slash command.
- Inject an invoked skill into the conversation as a **user** message, never a
  system-prompt edit, so the per-conversation prompt cache survives.
- Track per-skill usage telemetry in a sidecar and derive staleness from it.
- Run a periodic review pass that transitions skills active → stale → archived,
  optionally consolidating and pruning.
- Back up the skills tree before every mutating run, and support rollback.
- Enforce pinning as an opt-out from auto-transitions.

## Key Files

- [`agent/curator.py`](../../../agent/curator.py) — the review loop, auto-transitions, the LLM review prompt, `get_prune_builtins()` (`:192`).
- [`agent/curator_backup.py`](../../../agent/curator_backup.py) — pre-run snapshots of the skills tree.
- [`hermes_cli/curator.py`](../../../hermes_cli/curator.py) — the `hermes curator <verb>` argparse surface (`:838` onward).
- [`agent/skill_commands.py`](../../../agent/skill_commands.py) — `scan_skill_commands()` (`:419`), `get_skill_commands()` (`:528`), `build_skill_invocation_message()` (`:630`), `reload_skills()` (`:546`).
- [`agent/skill_utils.py`](../../../agent/skill_utils.py) — `parse_frontmatter()` (`:175`), the real frontmatter loader.
- [`agent/skill_preprocessing.py`](../../../agent/skill_preprocessing.py), [`agent/skill_bundles.py`](../../../agent/skill_bundles.py) — preprocessing and multi-skill bundles (`skill_bundles.py:286` reuses `_build_skill_message`).
- [`tools/skill_usage.py`](../../../tools/skill_usage.py) — the `.usage.json` sidecar, `is_agent_created()` (`:427`), `list_agent_created_skill_names()` (`:338`).
- [`tools/skill_manager_tool.py`](../../../tools/skill_manager_tool.py) — the `skill_manage` tool (`:1542`, registered `:1832`), `_pinned_guard()` (`:274`).
- [`tools/skills_hub.py`](../../../tools/skills_hub.py) — `OptionalSkillSource(SkillSource)` (`:3273`) and the `--now` cache-invalidation handling (`:1981`, `:2027`, `:2039`).

## Public API

```python
# agent/skill_commands.py
def scan_skill_commands() -> Dict[str, Dict[str, Any]]
def get_skill_commands() -> Dict[str, Dict[str, Any]]
def build_skill_invocation_message(cmd_key, user_instruction="", task_id=None,
                                   runtime_note="") -> Optional[str]
def build_stacked_skill_invocation_message(...)   # several skills in one turn
def resolve_skill_command_key(command: str) -> Optional[str]
def split_stacked_skill_commands(rest: str) -> tuple[list[str], str]
def reload_skills() -> Dict[str, Any]

# agent/skill_utils.py
def parse_frontmatter(content: str) -> Tuple[Dict[str, Any], str]

# tools/skill_usage.py
def is_agent_created(skill_name: str) -> bool
def list_agent_created_skill_names() -> List[str]
def latest_activity_at(row) -> ...; def set_state(...); def set_pinned(...)
```

The `hermes curator` verb set is **17**, not the 11 `AGENTS.md` lists:
`status`, `usage`, `run`, `pause`, `resume`, `pin`, `unpin`, `list-unmanaged`,
`adopt`, `restore`, `list-archived`, `archive`, `prune`, `backup`, `rollback`,
`ledger`, `purge`. The documented list omits `usage`, `list-unmanaged`, `adopt`,
`list-archived`, `ledger`, and `purge`.

## Internal Structure

**Two surfaces, two activation rules.** `skills/` ships categories that are
loadable out of the box — `apple`, `autonomous-ai-agents`, `creative`, `devops`,
`email`, `github`, `index-cache`, `jupyter-notebook-analysis`, `media`, `mlops`,
`note-taking`, `productivity`, `research`, `smart-home`, `social-media`,
`software-development`. `optional-skills/` ships heavier or niche skills that stay
inert until installed via `hermes skills install official/<category>/<skill>` —
`autonomous-ai-agents`, `blockchain`, `communication`, `creative`, `data-science`,
`devops`, `dogfood`, `email`, `finance`, `gaming`, `health`, `mcp`, `migration`,
`mlops`, `payments`, `productivity`, `research`, `security`,
`software-development`, `web-development`, `yuanbao`. Paths nest arbitrarily deep
(`skills_hub.py:656` cites `official/mlops/training/trl-fine-tuning`), so the
adapter resolves by suffix, not by a fixed depth.

**Invocation preserves the cache.** `build_skill_invocation_message()` is
documented as *"Build the user message content for a skill slash command
invocation"* — the payload joins the transcript as a user turn. `reload_skills()`
carries the matching note: *"This does NOT invalidate the skills system-prompt
cache."* The gateway's auto-skill path (`gateway/run.py:18862`) shows the same
discipline: it injects only on **new** sessions, concatenating the built payloads
ahead of the user's own text into `event.text`, because an ongoing conversation
already carries the skill content in its history.

**Telemetry drives lifecycle.** `tools/skill_usage.py` owns
`~/.hermes/skills/.usage.json` (`:86`), a read-modify-write sidecar serialized
across processes (`:91`). Per-skill rows carry `use_count`, `view_count`,
`patch_count`, `last_activity_at`, `state`, and `pinned`. Staleness is computed
from `last_activity_at` against the configured thresholds; archiving is delegated
to the hub's own `archive_skill()` rather than reimplemented.

**The review pass.** `agent/curator.py` seeds candidates, applies
`stale_after_days` / `archive_after_days` transitions, and runs an LLM review with
an explicit prompt that forbids improvising (`:414`: *"DO NOT call terminal to mv
skill directories into `.archive/`"*) and requires an accounting of every move
(`:577`). `agent/curator_backup.py` snapshots the tree first. The maximum
destructive action is an archive to `~/.hermes/skills/.archive/` (`:454`,
`:1477`), which is recoverable by `mv` or the `restore` verb.

**Config.** Under `curator:` in `hermes_cli/config_defaults.py:2046`:
`enabled: True`, `interval_hours: 24 * 7`, `min_idle_hours: 2`,
`stale_after_days: 30`, `archive_after_days: 90`, `consolidate: False`,
`prune_builtins: True`, `archive_ttl_days: 0`, plus a nested `backup` block. A
separate `curator` entry under the auxiliary task table (`:1126`) pins the
side-LLM used for review (`provider: "auto"`, `timeout: 600`, `reasoning_effort`).

## Dependencies

- **Used by:** the CLI (`hermes_cli/curator.py`, `hermes_cli/subcommands/skills.py`), the gateway (`gateway/run.py` auto-skill and `/skill` dispatch), the desktop palette via the backend's `commands.catalog`, and `agent/` turn assembly.
- **Uses:** `agent/auxiliary_client.py` for the review LLM, `tools/skills_hub.py` for hub/archive operations, `hermes_constants.get_hermes_home()` for every path, `hermes_cli/config_defaults.py` for defaults.

## Notable Patterns / Gotchas

- **`prune_builtins` defaults to `True` — bundled skills are NOT categorically off-limits.** `AGENTS.md` states the Curator only touches `created_by: "agent"` skills and that "bundled + hub-installed skills are off-limits." The code is conditional: `get_prune_builtins()` (`agent/curator.py:192`) returns `cfg.get("prune_builtins", True)` and the default in `config_defaults.py:2075` is `True`, so built-ins are seeded as candidates and become eligible for pruning (`curator.py:309`, `:1675`). Treat the doc's blanket claim as the intent and the flag as the behavior; check the flag before assuming a bundled skill is safe.
- **`created_by: "agent"` means "curator-managed", not "the agent wrote it."** The gate is `record.get("created_by") == "agent" or record.get("agent_created") is True` (`tools/skill_usage.py:505`). The module's own note (`:488`, issue #67140) records that the on-disk field name is historical and that records predating it carry no key at all, so authorship is unrecoverable for them — which is why `list-unmanaged` and `adopt` exist as verbs.
- **Pinning blocks deletion but not improvement.** `_pinned_guard()` (`tools/skill_manager_tool.py:274`) refuses `skill_manage(action="delete")` on a pinned skill while explicitly allowing patch/edit/write_file/remove_file, so the agent can keep improving a pinned playbook. Pinned rows are also exempt from every auto-transition and from the LLM review pass.
- **Cache-aware mutation is the default.** `hermes_cli/skills_hub.py` computes `invalidate_cache = "--now" in args` (`:1981`, `:2027`, `:2039`): a skills install defers invalidation to the next session unless the user passes `--now`. Any new slash command that mutates system-prompt state must follow this shape.
- **The sidecar is advisory.** A broken `.usage.json` never breaks the underlying tool call (`skill_usage.py:13`), so telemetry loss degrades curation quality but not capability.
- **`archive_ttl_days: 0` means archives are kept forever.** Raising it is the only path to automatic deletion of archived skills; the default never discards.
- **Cross-references:** the `skills` toolset (`skills_list`, `skill_view`, `skill_manage`) is covered in [tools.md](tools.md); the slash-command registry that surfaces skill commands is in [cli.md](cli.md); the desktop palette's curation of those commands is in [desktop.md](desktop.md).
