---
name: jupyter-notebook-analysis
description: Perform stateful Python data analysis, machine learning, and iterative coding using the jupyter_execute tool.
---

# Jupyter Notebook Analysis & Stateful Python Skill

Use this skill when performing data science, machine learning, data processing, database inspection, or complex multi-step Python tasks with the `jupyter_execute` tool.

## Runtime Environment & Filesystem

1. **Host-Aligned Paths & Working Directory**:
   - The Jupyter kernel runs with `HOME=/home/ienliven` and the working directory set to `/home/ienliven`.
   - Host files, user databases, and repository checkouts located under `/home/ienliven/` (e.g. `Projects/`, `.local/share/`, datasets) are directly accessible with standard filesystem paths.
   - Relative file paths resolve relative to `/home/ienliven/`.

2. **Output Handling**:
   - Standard stdout (`print(...)`), expressions evaluated on the last line of a cell, and rich display objects (`display(...)`, `df.head()`, matplotlib figures) are captured and returned in the tool response.

## Core Guiding Principles

1. **Leverage Stateful Execution**:
   - Variables, imported packages, functions, and loaded DataFrames persist across cells for the duration of the agent session.
   - Do NOT re-import libraries or re-read datasets in subsequent cells if they were loaded in earlier cells.

2. **Cell-by-Cell Execution Structure**:
   - **Cell 1: Environment & Setup**: Import libraries (`pandas`, `numpy`, `sqlite3`, `torch`, etc.) and load datasets or establish connections.
   - **Cell 2+: Incremental Operations**: Perform data transformations, database queries, model training, or analysis step-by-step.
   - **Inspection**: Evaluate expressions on the last line of a cell or use `print()`, `df.info()`, and `df.head()` to verify state before moving to the next step.

3. **Inline Shell & Package Management**:
   - If a required Python package is missing in the kernel, install it directly inside a cell using `%pip install <package_name>`.

4. **Kernel Management & Resilience**:
   - **Automatic Recovery**: If a kernel terminates or becomes unresponsive, the tool automatically re-provisions a fresh kernel on the next call.
   - **Explicit Reset**: When switching to a completely unrelated sub-task, or if memory usage becomes excessive, set `reset_kernel=True` in `jupyter_execute` to clear in-memory state.

## Workflow Example

### Step 1: Initialize Environment and Load Data / Connect DB
```python
import pandas as pd
import sqlite3
import os

# Connect to database or read file from /home/ienliven
db_path = "/home/ienliven/.local/share/opencode/opencode.db"
con = sqlite3.connect(db_path)
tables = pd.read_sql_query("SELECT name FROM sqlite_master WHERE type='table'", con)
print("Available tables:", tables["name"].tolist())
```

### Step 2: Incremental Analysis (No re-connecting needed)
```python
# con and pd are already in memory from Step 1
df_sessions = pd.read_sql_query("SELECT * FROM sessions LIMIT 10", con)
df_sessions.info()
df_sessions.head()
```

### Step 3: Kernel Reset (When starting a fresh task)
Set `reset_kernel=True` when invoking `jupyter_execute` to clear kernel memory when switching context:
```json
{
  "code": "# Fresh task setup\nimport numpy as np",
  "reset_kernel": true
}
```
