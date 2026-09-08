# Hermes Agent — Code Wiki

Reference documentation for the Hermes Agent codebase: what each subsystem is, how
it is wired, and which file to open when you need to change it. Generated from the
source tree at the commit recorded in `.codewiki-state.json`.

Hermes is a self-improving personal AI agent from Nous Research. One agent engine
(`AIAgent`) runs unchanged across a classic CLI, an Ink terminal UI, a
multi-platform messaging gateway, a web dashboard, an Electron desktop app, and an
ACP editor adapter, and it also runs unattended from a cron scheduler and a batch
trajectory runner. It learns across sessions — agent-authored skills, curated
memory, FTS5 search over its own past conversations — and it reaches the world
through tools: a real terminal, a browser, file editing, web search, image and
video generation. The engine is provider-agnostic; a plugin per inference backend
absorbs the dialect differences.

This wiki is the **what/how**. The maintainers' intent layer — what gets merged,
what gets rejected, and why certain limitations are deliberate — is
[`AGENTS.md`](../../AGENTS.md), and it is worth reading before this wiki if you
plan to contribute.

## Key Concepts

- **Narrow waist, broad edges** — the core exposes a small universal tool set; new
  capability arrives as a CLI command + skill, a service-gated tool, a plugin, or an
  MCP server, because every model tool is serialized and sent on every API call.
- **Toolset** — the unit of enable/disable. A tool is invisible to the model until
  its name is reachable through a toolset in `toolsets.py`; registration alone does
  nothing.
- **Prompt cache is sacred** — the system prompt is byte-stable for the life of a
  conversation and message roles strictly alternate. Context compression is the
  only sanctioned mid-conversation mutation.
- **Session-scoped surface** — capability that depends on *who is connected*
  (desktop panes, in-app browser, reactions) resolves from the session's platform,
  never from a process env var, because client and backend may be different machines.
- **Profiles** — fully isolated instances, each its own `HERMES_HOME`, selected
  before any import so every path helper scopes correctly.
- **Skills** — markdown procedures the agent loads on demand and can author itself;
  the Curator ages and archives the stale ones the agent wrote.
- **Plugins** — four independent discovery systems (general, memory providers,
  model providers, ABC families). Plugins never edit core files.
- **Delegation vs. Kanban** — `delegate_task` spawns ephemeral in-process
  subagents; Kanban is a durable SQLite work queue across profiles. Different
  durability guarantees, different tools.

## Entry Points

| Path | What it is |
|---|---|
| [`hermes`](../../hermes) | Python launcher calling `hermes_cli.main:main` (not a shell script) |
| [`hermes_cli/main.py`](../../hermes_cli/main.py) | argparse wiring for the subcommand tree; `_apply_profile_override()` runs first |
| [`cli.py`](../../cli.py) | `HermesCLI` — the interactive classic CLI, `process_command()`, `load_cli_config()` |
| [`run_agent.py`](../../run_agent.py) | `AIAgent` (`:412`), `run_conversation()` (`:8337`), `chat()` (`:8808`) |
| [`model_tools.py`](../../model_tools.py) | `get_tool_definitions()`, `handle_function_call()` |
| [`tools/registry.py`](../../tools/registry.py) | `ToolRegistry`, `discover_builtin_tools()` — AST-gated auto-discovery |
| [`gateway/run.py`](../../gateway/run.py) | `GatewayRunner` (`:6522`), `main()` (`:30574`) |
| [`tui_gateway/server.py`](../../tui_gateway/server.py) | JSON-RPC host behind the TUI; `_load_enabled_toolsets()` (`:4484`) |
| [`cron/scheduler.py`](../../cron/scheduler.py) | the tick loop and its hardening windows |
| [`acp_adapter/entry.py`](../../acp_adapter/entry.py) | ACP server for editor clients |
| [`mcp_serve.py`](../../mcp_serve.py) | stdio MCP server exposing messaging conversations as MCP tools |
| [`batch_runner.py`](../../batch_runner.py) | `BatchRunner` — parallel trajectory generation |

Installed console scripts, from `pyproject.toml`: `hermes`, `hermes-agent`,
`hermes-acp`.

## High-Level Architecture

Every surface constructs an `AIAgent` and drives it through `run_conversation()`.
The loop sends the message list plus the session's tool schemas to a provider
transport; the model answers or requests tool calls; calls dispatch through
`model_tools.handle_function_call()` into the `ToolRegistry`; results append as
`tool` messages and the loop repeats under an iteration budget until the model
stops calling tools. All surfaces share one SQLite session store with FTS5 search,
which is what lets a conversation begun on Telegram resume in the TUI.

See [architecture.md](architecture.md) for the component map, the tool-registration
pipeline, and a turn traced end to end.

## Module Map

| Module | Purpose |
|---|---|
| [`agent core loop`](modules/agent-core.md) | `AIAgent`, the tool-calling loop, iteration budget, guards, compaction, prompt-cache discipline |
| [`tools & toolsets`](modules/tools.md) | registry, AST auto-discovery, toolset resolution, dispatch, `check_fn` gating, terminal backends |
| [`providers & transports`](modules/providers-transports.md) | provider profiles, API-mode transports, credential pool, capability registries |
| [`state & persistence`](modules/state-persistence.md) | SQLite session DB, FTS5 search, schema repair, profile-aware paths, logging |
| [`gateway`](modules/gateway.md) | platform adapters, session keying, delivery, slash interception, authz |
| [`cron`](modules/cron.md) | job store, tick loop, hardening windows, delivery, scheduler providers |
| [`cli`](modules/cli.md) | subcommands, the slash-command registry, config loaders, skins, curses UI |
| [`tui`](modules/tui.md) | Ink renderer + Python JSON-RPC host; PTY embedding in the dashboard |
| [`web dashboard`](modules/web-dashboard.md) | FastAPI server, React SPA, PTY bridge, dashboard auth plugins |
| [`desktop app`](modules/desktop.md) | Electron renderer, headless backend spawn, slash curation, session-scoped toolsets |
| [`acp adapter`](modules/acp.md) | Agent Client Protocol server for VS Code / Zed / JetBrains |
| [`plugins`](modules/plugins.md) | the four discovery systems, manifests, lifecycle hooks, compatibility contract |
| [`skills & curator`](modules/skills-curator.md) | skill surfaces, `SKILL.md` contract, usage telemetry, archival |
| [`delegation & kanban`](modules/delegation-kanban.md) | `delegate_task` subagents vs. the durable Kanban board |

## Diagrams

- [System architecture](architecture.md#system-diagram) — components and process
  boundaries
- [Tool registration flow](architecture.md#tool-registration-flow) — from module
  import to schema on the wire
- [Class diagram](diagrams/class-diagram.md) — core types and extension-point ABCs
- [Sequence diagrams](diagrams/sequences.md) — traced runtime call paths

## Reading Paths

**New to the repo** → this page, then [architecture.md](architecture.md), then
[modules/agent-core.md](modules/agent-core.md).

**Adding capability** → [modules/tools.md](modules/tools.md) for the Footprint
Ladder and the toolset gate, then [modules/plugins.md](modules/plugins.md) to pick
an extension point. Do not reach for a new core tool first.

**Fixing a messaging bug** → [modules/gateway.md](modules/gateway.md), paying
attention to the two message guards and the fail-closed secret scoping rules.

**Working on a UI surface** → identify which of the four chat surfaces you are in
(classic CLI, Ink TUI, dashboard-embedded TUI, Electron desktop). They are easy to
confuse and they have different owners; [modules/tui.md](modules/tui.md) and
[modules/desktop.md](modules/desktop.md) draw the lines.

**Onboarding an unfamiliar file** → find its module above, read the *Key Files* and
*Dependencies* sections, then open the file. The *Notable Patterns / Gotchas*
sections record the traps that cost the most debugging time.

## Getting Started

Setup, first run, contributor workflow, test runner, and the dependency-pinning
policy: [getting-started.md](getting-started.md).

## Provenance

Generated by the `code-wiki` skill from a static read of the working tree — no code
was executed to produce it. Every path, symbol, and line number was resolved
against source; where the maintainers' own documentation and the code disagreed, the
code won and the disagreement is noted in the page that covers it. Treat it as a
map, not as ground truth: re-run the skill after large refactors, and trust
`git log` and the test suite over any sentence here.
