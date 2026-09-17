# Plan: `memory-lite` — a native-Python Hermes memory provider

A deliberately small replacement for the `memory_tencentdb` integration, written against
Hermes' real `MemoryProvider` contract instead of ported from the TypeScript engine.

Status: proposal. Every interface claim below was read out of the installed tree at
`~/.hermes/hermes-agent/` on 2026-09-12, not inferred.

---

## 0. Scope decision

Build **only the part of L0→L3 that pays for itself in Python**, and delete every piece
whose reason for existing was the language boundary.

### Kept

| Capability | Why it earns its place |
|---|---|
| Two-layer store: extracted **facts** + provenance | Minimum that gives cross-session recall. This is the whole product. |
| SQLite + **FTS5** (BM25) | stdlib-only, verified working in the venv (`sqlite 3.53.1`, `fts5` + `porter` create fine). No native dep, no server. |
| Async capture via `sync_turn` | Contract requires non-blocking; cheap to do correctly with one worker. |
| Two explicit search tools | The smoke test proved recall-by-tool is where the value actually lived. |
| `on_session_switch`, `on_pre_compress`, `backup_paths` | ~20 lines each, and each prevents a distinct class of data loss. |
| `recall_status()` | Free deterministic `🧠 recalled N memories` indicator; no model cooperation needed. |
| Mirror `on_memory_write` | Makes the plugin *complement* Hermes' built-in memory tool instead of competing with it. |

### Dropped, with the reason

| Dropped | Reason |
|---|---|
| **Sidecar process, HTTP layer, supervisor, circuit breaker, watchdog** | Their entire justification was "TS engine, Python host". Hermes is Python. This is ~1,800 of the ~5,900 lines gone, and with it the whole failure domain we exercised (dead sidecar, pipe deadlock, zombie reaping, `MEMORY_TENCENTDB_*`/`TDAI_*` aliasing). |
| **Embedding + vector layer, `hybrid`, RRF fusion** | In the live run `embeddingService: false` on *every* call and `strategy: hybrid` silently degraded to `keyword`. Shipping FTS-only removes a silent-degradation failure mode rather than implementing it better. |
| **jieba / `bm25.language: zh`** | Came up `zh` on English text and scored everything `0.000`. English-first wants `unicode61` + `porter`. |
| **L2 scene blocks + L3 persona synthesis** | The expensive tier: cadence-driven LLM pipeline, markdown file lifecycle, staleness cursors, `warmup_threshold`. Replaced by **one** extraction call at the session boundary (§4). |
| **Scheduler, queues, `CheckPointManager`** | The counter-drift bug they fixed (`recalculate()`, #337) exists only because they had counters that could drift. No scheduler → no drift class. |
| **Offload / Mermaid context engine** | Off by default upstream, needs a runtime patch of the host, orthogonal to memory. |
| **A searchable L0 raw-turn table** | Hermes already persists every session in `~/.hermes/state.db` (+ FTS5) and `~/.hermes/sessions/*.jsonl`, and already ships `session_search`. Rebuilding it is duplicate storage for no new capability. Keep a thin JSONL for *provenance* only. |

**Net:** ~1,100 lines of Python, zero non-stdlib runtime deps, zero processes, zero ports.

---

## 1. The contract this must satisfy

From `agent/memory_provider.py` (404 lines, read in full):

**Four `@abstractmethod`s — the only mandatory surface:**

| Member | Signature | Note |
|---|---|---|
| `name` | `@property -> str` | provider key |
| `is_available` | `() -> bool` | **"Should not make network calls — just check config and installed deps."** |
| `initialize` | `(session_id: str, **kwargs) -> None` | see kwargs below |
| `get_tool_schemas` | `() -> List[Dict]` | OpenAI function-calling shape |

Everything else has a default and is opted into: `prefetch`, `queue_prefetch`,
`recall_status`, `sync_turn`, `handle_tool_call`, `shutdown`, `unavailable_reason`,
`system_prompt_block`, `on_turn_start`, `on_session_end`, `on_session_switch`,
`on_pre_compress`, `on_delegation`, `on_memory_write`, `get_config_schema`,
`save_config`, `backup_paths`.

**`initialize()` kwargs (documented, must be honoured):**

- `hermes_home` (always) — **use this, never hardcode `~/.hermes`**; it is what makes the
  plugin profile-safe.
- `platform` (always) — `cli`, `telegram`, `cron`, …
- `agent_context` (often) — `primary` | `subagent` | `cron` | `flush`. The docstring is
  explicit: *"Providers should skip writes for non-primary contexts (cron system prompts
  would corrupt user representations)."*
- `agent_identity`, `agent_workspace`, `parent_session_id`, `user_id`, `user_id_alt`.

**Discovery + registration** (`plugins/memory/__init__.py`):

- Scan order: bundled `plugins/memory/<name>/` → **user `$HERMES_HOME/plugins/<name>/`** →
  project-local `./.hermes/plugins/<name>/` → pip entry points. Earlier source wins on a
  name collision.
- A directory qualifies if it holds `__init__.py` whose *source text* contains
  `register_memory_provider` or `MemoryProvider` (cheap text scan, no import).
- Entry point: top-level `def register(ctx)` calling `ctx.register_memory_provider(Instance())`
  — this is exactly what `plugins/memory/hindsight/__init__.py:2438` does.
- `plugin.yaml` supplies `name`, `version`, `description`, `pip_dependencies`,
  `requires_env`, `hooks`.

**Two behaviours already provided by the host that we must not duplicate:**

1. **Timeout + thread isolation.** `MemoryManager._prefetch_provider`
   (`agent/memory_manager.py:549-600`) runs our `prefetch()` on a daemon thread,
   `thread.join(self._external_prefetch_timeout)`, and on timeout logs *"timed out …
   skipping it until the stuck call returns"* and returns `""`. It also **skips the turn
   outright** if a previous prefetch thread for this provider is still alive.
   → We need **no** circuit breaker, **no** watchdog, **no** recovery machinery. We only
   need to be *fast*, and to keep our own deadline strictly below the host's.
2. **Framing + sanitisation.** `build_memory_context_block` (`:347-361`) wraps our return
   value in `<memory-context>` with a system note, and calls `sanitize_context()`; if that
   changes the text it logs *"memory provider returned pre-wrapped context; stripped"*.
   → **Return raw text. Do not emit our own fence.**
3. **Trivial-prompt gate.** `agent/memory_provider.py:75-101` exports
   `TRIVIAL_PROMPT_RE` / `is_trivial_prompt()` as the *single source of truth*, shared with
   the core gate, "so the two can never drift apart". → **Import and reuse it.**

### The KV-cache property, now settled from source

Earlier I could not determine where Hermes injects `prefetch()` output. It is now pinned:

`agent/turn_context.py:53-84` — `compose_user_api_content(content, ext_prefetch_cache, …)`
returns `content + "\n\n" + fenced_memory`, applied to the **API copy of the current turn's
user message**, with the result stamped on the `api_content` sidecar while the *stored*
content stays clean. Its docstring: *"Both are appended to the API copy of the user message
only — the stored content stays clean … which is the whole prompt-cache invariant: what
turn N sends must be what turn N+1 replays."*

Consequences we design to, rather than fight:

- Volatile per-turn recall belongs in `prefetch()` → lands at the **very end of the newest
  user message** → the whole prior prefix stays byte-identical → cache-friendly by
  construction.
- Static text belongs in `system_prompt_block()` → **must be constant for the life of the
  session**. Anything volatile here busts the prefix on every turn. This is precisely the
  mistake the TS provider made by pushing per-turn content toward the system region.
- Hermes' hard invariant, restated in the contributor guide: *"Never break prompt caching —
  don't change past context, toolsets, or the system prompt mid-conversation."*

---

## 2. Layout

```
~/.hermes/plugins/memory_lite/          # user-installed; profile-safe via hermes_home
├── plugin.yaml
├── __init__.py          # provider class + register(ctx)          ~320 lines
├── store.py             # SQLite/FTS5, single writer thread       ~230 lines
├── retrieve.py          # BM25 query build + sanitisation          ~120 lines
├── extract.py           # heuristic (no LLM) + LLM extraction      ~180 lines
├── config_schema.py     # loaded by path by the web server         ~60 lines
└── tests/
   └── test_provider.py                                              ~220 lines
```

`config_schema.py` is a separate file because the dashboard loads it **by path without
importing the agent runtime** (`plugins/memory/__init__.py`, `find_provider_dir`
docstring) — a provider with no on-disk directory loses its dashboard config panel.

---

## 3. Storage

One database at `<hermes_home>/memory-lite/store.db`, WAL, `synchronous=NORMAL`,
`busy_timeout=5000`. Under `hermes_home` means `hermes backup` captures it for free —
`backup_paths()` then returns `[]` (its documented purpose is state kept *outside*
`HERMES_HOME`).

```sql
CREATE TABLE IF NOT EXISTS facts (
  id            INTEGER PRIMARY KEY,
  fact          TEXT    NOT NULL,
  kind          TEXT    NOT NULL,          -- preference|correction|environment|procedure|event
  scope_user    TEXT    NOT NULL DEFAULT '',
  scope_profile TEXT    NOT NULL DEFAULT '',
  source        TEXT    NOT NULL,          -- turn|heuristic|llm|builtin-mirror
  session_id    TEXT    NOT NULL DEFAULT '',
  turn_no       INTEGER NOT NULL DEFAULT 0,
  ts            REAL    NOT NULL,
  last_used_ts  REAL    NOT NULL DEFAULT 0,
  use_count     INTEGER NOT NULL DEFAULT 0,
  active        INTEGER NOT NULL DEFAULT 1,
  supersedes    INTEGER,
  UNIQUE (fact, scope_user, scope_profile)
);
CREATE INDEX idx_facts_active ON facts(active, scope_user, scope_profile);

CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
  fact,
  tokenize = 'unicode61 remove_diacritics stopwords porter'
);
```

**FTS5 as a contentless index, not an external-content table.** An external-content table
(`content='facts'`) requires us to keep three raw triggers byte-exact with the parent;
getting that wrong is the classic silent-corruption bug (`INSERT`/`DELETE`/`UPDATE` trigger
mismatch). Storing `fact` twice in a ≤1,100-row table is irrelevant, and contentless
removes an entire bug class. Decision made; do not revisit.

Provenance JSONL at `<hermes_home>/memory-lite/turns.jsonl` — append-only, one line per
captured turn (`ts`, `session_id`, `turn_no`, `platform`, truncated `user`/`assistant`).
**Not indexed, not searched.** It exists so a wrong recall can be traced to the turn that
produced it — the white-box property worth keeping from the original design. If someone
wants to search history, that is `session_search`'s job.

**Retention.** Cap at `max_facts` (default 2,000). On overflow, soft-delete
(`active = 0`) the oldest by `COALESCE(NULLIF(last_used_ts,0), ts)` — recency of *use*, not
of insertion. Soft-delete keeps rows recoverable and makes `hermes memory-lite prune`
reversible.

---

## 4. Extraction: three tiers, paid for by the cheapest that works

The live run measured **>30 s** for one L1 extraction against the local backend, and **220 ms**
for the no-key failure. That is the wrong way round for a hot path, so extraction is moved
off the hot path entirely and tiered.

| Tier | Trigger | LLM? | Latency risk |
|---|---|---|---|
| **T0 raw capture** | every `sync_turn` | no | none — append only |
| **T1 heuristic facts** | every `sync_turn`, in the worker | **no** | none — regex only |
| **T2 LLM facts** | `on_session_end` + `on_pre_compress` | yes | off the hot path |

**T1 is the piece that makes the plugin useful from turn one with zero LLM cost.** A small
deterministic set of patterns, each emitting a `kind`-tagged fact:

```python
PATTERNS = [
    # explicit preference / aversion
    (r"\bi (?:prefer|would prefer|like|want|am used to)\b(.{8,160})",            "preference"),
    (r"\bi (?:don'?|do not|never|always|avoid)\b(.{8,160})",                      "preference"),
    (r"\b(?:please|from now on|going forward|for this project)\b(.{8,160})",     "preference"),
    # correction of the agent — highest signal, most likely to be re-needed
    (r"\b(?:no[,;]|that'?s (?:wrong|incorrect)|instead)\b(.{8,160})",             "correction"),
    # environment / toolchain facts, cheap and very reusable
    (r"\b(?:we|the project|this repo|it) (?:use|uses|run|runs|pin|pins|require|requires)\b(.{6,160})", "environment"),
]
```

Guards, all of them load-bearing:

- skip when `is_trivial_prompt(user_content)` — reuse the host's regex, do not re-implement.
- skip when the turn is shorter than `min_turn_chars` (default 24) or is a bare tool-result
  acknowledgement.
- **dedupe before insert**: exact `UNIQUE` on normalised text, plus an FTS probe for the
  candidate's top-3 hits; if a live fact has ≥0.8 token-set Jaccard similarity, `UPDATE`
  `last_used_ts` instead of inserting. Without this the store fills with near-duplicates
  and recall precision decays monotonically — the failure mode that quietly kills memory
  systems.
- never store anything matching a secret shape (`(?i)\b(sk|pk|ghp|glpat|xox[bbr])-[A-Za-z0-9_]{16,}\b`,
  PEM headers, `Bearer …`); drop and log at `debug`. Mirrors Hermes' own redaction posture.

**T2** fires at the two moments where content is *about to be lost* — session end and
pre-compression — which is exactly when extraction earns its keep, and it is one call per
session rather than a cadence-driven pipeline. Use the host's own model so there is no
second credential to configure (this is what removes the `TDAI_LLM_*` failure mode we hit):

```python
from agent.plugin_llm import ...   # ctx-provided host-owned completion
res = llm.complete(
    messages=[{"role": "system", "content": EXTRACT_SYSTEM},
              {"role": "user",   "content": transcript_window}],
    max_tokens=1200, timeout=25.0, purpose="memory-lite.extract",
)
```

Budget: **one call per session**, `timeout=25 s` (must stay under the host's prefetch
timeout only if we ever move it on-path; off-path we still cap it so a wedged backend
cannot hold `shutdown()`), `max_tokens=1200`, and skip entirely when the session has
fewer than `min_turns_for_extract` (default 4) captured turns. Parse a JSON array; on any
parse failure keep T1 facts and log — never raise into the host.

`on_pre_compress(messages)` additionally returns a short digest string. The ABC documents
this return value as *"text to include in the compression summary prompt so the compressor
preserves provider-extracted insights"* — so this is free insurance against the summariser
dropping a preference, and it is the single highest-value-per-line hook in the whole ABC.

---

## 5. Recall

```python
def prefetch(self, query: str, *, session_id: str = "") -> str:
    if self._closed or is_trivial_prompt(query):
        self._last_count = None
        return ""
    t_end = time.monotonic() + self._budget      # 0.12 s, ≪ host timeout
    rows = self._store.search(query, self._scope(session_id), self._max_results, t_end)
    self._last_count = len(rows)
    if not rows:
        return ""
    return "## Recalled from memory-lite\n" + "\n".join(
        f"- [{r.kind}] {r.fact}" for r in rows)
```

- **Return raw text, no fence** — the host wraps and sanitises (§1.2).
- **Deterministic for a given query** so the same turn replays identically; ordering is
  `bm25 DESC, ts DESC` with the tie-break explicit, never `RANDOM()`.
- `recall_status()` returns `RecallStatus("memory-lite", self._last_count)` on the same
  turn thread, or `None` when nothing was injected — the ABC requires it reflect only the
  **last** prefetch, never a stale count.
- `queue_prefetch()` stays a **no-op**. The ABC's background-prefetch design exists for
  network backends; ours is a local indexed read at sub-10 ms, so background caching would
  add a thread and a staleness question for nothing.

**BM25 column weights** favour the fact text over nothing else (single column) and rely on
the `porter` stemmer for `prefer/preference`, `run/running`:

```python
def build_match_query(q: str) -> str:
    toks = []
    for w in re.findall(r"[\w'-]+", q.lower()):
        if len(w) < 2 or w in STOP:
            continue
        w = w.replace('"', "").replace("*", "")
        if not w:
            continue
        toks.append(f'"{w}"*' if len(w) >= 4 else f'"{w}"')
    return " OR ".join(toks[:12])          # OR, not AND: recall over precision
```

Every token is quoted and `*`/`"` are stripped, so user text cannot inject FTS5 operators —
this is the same class of bug upstream fixed in `sanitize FTS5 query tokens to prevent MATCH
injection` (#529). Do it at the boundary, and unit-test it with adversarial input.

**No score threshold.** The live run showed `scoreThreshold: 0.3` being bypassed by a
*"document set is small — returning all matched results"* escape hatch, which behaves
discontinuously as the store grows. Instead: cap by `max_results` (5) and let BM25 ordering
do the work. A threshold tuned against nothing is worse than no threshold.

---

## 6. Tools

Two, both returning **JSON strings** (the ABC: *"Must return a JSON string"*), both
name-prefixed to avoid collision with the built-in `memory` tool and any other provider:

| Tool | Purpose | Args |
|---|---|---|
| `memory_lite_search` | structured facts | `query`, `kind?`, `limit?` |
| `memory_lite_remember` | agent-initiated durable write | `fact`, `kind?` |

`handle_tool_call` dispatches on name and returns `json.dumps({...})` on every path,
including errors: `{"ok": false, "error": "..."}`. An unknown name must raise
`NotImplementedError` per the base, but a *bad argument* must return JSON, not raise.

Deliberately **no** `memory_lite_forget` in v1 — deletion of a fact the agent did not write
is a user-facing action; expose it via the CLI subcommand where it can be confirmed.

---

## 7. System prompt block

Return a **constant** string for the life of the session (never per-turn), ~6 lines, telling
the model the store exists and how to use the two tools. Nothing volatile, no counts, no
timestamps, no "last updated" — the earlier review found the TS version's scene-navigation
block was cacheable *only* because it happened to contain no timestamps; make that a rule
rather than luck.

Cache the computed string on `self` at `initialize()` time so repeated calls are
byte-identical by construction, not by discipline.

---

## 8. Config

`get_config_schema()` — all non-secret, so `save_config()` writes our own file and no
`.env` entry is needed (the ABC requires *either* `save_config()` *or* env-only):

| key | default | note |
|---|---|---|
| `enabled` | `true` | |
| `max_facts` | `2000` | `minimum: 50` |
| `max_results` | `5` | `minimum: 1, maximum: 20` |
| `heuristic_enabled` | `true` | T1 |
| `llm_extraction` | `true` | T2; `false` ⇒ pure-stdlib, zero-LLM mode |
| `min_turns_for_extract` | `4` | |
| `retention_days` | `0` | `0` = keep until `max_facts` pressure |

Written to `<hermes_home>/memory-lite/config.json`. Secrets: **none** — the plugin reuses
the host model via `plugin_llm`, so there is no API key to manage. `requires_env: []`.

`is_available()` therefore checks only: config enabled, `sqlite3` importable, FTS5
creatable in a `:memory:` probe, directory writable. **No network**, per the ABC.

---

## 9. Build order — each phase independently shippable

| Phase | Deliverable | Gate (must pass before next) |
|---|---|---|
| **0. Skeleton** | `plugin.yaml`, `__init__.py` with the 4 abstracts, `prefetch` returning a fixed marker | `hermes doctor` clean; provider listed by `discover_memory_providers()`; marker visible in the wire request; `🧠` indicator renders |
| **1. Capture** | `store.py`, T0 JSONL + T1 heuristics, worker thread | `sync_turn` ×N then `sqlite3 store.db 'SELECT COUNT(*) FROM facts'` > 0; JSONL line count == turns; worker queue never grows past bound under a 500-turn burst |
| **2. Recall** | `retrieve.py`, `prefetch`, `recall_status` | seeded 30 facts; query for a planted fact returns it at rank 1; adversarial FTS input (`"OR"`, `*`, `NEAR/`, unbalanced `"`) returns results and never 500s; p95 `prefetch` < 15 ms |
| **3. Tools** | both tools + `handle_tool_call` | model calls `memory_lite_search` unprompted on a recall-probing question; every return path is valid JSON |
| **4. LLM tier** | T2 at `on_session_end` + `on_pre_compress` | one call per session (assert on a call counter); timeout path leaves T1 facts intact; `llm_extraction: false` ⇒ suite passes with no LLM stub |
| **5. Hardening** | `on_session_switch`, `on_memory_write`, `backup_paths`, `get_config_schema`/`save_config`, `unavailable_reason`, CLI | `/reset` then a write lands under the **new** session id; built-in `memory` add appears as a `builtin-mirror` fact; `hermes memory-lite stats` works |

Ship after Phase 3 if only one thing is wanted: **Phases 0–3 deliver useful recall with zero
LLM calls.** That is the point of the tiering — the LLM is an enhancement, not a dependency,
which is the exact inversion of the system we just tested.

---

## 10. Pitfalls (each one is a bug we or upstream already had)

1. **`on_session_switch` is not optional if you cache session state.** `/reset`, `/new`,
   `/branch`, `/resume` and context compression all reassign `session_id` *without tearing
   the provider down*. Handle `reset=True` by flushing per-session buffers; handle
   `rewound=True` by invalidating caches. Miss this and facts are attributed to a dead
   session — silent, permanent mislabelling.
2. **`agent_context != "primary"` must skip writes.** Cron/subagent/flush contexts would
   otherwise write system prompts into the user's memory. The ABC says so explicitly.
3. **Never pre-wrap the prefetch output.** The host strips it and logs a warning; a
   double fence also wastes tokens on every turn.
4. **`handle_tool_call` must always return JSON**, including on error.
5. **`is_available()` must not touch the network.** It gates activation during agent init.
6. **Use the `hermes_home` kwarg**, never `~/.hermes` — otherwise profiles silently share a
   store. (Contributor guide: *"Use `get_hermes_home()` … never hardcode `~/.hermes`"*.)
7. **One SQLite writer.** Reads from the host's prefetch thread are fine under WAL; writes
   all go through the single worker. Do not write from `sync_turn` inline — that is the
   blocking-on-the-hot-path mistake.
8. **`shutdown()` must be bounded.** Flush the queue with a deadline (e.g. 2 s), then close.
   The TS gateway's `destroy timeout. Pending work will be recovered on next startup` was
   exactly an unbounded flush; we have no queue deep enough to excuse it.
9. **Do not mutate past messages** and keep `system_prompt_block()` constant — the hard
   prompt-cache invariant.
10. **Cap every string you store** (`fact` ≤ 400 chars, truncated with `…`) so one runaway
    paste cannot dominate BM25 scoring forever.

---

## 11. Testing

Per the contributor guide, tests redirect `HERMES_HOME` to a temp dir — **never touch real
`~/.hermes/`**. The provider class is directly instantiable, so the core needs no agent:

```python
def test_recall_ranks_planted_fact_first(tmp_path):
    p = MemoryLiteProvider()
    p.initialize("sess-a", hermes_home=str(tmp_path), platform="cli",
                  agent_context="primary")
    for u, a in TURNS:
        p.sync_turn(u, a, session_id="sess-a")
    p._store.flush(timeout=2.0)                  # deterministic: drain the worker
    out = p.prefetch("which package manager should we use", session_id="sess-a")
    assert "pnpm" in out
    assert p.recall_status().count >= 1
    p.shutdown()
```

Required cases: adversarial FTS tokens; secret-shape rejection; trivial-prompt short-circuit;
non-primary context writes nothing; `on_session_switch(reset=True)` clears buffers;
`llm_extraction: false` full pass; `max_facts` overflow soft-deletes by use-recency;
double `initialize()` (the host may call it more than once) is idempotent.

Run with the canonical runner so CI parity holds: `scripts/run_tests.sh
tests/.../test_provider.py`.

---

## 12. Explicit non-goals

- No server, no port, no subprocess, no supervisor.
- No embeddings, no vectors, no RRF, no `hybrid` strategy string.
- No scene blocks, no persona file, no scheduler, no checkpoints.
- No context offloading / Mermaid / context engine.
- No re-implementation of session search (`session_search` owns it).
- No Chinese tokenisation. Add `simple` tokenizer + a segmentation step later **only** if a
  real Chinese workload appears; do not pre-build it.

---

## 13. Open questions for you

1. **Where should it live?** `~/.hermes/plugins/memory_lite/` (user-level, survives Hermes
   upgrades, no fork of core) is the default this plan assumes. Bundling into
   `~/.hermes/hermes-agent/plugins/memory/` would give it the same first-class status as
   `hindsight`/`mem0` but couples it to the upstream tree.
2. **One external provider is allowed at a time** (`MemoryManager` enforces it to prevent
   tool-schema bloat). Confirm nothing else is set as `memory.provider`, or this silently
   never activates.
3. **Do you want the built-in `memory` tool mirrored in?** §Kept says yes (it complements
   rather than competes), but if you would rather the two stay fully independent, drop
   `on_memory_write` — it is ~15 lines.
4. **Is `memory_lite_remember` wanted in v1**, or read-only first? Read-only is the smaller
   commitment and still delivers the recall that motivated the exercise.