#!/usr/bin/env python3
"""
Jupyter Notebook / Server Tool -- Stateful Python REPL for Hermes Agent

Enables executing Python code inside a persistent Jupyter Kernel running on a
Jupyter Server (local or remote). Preserves variables, imported modules, functions,
and loaded data frames across multiple execution turns without requiring full script re-runs
or re-loading data into the LLM context window.

Configuration (`~/.hermes/config.yaml`):
```yaml
jupyter:
  url: "http://localhost:8888/"
  token: "sk-tailscale-jupyter"
  idle_ttl_secs: 1800
  kernel_name: "python3"
```

Can also be configured via environment variables:
- `JUPYTER_URL`
- `JUPYTER_TOKEN`
"""

import asyncio
import json
import logging
import os
import re
import threading
import time
import uuid
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests
try:
    from websockets.asyncio.client import connect as ws_connect
except ImportError:
    from websockets.client import connect as ws_connect

from tools.registry import registry, tool_error
logger = logging.getLogger(__name__)

# ANSI escape sequence scrubber for traceback outputs
_ANSI_ESCAPE_RE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

def _strip_ansi(text: str) -> str:
    """Strip ANSI escape codes from terminal outputs/tracebacks."""
    return _ANSI_ESCAPE_RE.sub('', text)


def _get_jupyter_config() -> Dict[str, Any]:
    """Load jupyter config from config.yaml or env vars."""
    config: Dict[str, Any] = {
        "url": "http://localhost:8888/",
        "token": "sk-tailscale-jupyter",
        "idle_ttl_secs": 1800,
        "kernel_name": "python3",
        "timeout": 300,
    }

    try:
        from hermes_cli.config import load_config
        user_cfg = load_config()
        j_cfg = user_cfg.get("jupyter", {})
        if isinstance(j_cfg, dict):
            config.update(j_cfg)
    except Exception as e:
        logger.debug("Could not load config.yaml for Jupyter tool: %s", e)

    # Environment variable overrides
    if os.getenv("JUPYTER_URL"):
        config["url"] = os.getenv("JUPYTER_URL")
    if os.getenv("JUPYTER_TOKEN"):
        config["token"] = os.getenv("JUPYTER_TOKEN")
    if os.getenv("JUPYTER_IDLE_TTL"):
        try:
            config["idle_ttl_secs"] = int(os.getenv("JUPYTER_IDLE_TTL", "1800"))
        except ValueError:
            pass

    # Normalize URL
    url = str(config.get("url", "http://localhost:8888/")).strip()
    if not url.endswith("/"):
        url += "/"
    config["url"] = url
    return config


class JupyterKernelManager:
    """Manages Jupyter server kernel sessions and idle TTL cleanup."""

    def __init__(self):
        self._kernels: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._cleanup_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def _ensure_cleanup_thread(self):
        with self._lock:
            if self._cleanup_thread is None or not self._cleanup_thread.is_alive():
                self._stop_event.clear()
                self._cleanup_thread = threading.Thread(target=self._idle_cleanup_loop, daemon=True)
                self._cleanup_thread.start()

    def _idle_cleanup_loop(self):
        """Periodically check for idle kernels and delete those exceeding TTL."""
        while not self._stop_event.is_set():
            time.sleep(30)
            cfg = _get_jupyter_config()
            ttl = int(cfg.get("idle_ttl_secs", 1800))
            now = time.monotonic()
            to_delete = []

            with self._lock:
                for session_key, info in list(self._kernels.items()):
                    if now - info.get("last_used", now) > ttl:
                        to_delete.append((session_key, info.get("kernel_id")))

            for session_key, kernel_id in to_delete:
                logger.info("Jupyter kernel %s for session %s exceeded idle TTL (%ds). Shutting down.", kernel_id, session_key, ttl)
                self._shutdown_kernel(kernel_id)
                with self._lock:
                    self._kernels.pop(session_key, None)

    def get_or_create_kernel(self, session_key: str, force_reset: bool = False) -> str:
        """Get an existing active kernel_id or create a new kernel."""
        self._ensure_cleanup_thread()
        cfg = _get_jupyter_config()

        with self._lock:
            if not force_reset and session_key in self._kernels:
                info = self._kernels[session_key]
                info["last_used"] = time.monotonic()
                return info["kernel_id"]

        if force_reset:
            self.close_session(session_key)

        kernel_id = self._spawn_kernel(cfg)
        with self._lock:
            self._kernels[session_key] = {
                "kernel_id": kernel_id,
                "created_at": time.monotonic(),
                "last_used": time.monotonic(),
            }
        return kernel_id

    def _spawn_kernel(self, cfg: Dict[str, Any]) -> str:
        """Create a new kernel on Jupyter Server via HTTP REST API."""
        base_url = cfg["url"]
        token = cfg.get("token", "")
        kernel_name = cfg.get("kernel_name", "python3")
        endpoint = urljoin(base_url, "api/kernels")

        headers = {}
        if token:
            headers["Authorization"] = f"token {token}"

        try:
            resp = requests.post(endpoint, headers=headers, json={"name": kernel_name}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            kernel_id = data.get("id")
            if not kernel_id:
                raise RuntimeError(f"Jupyter Server did not return a kernel ID: {data}")
            return kernel_id
        except Exception as exc:
            logger.error("Failed to spawn Jupyter kernel at %s: %s", endpoint, exc)
            raise RuntimeError(f"Could not start Jupyter kernel on {base_url}: {exc}") from exc

    def _shutdown_kernel(self, kernel_id: str):
        """Shut down a kernel via HTTP REST API."""
        cfg = _get_jupyter_config()
        base_url = cfg["url"]
        token = cfg.get("token", "")
        endpoint = urljoin(base_url, f"api/kernels/{kernel_id}")

        headers = {}
        if token:
            headers["Authorization"] = f"token {token}"

        try:
            resp = requests.delete(endpoint, headers=headers, timeout=10)
            if resp.status_code not in (200, 204, 404):
                logger.warning("Unexpected status %d when shutting down kernel %s", resp.status_code, kernel_id)
        except Exception as exc:
            logger.warning("Error shutting down kernel %s: %s", kernel_id, exc)

    def close_session(self, session_key: str):
        """Close kernel for a specific session."""
        with self._lock:
            info = self._kernels.pop(session_key, None)
        if info:
            self._shutdown_kernel(info["kernel_id"])


_kernel_manager = JupyterKernelManager()


async def _execute_code_async(kernel_id: str, code: str, cfg: Dict[str, Any]) -> Tuple[str, bool]:
    """Execute code on the kernel via WebSocket channel and aggregate output."""
    base_url = cfg["url"]
    token = cfg.get("token", "")
    timeout = int(cfg.get("timeout", 300))

    parsed = urlparse(base_url)
    ws_scheme = "wss" if parsed.scheme == "https" else "ws"
    ws_url = f"{ws_scheme}://{parsed.netloc}{parsed.path}api/kernels/{kernel_id}/channels"
    if token:
        ws_url += f"?token={token}"

    msg_id = str(uuid.uuid4())
    req_payload = {
        "header": {
            "msg_id": msg_id,
            "username": "hermes",
            "session": str(uuid.uuid4()),
            "msg_type": "execute_request",
            "version": "5.3",
        },
        "parent_header": {},
        "metadata": {},
        "content": {
            "code": code,
            "silent": False,
            "store_history": True,
            "user_expressions": {},
            "allow_stdin": False,
            "stop_on_error": True,
        },
    }

    output_chunks = []
    has_error = False

    async with ws_connect(ws_url) as ws:
        await ws.send(json.dumps(req_payload))
        deadline = time.monotonic() + timeout

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                output_chunks.append("\n[Execution timed out after {timeout} seconds]")
                has_error = True
                break

            try:
                raw_msg = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                output_chunks.append(f"\n[Execution timed out after {timeout} seconds]")
                has_error = True
                break

            data = json.loads(raw_msg)
            parent_id = data.get("parent_header", {}).get("msg_id")
            if parent_id != msg_id:
                continue

            msg_type = data.get("msg_type")
            content = data.get("content", {})

            if msg_type == "stream":
                text = content.get("text", "")
                output_chunks.append(text)
            elif msg_type == "execute_result":
                res_data = content.get("data", {})
                if "text/plain" in res_data:
                    output_chunks.append(str(res_data["text/plain"]) + "\n")
            elif msg_type == "error":
                has_error = True
                ename = content.get("ename", "Error")
                evalue = content.get("evalue", "")
                tb = content.get("traceback", [])
                clean_tb = [_strip_ansi(line) for line in tb]
                if clean_tb:
                    output_chunks.append("\n".join(clean_tb) + "\n")
                else:
                    output_chunks.append(f"{ename}: {evalue}\n")
            elif msg_type == "status" and content.get("execution_state") == "idle":
                break

    full_output = "".join(output_chunks).strip()
    return full_output, has_error


def jupyter_execute(
    code: str,
    reset_kernel: bool = False,
    task_id: Optional[str] = None,
) -> str:
    """
    Executes Python code inside a persistent Jupyter/IPython kernel session.

    CRITICAL RUNTIME GUARANTEES:
    1. Statefulness: Variables, imported modules, functions, and loaded datasets defined
       in previous execution cells persist in memory for subsequent cells in this session.
    2. Efficiency: You do not need to re-import packages or re-load files if executed earlier.
    3. Output: Expressions on the last line are automatically evaluated and displayed.

    Args:
        code: Python code block to execute in the kernel.
        reset_kernel: Set to True to restart the Jupyter kernel (clears in-memory variables).
        task_id: Optional session identifier for kernel mapping.

    Returns:
        String containing stdout, execution result, or error traceback.
    """
    if not code or not code.strip():
        return tool_error("Empty code block provided to jupyter_execute.")

    session_key = task_id or "default_session"
    cfg = _get_jupyter_config()

    try:
        kernel_id = _kernel_manager.get_or_create_kernel(session_key, force_reset=reset_kernel)
        output, has_error = asyncio.run(_execute_code_async(kernel_id, code, cfg))

        if not output:
            output = "[Code executed successfully with no output]"

        if has_error:
            return f"Cell Execution Error:\n{output}"
        return output
    except Exception as exc:
        logger.error("jupyter_execute error for session %s: %s", session_key, exc, exc_info=True)
        return tool_error(f"Jupyter Kernel execution error: {exc}")


JUPYTER_EXECUTE_SCHEMA = {
    "name": "jupyter_execute",
    "description": (
        "Execute Python code inside a persistent Jupyter/IPython kernel session. "
        "Variables, functions, imported modules, and loaded data frames persist across cells. "
        "Use this for data analysis, iterative coding, model training, or stateful Python operations."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Python source code snippet to execute in the persistent kernel.",
            },
            "reset_kernel": {
                "type": "boolean",
                "description": "Optional. Set to True to restart the kernel and clear all in-memory variables.",
                "default": False,
            },
        },
        "required": ["code"],
    },
}


def _jupyter_execute_handler(args: Dict[str, Any], **kwargs) -> str:
    code = args.get("code", "")
    reset_kernel = bool(args.get("reset_kernel", False))
    task_id = kwargs.get("task_id")
    return jupyter_execute(code=code, reset_kernel=reset_kernel, task_id=task_id)


def check_jupyter_requirements() -> bool:
    """Check if Jupyter server is configured or reachable."""
    cfg = _get_jupyter_config()
    return bool(cfg.get("url"))


registry.register(
    name="jupyter_execute",
    toolset="jupyter",
    schema=JUPYTER_EXECUTE_SCHEMA,
    handler=_jupyter_execute_handler,
    check_fn=check_jupyter_requirements,
    emoji="🪐",
    max_result_size_chars=100_000,
)
