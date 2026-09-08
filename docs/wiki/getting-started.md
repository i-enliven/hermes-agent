# Getting Started

Two different things you might mean by "getting started": **running** Hermes as a
user, or **hacking** on this repository. Both are covered below. Every version,
command, and path here was read from `pyproject.toml`, `.python-version`,
`README.md`, and `scripts/run_tests.sh` at the commit recorded in
`.codewiki-state.json`.

## Prerequisites

| Requirement | Value | Source |
|---|---|---|
| Python | `>=3.11,<3.14` (`.python-version` pins `3.11`) | `pyproject.toml` `requires-python` |
| Package manager | `uv` (Astral's Rust resolver) | `README.md`, `pyproject.toml` `[tool.uv]` |
| Node.js | needed for the TUI (`ui-tui/`) and dashboard SPA (`web/`) | `package.json` |
| OS | Linux, macOS, WSL2, native Windows, Android/Termux | `README.md` |
| CLI tools | `ripgrep`, `ffmpeg`; Windows also gets a bundled portable Git Bash (MinGit) | `README.md` |
| Version of this tree | `hermes-agent` `0.20.3`, MIT, by Nous Research | `pyproject.toml` |

The upper bound on `requires-python` is deliberate, not cosmetic — `uv` resolves
the project's Python from it, and an inherited `UV_PYTHON` can otherwise drag in
an unsupported interpreter. See the comment block above `requires-python` in
`pyproject.toml`.

## Installation — as a user

Linux / macOS / WSL2 / Termux:

```bash
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
source ~/.bashrc      # or: source ~/.zshrc
hermes
```

Native Windows (PowerShell):

```powershell
iex (irm https://hermes-agent.nousresearch.com/install.ps1)
```

The installer provisions `uv`, Python 3.11, Node.js, `ripgrep`, `ffmpeg`, and — on
Windows — an isolated MinGit at `%LOCALAPPDATA%\hermes\git` used to run shell
commands. Native Windows installs under `%LOCALAPPDATA%\hermes`; everything else
under `~/.hermes`.

On Termux, Hermes installs the curated `.[termux]` extra rather than `.[all]`,
because the full extra pulls Android-incompatible voice dependencies.

## First Run

```bash
hermes setup          # full wizard: provider, keys, tools, platforms
hermes                # interactive CLI — start chatting
```

To skip collecting five separate API keys (model, web search, image gen, TTS,
cloud browser) in favour of one subscription:

```bash
hermes setup --portal # OAuth login, sets Nous as provider, enables Tool Gateway
hermes portal info    # inspect what is wired up
```

Daily-driver commands worth knowing on day one:

```bash
hermes model          # choose provider + model (switchable mid-conversation)
hermes tools          # curses UI: enable/disable toolsets per platform
hermes config get|set # read/write individual config.yaml values
hermes gateway        # run the messaging gateway
hermes doctor         # diagnose a broken install
hermes update         # self-update
```

## Installation — as a contributor

The supported path is to install normally, then work inside the checkout the
installer creates, because `hermes update`, the managed venv, lazy dependencies,
the gateway, and docs tooling all assume that layout:

```bash
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
cd "${HERMES_HOME:-$HOME/.hermes}/hermes-agent"
uv pip install -e ".[all,dev]"
scripts/run_tests.sh
```

Throwaway clone / CI fallback. Put the venv **outside** the source tree — a venv
inside the directory the agent operates from can be wiped by a relative-path
command the agent runs against its own checkout, destroying the running runtime
mid-session:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv venv ~/.hermes/venvs/hermes-dev --python 3.11
source ~/.hermes/venvs/hermes-dev/bin/activate
uv pip install -e ".[all,dev]"
scripts/run_tests.sh
```

### Running the tests

Always go through the wrapper; never call `pytest` directly. `scripts/run_tests.sh`
enforces CI parity: per-file subprocess isolation via
`scripts/run_tests_parallel.py` (no xdist, no module-level leakage), `TZ=UTC`,
`LANG=C.UTF-8`, `PYTHONHASHSEED=0`, credential env vars blanked, and venv probing
of `.venv` → `venv` → `~/.hermes/...`.

```bash
scripts/run_tests.sh                          # full suite
scripts/run_tests.sh -j 4                     # cap parallelism
scripts/run_tests.sh tests/agent/             # one directory
scripts/run_tests.sh tests/agent/ tests/acp/  # several roots
```

A test file that passes on retry is reported in a `⚠ FLAKY` section. That is a bug
to fix, not noise to ignore.

### TypeScript workspaces

The TUI, dashboard, and desktop app are separate Node workspaces; install from the
repo root so workspace linking resolves:

```bash
cd ui-tui && npm install && npm run dev      # watch-mode TUI
npm run typecheck                             # tsc --noEmit
npm test                                      # vitest
```

Check `package.json` in each workspace for the authoritative script names —
`ui-tui/`, `web/`, `apps/desktop/`, `apps/shared/`.

## Dependency pinning policy

Every dependency must carry an upper bound to limit supply-chain surface. This is
policy, enforced in review, and it exists because of the litellm compromise
(#2796, #2810) and the Mini Shai-Hulud worm campaign. In practice the manifest is
*stricter* than the guide's table: of 29 direct dependencies, 24 are exact `==`
pins and 5 use `>=floor,<next_major` — but **all 29 carry a ceiling**, which is the
rule that actually matters. A bare `>=X.Y.Z` with no ceiling is rejected in CI.
Real entries from `pyproject.toml`:

```toml
"openai==2.24.0",
"fire==0.7.1",
"rich==14.3.3",
"pydantic==2.13.4",
"prompt_toolkit==3.0.52",
"websockets==15.0.1",
"urllib3>=2.7.0,<3",          # one of the 5 ranged pins
"fastapi>=0.104.0,<1",
anthropic = ["anthropic==0.87.0"]   # CVE-2026-34450, CVE-2026-34452
modal     = ["modal==1.3.4"]
daytona   = ["daytona==0.155.0"]
```

The comment block above the dependency list records why the default moved to exact
pinning: a range like `mistralai>=2.3.0,<3` would have pulled the compromised
`mistralai` 2.4.6 release into every install before the quarantine. AGENTS.md also
requires commit-SHA pinning for `git+` URLs and GitHub Actions, and `==exact` for
CI-only pip installs — note that this checkout's `pyproject.toml` contains no
`git+` URLs and there is no `.github/workflows/` directory here, so those two rules
could not be confirmed from source.

Optional extras are declared under `[project.optional-dependencies]`; the ones that
matter day to day are `all`, `dev`, `termux`, `voice`, `wake`, `web`, `google`,
`youtube`, `modal`, `daytona`, `anthropic`, `acp`. `[tool.uv] override-dependencies`
carries documented, dated exceptions (currently `pynacl` and `cryptography`) with
removal conditions written into the comments — read them before "fixing" a pin.

## Configuration

The single most important rule: **`.env` is for secrets only.** API keys, tokens,
passwords. Every behavioural setting — timeouts, thresholds, feature flags, display
preferences, paths — belongs in `config.yaml`.

| Location | Holds |
|---|---|
| `~/.hermes/config.yaml` | all settings; merged over `DEFAULT_CONFIG` in `hermes_cli/config.py` |
| `~/.hermes/.env` | secrets only (see `.env.example` for the full catalogue) |
| `~/.hermes/logs/` | `agent.log` (INFO+), `errors.log` (WARNING+), `gateway.log` |
| `~/.hermes/skills/` | user + agent-authored skills; `.archive/` holds curatorial archives |
| `~/.hermes/plugins/` | user-installed plugins |
| `~/.hermes/profiles/<name>/` | fully isolated alternative instances |

Top-level `config.yaml` sections include `model`, `agent`, `terminal`,
`compression`, `display`, `stt`, `tts`, `memory`, `security`, `delegation`,
`smart_model_routing`, `checkpoints`, `auxiliary`, `curator`, `skills`, `gateway`,
`logging`, `cron`, `profiles`, `plugins`, `honcho`.

Adding a key to an existing section needs no version bump — the deep-merge handles
it. Bump `_config_version` only when existing user config must be actively
migrated. See [modules/cli.md](modules/cli.md) for the three separate config
loaders and which one you are in.

Working directory semantics differ by surface: the CLI uses the process cwd; the
gateway uses `terminal.cwd` from `config.yaml` and bridges it to `TERMINAL_CWD`
for child tools. `MESSAGING_CWD` and a `.env` `TERMINAL_CWD` are removed and now
only emit a deprecation warning.

## Profiles

A profile is a fully isolated instance with its own `HERMES_HOME` — config, keys,
memory, sessions, skills, gateway state. `hermes -p <name> …` selects one. The
mechanism is `_apply_profile_override()` in `hermes_cli/main.py`, which sets
`HERMES_HOME` before any module import, so every `get_hermes_home()` call site
scopes correctly. See [modules/state-persistence.md](modules/state-persistence.md).

## Terminal backends

The agent's shell can run against seven backends, all implementing the base class
in `tools/environments/base.py`:

`local` · `docker` · `ssh` · `singularity` · `modal` · `daytona` · `vercel_sandbox`

`modal` and `daytona` offer serverless persistence — the environment hibernates when
idle and wakes on demand, which is what makes a near-zero-cost idle deployment
possible. See [modules/tools.md](modules/tools.md).

## Common Workflows

### Add a slash command

One registry entry plus a handler — every downstream consumer (CLI dispatch,
gateway help, Telegram menu, Slack routing, autocomplete) derives from the
registry:

1. Add a `CommandDef` to `COMMAND_REGISTRY` in `hermes_cli/commands.py`.
2. Add a branch in `HermesCLI.process_command()` in `cli.py`.
3. If it should work over messaging, add a handler in `gateway/run.py`.

Adding an alias needs only the `aliases` tuple. See
[modules/cli.md](modules/cli.md).

### Add a tool

Prefer the Footprint Ladder — a new *core* tool is paid for on every API call:
extend existing code → CLI command + skill → service-gated tool (`check_fn`) →
plugin → MCP server in the catalog → new core tool as a last resort. For local or
custom capability, do not edit core at all: write
`~/.hermes/plugins/<name>/plugin.yaml` + `__init__.py` and call
`ctx.register_tool(...)`. See [modules/tools.md](modules/tools.md) and
[modules/plugins.md](modules/plugins.md).

### Add a messaging platform

Follow `gateway/platforms/ADDING_A_PLATFORM.md`, and take a token lock via
`acquire_scoped_lock()` from `gateway/status` in `connect()`/`start()` so two
profiles cannot use one credential. See [modules/gateway.md](modules/gateway.md).

### Schedule unattended work

```bash
hermes cron list
hermes cron add            # duration "30m", "every monday 9am", 5-field cron, or ISO one-shot
```

The agent schedules the same thing through the `cronjob` tool. Restart-surviving
work must use `cronjob` or `terminal(background=True, notify_on_complete=True)` —
background `delegate_task` is process-local. See
[modules/cron.md](modules/cron.md).

## Where To Go Next

- Orientation and the module map: [README.md](README.md)
- How the pieces connect: [architecture.md](architecture.md)
- Type-level view: [diagrams/class-diagram.md](diagrams/class-diagram.md)
- Runtime call paths: [diagrams/sequences.md](diagrams/sequences.md)
- The maintainers' own contribution rubric — read this before opening a PR:
  [`AGENTS.md`](../../AGENTS.md)
