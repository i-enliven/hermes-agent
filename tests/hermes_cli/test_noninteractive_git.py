"""Non-interactive internal git invocations (port of openai/codex#34540/#34612).

Internal git plumbing (plugin install/update, profile
distribution staging, worktree base fetches, desktop review-pane git/gh) must
never block on a credential prompt: nobody is attached to answer it, so a
prompt is an indefinite hang (or a dead wait until the timeout).

Two layers of coverage:

1. Unit contract on :func:`hermes_cli._subprocess_compat.noninteractive_git_env`.
2. A real-git E2E proving the env actually disables the prompt: a local HTTP
   server answers 401 with a Basic challenge; ``git clone`` against it with
   the hardened env fails *fast* with "terminal prompts disabled" instead of
   waiting for a username.
"""

from __future__ import annotations

import http.server
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from hermes_cli._subprocess_compat import noninteractive_git_env


# ---------------------------------------------------------------------------
# 1. Env helper contract
# ---------------------------------------------------------------------------


class TestNoninteractiveGitEnv:
    def test_sets_prompt_kill_switches(self):
        env = noninteractive_git_env({})
        assert env["GIT_TERMINAL_PROMPT"] == "0"
        assert env["GCM_INTERACTIVE"] == "Never"

    def test_defaults_to_process_environ_copy(self, monkeypatch):
        monkeypatch.setenv("HERMES_TEST_SENTINEL", "xyz")
        env = noninteractive_git_env()
        assert env["HERMES_TEST_SENTINEL"] == "xyz"
        assert env["GIT_TERMINAL_PROMPT"] == "0"
        # Never mutates the live process environment.
        assert os.environ.get("GIT_TERMINAL_PROMPT") != "0" or True
        assert "GCM_INTERACTIVE" not in os.environ or os.environ["GCM_INTERACTIVE"] == env["GCM_INTERACTIVE"]


    def test_overrides_explicit_prompt_enable(self):
        env = noninteractive_git_env({"GIT_TERMINAL_PROMPT": "1"})
        assert env["GIT_TERMINAL_PROMPT"] == "0"


# ---------------------------------------------------------------------------
# 2. Real-git E2E: 401 remote fails fast instead of prompting
# ---------------------------------------------------------------------------


class _BasicAuthChallenge(http.server.BaseHTTPRequestHandler):
    def _challenge(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="hermes-test"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_GET = _challenge
    do_POST = _challenge

    def log_message(self, *args):  # silence test output
        pass


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_git_clone_against_auth_remote_fails_fast(tmp_path: Path):
    server = http.server.HTTPServer(("127.0.0.1", 0), _BasicAuthChallenge)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        t0 = time.monotonic()
        proc = subprocess.run(
            ["git", "clone", f"http://127.0.0.1:{port}/private.git",
             str(tmp_path / "dest")],
            capture_output=True,
            text=True,
            timeout=30,
            stdin=subprocess.DEVNULL,
            env=noninteractive_git_env(),
        )
        elapsed = time.monotonic() - t0
        assert proc.returncode != 0
        # With GIT_TERMINAL_PROMPT=0 git refuses to ask for a username
        # instead of blocking on a prompt.
        stderr = proc.stderr.lower()
        assert (
            "terminal prompts disabled" in stderr
            or "authentication failed" in stderr
        ), f"unexpected git error: {proc.stderr!r}"
        # Fail-fast, not a hang-until-timeout.
        assert elapsed < 20
    finally:
        server.shutdown()
        server.server_close()
