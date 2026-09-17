---
name: time-travel
description: "Master conversation time-travel, step rollback, and lean context exploration in Hermes."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [time-travel, context-management, rollback, rewind, exploration, subagents, tokens]
    related_skills: [hermes-agent]
---

# Time Travel & Step Rollback in Hermes Agent

The `time_travel` tool and `/rewind` command allow you to explore codebases, inspect verbose files, run test scripts, and search the web, then **travel back in time** to an earlier step with distilled findings—completely shedding intermediate token bloat.

---

## 1. Why Time Travel?

During complex tasks, exploration often floods your context:
- Grepping 50 files or reading thousands of log lines
- Scraping multi-page web articles or documentation
- Trying speculative implementations or hypotheses that fail

Retaining all that raw text causes:
1. **Context Window Exhaustion**: Approaching context caps.
2. **Attention & Reasoning Degradation**: The model loses focus on the primary user objective amid thousands of lines of search noise.
3. **Escalating Token Costs**: Every subsequent turn re-submits all discarded exploration tokens.

**Time Travel allows you to backtrack cleanly.** You keep the conclusions, discard the noise, and continue from your decision point.

---

## 2. Step Notation: Turns vs. Cycles

Hermes indexes conversation progress across two dimensions:

| Step Identifier | Type | Meaning | When to Target |
| :--- | :--- | :--- | :--- |
| `"1"`, `"2"`, `"T"` | **User Turn** | The start of User Turn $T$, right before any agent actions in that turn. | When your entire line of reasoning for that user turn went down a wrong path and you want to start the turn fresh with the right information. |
| `"1.1"`, `"1.2"`, `"T.C"` | **Agent Cycle** | Agent Cycle $C$ within User Turn $T$ (the moment where you paused to think between tool calls). | When Step $T.C$ succeeded (e.g. found the right file), but subsequent steps ($T.(C+1), \dots$) accumulated noise. Rewinding to $T.C$ keeps Cycle $T.C$'s output and drops subsequent iterations. |

---

## 3. How to Use `time_travel`

When you have completed an exploratory detour and identified what you need:

1. **Identify the baseline step** you want to return to (e.g., Step `"1"` or `"1.1"`).
2. **Formulate high-density findings**:
   - Explicit file paths and line numbers
   - Extracted function signatures or configurations
   - Specific errors or conclusions
   - Do NOT just write "found the bug"; write the exact cause and line.
3. **Invoke `time_travel`**:
   ```json
   {
     "target_step": "1.1",
     "findings": "Verified that auth middleware in src/auth.py:L42 expects 'Bearer ' prefix. Token parsing error occurs on missing header."
   }
   ```
4. **Result**:
   - The engine soft-deletes intermediate messages.
   - The findings are appended to the target step as a `[Time Travel Observation]`.
   - Your next turn resumes cleanly from that baseline step with the findings in front of you.

---

## 4. `time_travel` vs. `delegate` (Subagents)

Both tools protect your context, but in opposite directions:

- **Forward Branching (`delegate_task`)**:
  - Use when you know *in advance* that a sub-task will be verbose or exploratory (e.g. "Search web documentation for 5 different APIs").
  - The child runs in a temporary isolated context and returns only the final summary.
- **Backward Backtracking (`time_travel`)**:
  - Use when you are already in the main conversation, tried an exploration or hypothesis, accumulated unexpected context noise, and now want to backtrack to before that detour began.

---

## 5. User Slash Command: `/rewind`

Users can also control time travel from the CLI or messaging gateways:
- `/rewind list` — Display recent turns and cycles with message previews and step IDs.
- `/rewind 1` — Roll back conversation history to User Turn 1.
- `/rewind 1.2` — Roll back to Agent Cycle 1.2.
- `/rewind 1 "New instructions or findings"` — Roll back to Turn 1 and steer with new instructions.
