#!/usr/bin/env python3
"""
Time Travel Tool Module - Step-Level Context Rollback for Clean Exploration

Allows the agent to backtrack to a previous User Turn (e.g. "1") or Agent Cycle
(e.g. "1.2"), shedding exploratory tool outputs (file views, search hits, logs)
while carrying forward only distilled findings to prevent context bloat.
"""

import re
from typing import Dict, Any, Optional

TIME_TRAVEL_SCHEMA = {
    "name": "time_travel",
    "description": (
        "Travel back in time to a previous User Turn (e.g. '1', '2') or Agent Cycle "
        "(e.g. '1.1', '1.2', '2.1') in the conversation history. "
        "All intermediate messages between the target step and now will be rolled back "
        "(soft-deleted from active context), and your specified 'findings' will be injected "
        "as an observation at that step. Use this tool after heavy exploration (searching codebase, "
        "browsing web pages, reading verbose logs) to return to your decision point armed with the "
        "distilled answer, keeping your context lean and high-signal."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "target_step": {
                "type": "string",
                "description": (
                    "The target turn or cycle to rewind to. Examples: "
                    "'1' (rewinds to start of User Turn 1), "
                    "'1.1' (rewinds to end of Agent Cycle 1.1, keeping Cycle 1.1's action), "
                    "'2.1' (rewinds to end of Cycle 1 in User Turn 2)."
                ),
            },
            "findings": {
                "type": "string",
                "description": (
                    "Key insights, conclusions, exact file paths, or extracted code snippets "
                    "discovered during subsequent exploration. These findings will be preserved "
                    "in the active context at the target step."
                ),
            },
            "restore_files": {
                "type": "boolean",
                "description": (
                    "Optional. If true, attempts to revert workspace files to the checkpoint captured "
                    "at the target turn (via CheckpointManager). Defaults to false."
                ),
                "default": False,
            },
        },
        "required": ["target_step", "findings"],
    },
}


def parse_step_identifier(step_str: str) -> Optional[tuple[int, int]]:
    """Parse a step identifier into (turn, cycle).

    "1" -> (1, 0)
    "1.2" -> (1, 2)
    Returns None if invalid format.
    """
    step_str = str(step_str).strip()
    match = re.match(r"^(\d+)(?:\.(\d+))?$", step_str)
    if not match:
        return None
    turn = int(match.group(1))
    cycle = int(match.group(2)) if match.group(2) is not None else 0
    return (turn, cycle)


def find_step_boundary_index(
    messages: list, target_turn: int, target_cycle: int = 0
) -> Optional[int]:
    """Locate the cut index in a messages list for a target turn and cycle.

    Returns the index of the message to retain (inclusive), or None if the
    step cannot be located in the messages list.
    """
    if not messages:
        return None
    from agent.context_compressor import is_user_originated_turn

    turn_count = 0
    for idx, msg in enumerate(messages):
        if is_user_originated_turn(msg):
            turn_count += 1
            if turn_count == target_turn:
                if target_cycle == 0:
                    return idx
                asst_cycles = 0
                for sub_idx in range(idx + 1, len(messages)):
                    sub_msg = messages[sub_idx]
                    if is_user_originated_turn(sub_msg):
                        break
                    if sub_msg.get("role") == "assistant":
                        asst_cycles += 1
                        if asst_cycles == target_cycle:
                            cut_idx = sub_idx
                            for tool_idx in range(sub_idx + 1, len(messages)):
                                if messages[tool_idx].get("role") == "tool":
                                    cut_idx = tool_idx
                                else:
                                    break
                            return cut_idx
                return None
    return None


def time_travel_tool(
    target_step: str,
    findings: str,
    restore_files: bool = False,
    agent: Optional[Any] = None,
    messages: Optional[list] = None,
) -> str:
    """Execute time travel request by staging pending rollback for the conversation loop."""
    parsed = parse_step_identifier(target_step)
    if not parsed:
        return f"Error: Invalid target_step format {target_step!r}. Use 'T' (e.g. '1') or 'T.C' (e.g. '1.2')."

    target_turn, target_cycle = parsed
    if target_turn < 1:
        return f"Error: Target turn must be >= 1 (got {target_turn})."

    if not findings or not findings.strip():
        return "Error: 'findings' must not be empty. Summarize the information discovered during exploration."

    if agent is not None:
        current_turn = getattr(agent, "_user_turn_count", 1) or 1
        current_cycle = getattr(agent, "_api_call_count", 1) or 1

        # Validation: target must be in the past
        if target_turn > current_turn or (target_turn == current_turn and target_cycle >= current_cycle):
            return (
                f"Error: Target step '{target_step}' is not in the past. "
                f"Current step is '{current_turn}.{current_cycle}'."
            )

        # Reality validation: target step must actually exist in session DB and active messages
        session_db = getattr(agent, "_session_db", None)
        session_id = getattr(agent, "session_id", None)
        if session_db is not None and session_id:
            boundary = session_db.resolve_step_boundary(session_id, target_step)
            if not boundary:
                try:
                    available = [s["step"] for s in session_db.list_session_steps(session_id)]
                except Exception:
                    available = []
                avail_str = f" Available steps: {', '.join(repr(s) for s in available)}." if available else ""
                return f"Error: Step '{target_step}' not found in active session.{avail_str}"

        active_msgs = messages or getattr(agent, "messages", None) or getattr(agent, "conversation_history", None)
        if active_msgs is not None:
            cut_idx = find_step_boundary_index(active_msgs, target_turn, target_cycle)
            if cut_idx is None:
                return (
                    f"Error: Step '{target_step}' not found in active conversation history "
                    f"(it may have been compacted or pruned)."
                )

        # Stage pending time travel for the conversation loop
        agent._pending_time_travel = {
            "target_step": target_step,
            "target_turn": target_turn,
            "target_cycle": target_cycle,
            "findings": findings.strip(),
            "restore_files": restore_files,
        }
    return (
        f"⏳ Time travel initiated to step '{target_step}'. "
        f"Context after step '{target_step}' will be rolled back and findings retained for the next turn."
    )


def check_time_travel_requirements() -> bool:
    """Check if time_travel tool is available."""
    return True


from tools.registry import registry

registry.register(
    name="time_travel",
    toolset="time_travel",
    schema=TIME_TRAVEL_SCHEMA,
    handler=lambda args, **kw: time_travel_tool(
        target_step=args.get("target_step", ""),
        findings=args.get("findings", ""),
        restore_files=args.get("restore_files", False),
        agent=kw.get("agent"),
        messages=kw.get("messages"),
    ),
    check_fn=check_time_travel_requirements,
    emoji="⏳",
)
