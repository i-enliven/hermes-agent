---
name: jupyter-notebook-analysis
description: Stateful Python data analysis in a live Jupyter kernel.
version: 1.0.0
author: Nous Research
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [jupyter, notebook, data-science, analysis, machine-learning, repl]
    category: data-science
    related_skills: [jupyter-notebook, xlsx]
---

# Jupyter Notebook Analysis Skill

Run data science, machine learning, and multi-step Python work cell-by-cell in a
persistent kernel via the `jupyter_execute` tool. Kernel state survives across calls
in a session, so you import once, load once, and build up results incrementally.
Not for shipping reusable scripts (use `terminal` for those) or for editing real
notebook files on disk.

## When to Use

- Exploring a dataset: schema, distributions, nulls, quick aggregates.
- Iterative model work: train, inspect metrics, tweak, retrain without restarting.
- Inspecting databases: list tables, run queries, page through results.
- Any Python task where each step depends on objects produced by earlier steps.

## Prerequisites

- A reachable Jupyter server, configured under the `jupyter` section of
  `config.yaml` (`url`, `token`, `kernel_name`, `timeout`, `idle_ttl_secs`).
- The `jupyter` toolset enabled; the tool is gated on a configured server URL, so
  it is absent from the schema until one is set.

## How to Use

Call `jupyter_execute` with a `code` block per logical step:

```json
{ "code": "import pandas as pd\ndf = pd.read_csv('data/events.csv')\ndf.head(5)" }
```

The response carries stdout, the value of the last expression, and rich display
output (`df.head()`, `df.info()`, matplotlib figures) or a traceback on failure.

## Quick Reference

| Need | Do |
| ---- | -- |
| Install a missing package | `%pip install <package>` inside a cell |
| See a value | Leave it as the last line, or `print(...)` |
| Inspect a frame | `df.head()`, `df.info()`, `df.describe()` |
| Clear all state | `jupyter_execute(code=..., reset_kernel=true)` |
| Recover a dead kernel | Just call again — a fresh kernel is provisioned automatically |

## Procedure

1. **Setup cell**: import libraries and load data or open connections. Use paths
   relative to the working directory, or `~` / `$HOME` expansions for files in the
   user's home tree.
2. **Analysis cells**: transform, query, or train one step at a time. Reuse the
   names already in memory (`df`, `con`, `model`) instead of re-importing or
   re-reading.
3. **Verification**: end a cell with the expression you want to see, so the result
   comes back in the tool response before you move on.
4. **Context switch**: pass `reset_kernel=True` when starting an unrelated task or
   when the kernel holds too much data.

Example — inspect a SQLite database without re-connecting per cell:

```python
# Cell 1
import sqlite3
import pandas as pd

db_path = "analytics.db"  # or Path.home() / ".local" / "share" / "app" / "app.db"
con = sqlite3.connect(db_path)
tables = pd.read_sql("SELECT name FROM sqlite_master WHERE type='table'", con)
print(tables["name"].tolist())
```

```python
# Cell 2 — con and pd are still live in the kernel
df = pd.read_sql("SELECT * FROM sessions LIMIT 10", con)
df.info()
df.head()
```

## Pitfalls

- Re-importing or re-reading files every cell wastes time and can mask stale state;
  the kernel already holds your objects.
- A cell that raises leaves earlier definitions intact — fix the failing cell rather
  than resetting the kernel.
- Kernel memory is per session and is dropped when the session ends or on an
  explicit reset; it is not a persistence layer. Write results to disk if you need
  them later.
- Long-running cells are bounded by the configured `timeout`; chunk heavy work or
  move it to `terminal(background=True)`.

## Verification

- The last cell's output shows the expected columns, row counts, or metrics.
- Persisted artifacts (exported CSVs, saved models) exist on disk — confirm with
  `terminal` (`ls -lh`) or `read_file`.
