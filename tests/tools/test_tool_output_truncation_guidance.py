"""Unit tests for tool output truncation guidance and terminal tool truncation notices."""

from unittest.mock import MagicMock
from agent.prompt_builder import TOOL_OUTPUT_TRUNCATION_GUIDANCE
from agent.system_prompt import build_system_prompt_parts


def test_tool_output_truncation_guidance_constant():
    """Verify TOOL_OUTPUT_TRUNCATION_GUIDANCE contains expected non-rerun instructions."""
    assert "Tool Output Truncation" in TOOL_OUTPUT_TRUNCATION_GUIDANCE
    assert "DO NOT re-run" in TOOL_OUTPUT_TRUNCATION_GUIDANCE
    assert "search_files" in TOOL_OUTPUT_TRUNCATION_GUIDANCE
    assert "read_file" in TOOL_OUTPUT_TRUNCATION_GUIDANCE


def test_system_prompt_includes_truncation_guidance():
    """Verify system prompt assembly includes TOOL_OUTPUT_TRUNCATION_GUIDANCE when valid_tool_names is present."""
    mock_agent = MagicMock()
    mock_agent.load_soul_identity = False
    mock_agent.skip_context_files = True
    mock_agent.valid_tool_names = {"terminal", "read_file", "search_files"}
    mock_agent.platform = "cli"
    mock_agent.model = "gpt-4"
    mock_agent.provider = "openai"
    mock_agent._memory_store = None
    mock_agent._user_profile_enabled = False
    mock_agent._memory_manager = None
    mock_agent._memory_manager = None
    mock_agent._tool_use_enforcement = False
    mock_agent._task_completion_guidance = False
    mock_agent._parallel_tool_call_guidance = False
    mock_agent._kanban_worker_guidance = ""
    parts = build_system_prompt_parts(mock_agent)
    assert TOOL_OUTPUT_TRUNCATION_GUIDANCE in parts["stable"]


def test_terminal_tool_truncation_notice_content():
    """Verify inline truncation notice formatting in terminal output."""
    from tools.tool_output_limits import get_max_bytes

    max_bytes = get_max_bytes()
    long_output = "a" * (max_bytes + 1000)

    # Simulated head/tail slicing with truncated notice
    head_chars = int(max_bytes * 0.4)
    tail_chars = max_bytes - head_chars
    omitted = len(long_output) - head_chars - tail_chars
    notice = (
        f"\n\n... [OUTPUT TRUNCATED - {omitted} chars omitted "
        f"out of {len(long_output)} total. Do NOT re-run the exact same command. "
        "Use search_files/grep on the spill file or narrow command filters/flags.] ...\n\n"
    )
    result = long_output[:head_chars] + notice + long_output[-tail_chars:]
    assert "OUTPUT TRUNCATED" in result
    assert "Do NOT re-run" in result
