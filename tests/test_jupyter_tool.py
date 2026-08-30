import asyncio
import json
import pytest
from unittest.mock import MagicMock, patch

from tools.jupyter_tool import (
    JupyterKernelManager,
    _get_jupyter_config,
    _strip_ansi,
    jupyter_execute,
)


def test_strip_ansi():
    raw = "\x1b[31mError:\x1b[0m Syntax fail"
    cleaned = _strip_ansi(raw)
    assert cleaned == "Error: Syntax fail"


def test_get_jupyter_config_defaults(monkeypatch):
    monkeypatch.delenv("JUPYTER_URL", raising=False)
    monkeypatch.delenv("JUPYTER_TOKEN", raising=False)
    cfg = _get_jupyter_config()
    assert "url" in cfg
    assert cfg["url"].endswith("/")
    assert "token" in cfg


def test_get_jupyter_config_env_overrides(monkeypatch):
    monkeypatch.setenv("JUPYTER_URL", "http://test-jupyter:9999")
    monkeypatch.setenv("JUPYTER_TOKEN", "custom-test-token")
    cfg = _get_jupyter_config()
    assert cfg["url"] == "http://test-jupyter:9999/"
    assert cfg["token"] == "custom-test-token"


def test_jupyter_execute_empty_code():
    res = jupyter_execute("")
    assert "[TOOL_ERROR]" in res or "Empty code block" in res


@patch("tools.jupyter_tool.requests.post")
@patch("tools.jupyter_tool.requests.delete")
def test_kernel_manager_lifecycle(mock_delete, mock_post):
    mock_post_resp = MagicMock()
    mock_post_resp.json.return_value = {"id": "mock-kernel-123"}
    mock_post_resp.raise_for_status = MagicMock()
    mock_post.return_value = mock_post_resp

    mgr = JupyterKernelManager()
    kernel_id = mgr.get_or_create_kernel("session-1")
    assert kernel_id == "mock-kernel-123"
    assert mock_post.called

    # Second call returns cached kernel_id
    mock_post.reset_mock()
    kernel_id_cached = mgr.get_or_create_kernel("session-1")
    assert kernel_id_cached == "mock-kernel-123"
    assert not mock_post.called

    # Close session calls delete endpoint
    mock_del_resp = MagicMock()
    mock_del_resp.status_code = 204
    mock_delete.return_value = mock_del_resp

    mgr.close_session("session-1")
    assert mock_delete.called


@pytest.mark.integration
def test_jupyter_execute_live_statefulness():
    """Live test against running Jupyter server (if active on localhost)."""
    try:
        # Step 1: Assign variable x in cell 1
        res1 = jupyter_execute("x = 42\nprint('Cell 1 set x')", task_id="test_live_session")
        if "[TOOL_ERROR]" in res1 or "Could not start Jupyter" in res1:
            pytest.skip("Local Jupyter server not available for live integration test")

        assert "Cell 1 set x" in res1

        # Step 2: Read variable x in cell 2 to confirm persistent statefulness
        res2 = jupyter_execute("print(f'Cell 2 x = {x}')", task_id="test_live_session")
        assert "Cell 2 x = 42" in res2

        # Step 3: Reset kernel and verify x is now undefined
        res3 = jupyter_execute("print(x)", reset_kernel=True, task_id="test_live_session")
        assert "Cell Execution Error" in res3 or "NameError" in res3
    finally:
        from tools.jupyter_tool import _kernel_manager
        _kernel_manager.close_session("test_live_session")
