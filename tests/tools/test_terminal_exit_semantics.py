"""Tests for terminal command exit code semantic interpretation."""

import json
import pytest

from tools.terminal_tool import (
    TERMINAL_EMPTY_OUTPUT_HINT,
    _interpret_exit_code,
    terminal_tool,
)


class TestInterpretExitCode:
    """Test _interpret_exit_code returns correct notes for known command semantics."""

    # ---- exit code 0 always returns None ----

    def test_success_returns_none(self):
        assert _interpret_exit_code("grep foo bar", 0) is None
        assert _interpret_exit_code("diff a b", 0) is None
        assert _interpret_exit_code("test -f /etc/passwd", 0) is None

    # ---- grep / rg family: exit 1 = no matches ----

    @pytest.mark.parametrize("cmd", [
        "grep 'pattern' file.txt",
        "egrep 'pattern' file.txt",
        "fgrep 'pattern' file.txt",
        "rg 'foo' .",
        "ag 'foo' .",
        "ack 'foo' .",
    ])
    def test_grep_family_no_matches(self, cmd):
        result = _interpret_exit_code(cmd, 1)
        assert result is not None
        assert "no matches" in result.lower()


    # ---- diff: exit 1 = files differ ----

    def test_diff_files_differ(self):
        result = _interpret_exit_code("diff file1 file2", 1)
        assert result is not None
        assert "differ" in result.lower()

    def test_colordiff_files_differ(self):
        result = _interpret_exit_code("colordiff file1 file2", 1)
        assert result is not None
        assert "differ" in result.lower()


    # ---- test / [: exit 1 = condition false ----

    def test_test_condition_false(self):
        result = _interpret_exit_code("test -f /nonexistent", 1)
        assert result is not None
        assert "false" in result.lower()


    # ---- find: exit 1 = partial success ----


    # ---- curl: various informational codes ----


    # ---- git: exit 1 is context-dependent ----


    # ---- pipeline / chain handling ----


    # ---- full paths ----


    # ---- env var prefix ----


    # ---- unknown commands return None ----


    # ---- edge cases ----


    def test_only_env_vars(self):
        """Command with only env var assignments, no actual command."""
        assert _interpret_exit_code("FOO=bar", 1) is None


def _minimal_terminal_config(cwd="/tmp"):
    return {
        "env_type": "local",
        "cwd": cwd,
        "timeout": 1,
        "host_cwd": None,
        "modal_mode": "auto",
        "docker_image": "",
        "singularity_image": "",
        "modal_image": "",
        "daytona_image": "",
    }


def _patch_common(monkeypatch, env):
    import tools.terminal_tool as tt

    monkeypatch.setattr(tt, "_active_environments", {"default": env})
    monkeypatch.setattr(tt, "_last_activity", {"default": 0})
    monkeypatch.setattr(tt, "_task_env_overrides", {})
    monkeypatch.setattr(tt, "_get_env_config", lambda: _minimal_terminal_config())
    monkeypatch.setattr(tt, "_start_cleanup_thread", lambda: None)
    monkeypatch.setattr(tt, "_resolve_container_task_id", lambda value: value or "default")
    monkeypatch.setattr(
        tt,
        "_check_all_guards",
        lambda command, env_type, **kwargs: {"approved": True},
    )


class TestEmptyOutputSignaling:
    """Test that terminal_tool signals EOF on empty output when exit code is 0."""

    def test_empty_output_on_exit_zero_signals_hint(self, monkeypatch):
        class FakeEnv:
            env = {}

            def execute(self, command, **kwargs):
                return {"output": "", "returncode": 0}

        _patch_common(monkeypatch, FakeEnv())
        result = json.loads(terminal_tool(command="true"))
        assert result["exit_code"] == 0
        assert result["output"] == "(empty output / 0 lines)"
        assert result["output"] == TERMINAL_EMPTY_OUTPUT_HINT
        assert result["error"] is None

    def test_whitespace_only_output_on_exit_zero_signals_hint(self, monkeypatch):
        class FakeEnv:
            env = {}

            def execute(self, command, **kwargs):
                return {"output": "   \n\t  \n  ", "returncode": 0}

        _patch_common(monkeypatch, FakeEnv())
        result = json.loads(terminal_tool(command="echo ''"))
        assert result["exit_code"] == 0
        assert result["output"] == "(empty output / 0 lines)"

    def test_nonempty_output_on_exit_zero_preserved(self, monkeypatch):
        class FakeEnv:
            env = {}

            def execute(self, command, **kwargs):
                return {"output": "all tests passed", "returncode": 0}

        _patch_common(monkeypatch, FakeEnv())
        result = json.loads(terminal_tool(command="pytest"))
        assert result["exit_code"] == 0
        assert result["output"] == "all tests passed"

    def test_empty_output_on_nonzero_exit_not_masked(self, monkeypatch):
        class FakeEnv:
            env = {}

            def execute(self, command, **kwargs):
                return {"output": "", "returncode": 1}

        _patch_common(monkeypatch, FakeEnv())
        result = json.loads(terminal_tool(command="false"))
        assert result["exit_code"] == 1
        assert result["output"] == ""
