"""Tests for hermes_cli.doctor."""

import os
import sys
import types
import io
import contextlib
from argparse import Namespace
from types import SimpleNamespace

import pytest

import hermes_cli.doctor as doctor
import hermes_cli.gateway as gateway_cli
from hermes_cli import doctor as doctor_mod
from hermes_cli.doctor import _has_provider_env_config


class TestDoctorPlatformHints:
    def test_termux_package_hint(self, monkeypatch):
        monkeypatch.setenv("TERMUX_VERSION", "0.118.3")
        monkeypatch.setenv("PREFIX", "/data/data/com.termux/files/usr")
        assert doctor._is_termux() is True
        assert doctor._python_install_cmd() == "python -m pip install"
        assert doctor._system_package_install_cmd("ripgrep") == "pkg install ripgrep"


    def test_sqlite_upgrade_hint_recreates_docker_containers(self, monkeypatch):
        monkeypatch.setattr(doctor, "detect_install_method", lambda _root: "docker")

        hint = doctor._sqlite_upgrade_hint()

        assert "docker pull nousresearch/hermes-agent:latest" in hint
        assert "recreate all Hermes containers" in hint
        assert "hermes update" not in hint

    def test_sqlite_upgrade_hint_keeps_git_runtime_repair(self):
        hint = doctor._sqlite_upgrade_hint("git")

        assert "run `hermes update`" in hint

    def test_sqlite_upgrade_hint_uses_pkg_for_apt_managed_install(self):
        hint = doctor._sqlite_upgrade_hint("apt")

        assert "run `pkg upgrade hermes-agent`" in hint
        assert "hermes update" not in hint

    def test_sqlite_upgrade_hint_preserves_nix_guidance_as_prose(self):
        guidance = doctor.recommended_update_command_for_method("nix")
        hint = doctor._sqlite_upgrade_hint("nix")

        assert guidance in hint
        assert f"run `{guidance}`" not in hint
        assert "hermes update" not in hint


class TestProviderEnvDetection:
    def test_detects_openai_api_key(self):
        content = "OPENAI_BASE_URL=http://localhost:1234/v1\nOPENAI_API_KEY=***"
        assert _has_provider_env_config(content)


    def test_returns_false_when_no_provider_settings(self):
        content = "TERMINAL_ENV=local\n"
        assert not _has_provider_env_config(content)


class TestDoctorToolAvailabilitySummary:
    def test_missing_api_key_summary_ignores_disabled_toolsets(self, monkeypatch):
        unavailable = [
            {"name": "rl", "missing_vars": ["TINKER_API_KEY"]},
            {"name": "web", "missing_vars": ["EXA_API_KEY"]},
        ]
        monkeypatch.setattr(doctor, "_enabled_cli_toolsets_for_doctor", lambda: {"web"})

        filtered = doctor._missing_api_key_toolsets_for_summary(unavailable)

        assert [item["name"] for item in filtered] == ["web"]


class TestDoctorEnvFileEncoding:
    """Regression for #18637 (bug 3): `hermes doctor` crashed on Windows
    Chinese locale (GBK) because `.env` was read with Path.read_text() which
    defaults to the system locale encoding, not UTF-8."""

    def test_doctor_reads_env_as_utf8_even_when_locale_is_not_utf8(
        self, monkeypatch, tmp_path
    ):
        import pathlib

        hermes_home = tmp_path / ".hermes"
        hermes_home.mkdir()
        # Write a UTF-8 .env containing an em dash (U+2014 = e2 80 94). The
        # 0x94 byte is exactly the one the issue reporter hit: it's invalid
        # as a GBK trailing byte in this position, so locale-default reads
        # raise UnicodeDecodeError on Chinese Windows.
        env_path = hermes_home / ".env"
        env_path.write_text(
            "OPENAI_API_KEY=sk-test  # em-dash here — should not crash\n",
            encoding="utf-8",
        )

        monkeypatch.setattr(doctor_mod, "HERMES_HOME", hermes_home)

        orig_read_text = pathlib.Path.read_text

        def gbk_like_read_text(self, encoding=None, errors=None, **kwargs):
            # Simulate a GBK locale: refuse to decode this specific UTF-8
            # .env unless the caller pins encoding="utf-8".
            if self == env_path and encoding != "utf-8":
                raise UnicodeDecodeError(
                    "gbk", b"\x94", 0, 1, "illegal multibyte sequence"
                )
            return orig_read_text(self, encoding=encoding, errors=errors, **kwargs)

        monkeypatch.setattr(pathlib.Path, "read_text", gbk_like_read_text)

        # Short-circuit the expensive tool-availability probe — we only
        # need doctor to reach the .env read without crashing.
        fake_model_tools = types.SimpleNamespace(
            check_tool_availability=lambda *a, **kw: (_ for _ in ()).throw(SystemExit(0)),
            TOOLSET_REQUIREMENTS={},
        )
        monkeypatch.setitem(sys.modules, "model_tools", fake_model_tools)

        # Run doctor. If the .env read still uses locale encoding, this
        # raises UnicodeDecodeError and the test fails.
        with pytest.raises(SystemExit):
            doctor_mod.run_doctor(Namespace(fix=False))


    def test_doctor_reads_invalid_utf8_env_via_latin1_fallback(
        self, monkeypatch, tmp_path
    ):
        """cp1252/latin-1 .env with ASCII provider hints must not abort doctor."""
        hermes_home = tmp_path / ".hermes"
        hermes_home.mkdir()
        env_path = hermes_home / ".env"
        # 0xff is invalid UTF-8; latin-1 decodes it. Keep an ASCII provider key
        # so the scan still reports a configured endpoint/key.
        env_path.write_bytes(b"OPENAI_API_KEY=sk-test\xff\n")

        monkeypatch.setattr(doctor_mod, "HERMES_HOME", hermes_home)

        fake_model_tools = types.SimpleNamespace(
            check_tool_availability=lambda *a, **kw: (_ for _ in ()).throw(SystemExit(0)),
            TOOLSET_REQUIREMENTS={},
        )
        monkeypatch.setitem(sys.modules, "model_tools", fake_model_tools)

        with pytest.raises(SystemExit):
            doctor_mod.run_doctor(Namespace(fix=False))


class TestDoctorToolAvailabilityOverrides:


    def test_marks_kanban_available_only_when_missing_worker_env_gate(self, monkeypatch):
        monkeypatch.setattr(doctor, "_honcho_is_configured_for_doctor", lambda: False)
        monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)

        available, unavailable = doctor._apply_doctor_tool_availability_overrides(
            [],
            [{"name": "kanban", "env_vars": [], "tools": ["kanban_show"]}],
        )

        assert available == ["kanban"]
        assert unavailable == []

    def test_leaves_kanban_unavailable_when_worker_env_is_set(self, monkeypatch):
        monkeypatch.setenv("HERMES_KANBAN_TASK", "probe")
        kanban_entry = {"name": "kanban", "env_vars": [], "tools": ["kanban_show"]}

        available, unavailable = doctor._apply_doctor_tool_availability_overrides(
            [],
            [kanban_entry],
        )

        assert available == []
        assert unavailable == [kanban_entry]




class TestHonchoDoctorConfigDetection:
    def test_reports_configured_when_enabled_with_api_key(self, monkeypatch):
        fake_config = SimpleNamespace(enabled=True, api_key="***")

        monkeypatch.setattr(
            "plugins.memory.honcho.client.HonchoClientConfig.from_global_config",
            lambda: fake_config,
        )

        assert doctor._honcho_is_configured_for_doctor()








def test_doctor_reports_vercel_backend_diagnostics(monkeypatch, tmp_path):
    monkeypatch.setenv("TERMINAL_ENV", "vercel_sandbox")
    monkeypatch.setenv("TERMINAL_VERCEL_RUNTIME", "python3.13")
    monkeypatch.setenv("TERMINAL_CONTAINER_DISK", "2048")
    monkeypatch.setenv("VERCEL_TOKEN", "super-secret-value")
    monkeypatch.delenv("VERCEL_PROJECT_ID", raising=False)
    monkeypatch.setenv("VERCEL_TEAM_ID", "team")
    monkeypatch.setattr(doctor_mod.importlib.util, "find_spec", lambda name: object() if name == "vercel" else None)

    fake_model_tools = types.SimpleNamespace(
        check_tool_availability=lambda *a, **kw: ([], []),
        TOOLSET_REQUIREMENTS={},
    )
    monkeypatch.setitem(sys.modules, "model_tools", fake_model_tools)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        doctor_mod.run_doctor(Namespace(fix=False))

    out = buf.getvalue()
    assert "Vercel runtime" in out
    assert "python3.13" in out
    assert "Vercel custom disk unsupported" in out
    assert "Vercel auth incomplete" in out
    assert "VERCEL_PROJECT_ID" in out
    assert "Vercel auth mode: incomplete access token" in out
    assert "Vercel auth present env: VERCEL_TOKEN, VERCEL_TEAM_ID" in out
    assert "Vercel auth missing env: VERCEL_PROJECT_ID" in out
    assert "super-secret-value" not in out
    assert "snapshot filesystem only" in out


# ── Memory provider section (doctor should only check the *active* provider) ──


class TestDoctorMemoryProviderSection:
    """The ◆ Memory Provider section should respect memory.provider config."""

    def _make_hermes_home(self, tmp_path, provider=""):
        """Create a minimal HERMES_HOME with config.yaml."""
        home = tmp_path / ".hermes"
        home.mkdir(parents=True, exist_ok=True)
        import yaml
        config = {"memory": {"provider": provider}} if provider else {"memory": {}}
        (home / "config.yaml").write_text(yaml.dump(config))
        return home

    def _run_doctor_and_capture(self, monkeypatch, tmp_path, provider=""):
        """Run doctor and capture stdout."""
        home = self._make_hermes_home(tmp_path, provider)
        monkeypatch.setattr(doctor_mod, "HERMES_HOME", home)
        monkeypatch.setattr(doctor_mod, "PROJECT_ROOT", tmp_path / "project")
        monkeypatch.setattr(doctor_mod, "_DHH", str(home))
        (tmp_path / "project").mkdir(exist_ok=True)

        # Stub tool availability (returns empty) so doctor runs past it
        fake_model_tools = types.SimpleNamespace(
            check_tool_availability=lambda *a, **kw: ([], []),
            TOOLSET_REQUIREMENTS={},
        )
        monkeypatch.setitem(sys.modules, "model_tools", fake_model_tools)

        # Stub auth checks to avoid real API calls
        try:
            from hermes_cli import auth as _auth_mod
            monkeypatch.setattr(_auth_mod, "get_codex_auth_status", lambda: {})
            monkeypatch.setattr(_auth_mod, "get_xai_oauth_auth_status", lambda: {})
        except Exception:
            pass

        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            doctor_mod.run_doctor(Namespace(fix=False))
        return buf.getvalue()

    def test_no_provider_shows_builtin_ok(self, monkeypatch, tmp_path):
        out = self._run_doctor_and_capture(monkeypatch, tmp_path, provider="")
        assert "Memory Provider" in out
        assert "Built-in memory active" in out
        # Should NOT mention Honcho or Mem0 errors
        assert "Honcho API key" not in out
        assert "Mem0" not in out


    def test_mem0_provider_not_installed_shows_fail(self, monkeypatch, tmp_path):
        # Make mem0 import fail
        monkeypatch.setitem(sys.modules, "plugins.memory.mem0", None)
        out = self._run_doctor_and_capture(monkeypatch, tmp_path, provider="mem0")
        assert "Memory Provider" in out
        assert "Built-in memory active" not in out


def test_run_doctor_termux_treats_docker_and_browser_warnings_as_expected(monkeypatch, tmp_path):
    helper = TestDoctorMemoryProviderSection()
    monkeypatch.setenv("TERMUX_VERSION", "0.118.3")
    monkeypatch.setenv("PREFIX", "/data/data/com.termux/files/usr")

    real_which = doctor_mod.shutil.which

    def fake_which(cmd):
        if cmd in {"docker", "node", "npm"}:
            return None
        return real_which(cmd)

    monkeypatch.setattr(doctor_mod.shutil, "which", fake_which)

    out = helper._run_doctor_and_capture(monkeypatch, tmp_path, provider="")

    assert "Docker backend is not available inside Termux" in out
    assert "Node.js not found (browser tools are optional in the tested Termux path)" in out
    assert "Install Node.js on Termux with: pkg install nodejs" in out
    assert "Termux browser setup:" in out
    assert "1) pkg install nodejs" in out
    assert "2) npm install -g agent-browser" in out
    assert "3) agent-browser install" in out
    assert "Termux compatibility fallbacks:" in out
    assert "use .[termux-all] for broad compatibility" in out
    assert "Matrix E2EE extra is excluded on Termux" in out
    assert "Local faster-whisper extra is excluded on Termux" in out
    assert "STT fallback: use Groq Whisper (set GROQ_API_KEY) or OpenAI Whisper (set VOICE_TOOLS_OPENAI_KEY)." in out
    assert "docker not found (optional)" not in out


def test_run_doctor_accepts_named_provider_from_providers_section(monkeypatch, tmp_path):
    home = tmp_path / ".hermes"
    home.mkdir(parents=True, exist_ok=True)

    import yaml

    (home / "config.yaml").write_text(
        yaml.dump(
            {
                "model": {
                    "provider": "volcengine-plan",
                    "default": "doubao-seed-2.0-code",
                },
                "providers": {
                    "volcengine-plan": {
                        "name": "volcengine-plan",
                        "base_url": "https://ark.cn-beijing.volces.com/api/coding/v3",
                        "default_model": "doubao-seed-2.0-code",
                        "models": {"doubao-seed-2.0-code": {}},
                    }
                },
            }
        )
    )

    monkeypatch.setattr(doctor_mod, "HERMES_HOME", home)
    monkeypatch.setattr(doctor_mod, "PROJECT_ROOT", tmp_path / "project")
    monkeypatch.setattr(doctor_mod, "_DHH", str(home))
    (tmp_path / "project").mkdir(exist_ok=True)

    fake_model_tools = types.SimpleNamespace(
        check_tool_availability=lambda *a, **kw: ([], []),
        TOOLSET_REQUIREMENTS={},
    )
    monkeypatch.setitem(sys.modules, "model_tools", fake_model_tools)

    try:
        from hermes_cli import auth as _auth_mod
        monkeypatch.setattr(_auth_mod, "get_codex_auth_status", lambda: {})
        monkeypatch.setattr(_auth_mod, "get_xai_oauth_auth_status", lambda: {})
    except Exception:
        pass

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        doctor_mod.run_doctor(Namespace(fix=False))

    out = buf.getvalue()
    assert "model.provider 'volcengine-plan' is not a recognised provider" not in out


def test_run_doctor_accepts_stable_key_when_provider_name_differs(
    monkeypatch, tmp_path
):
    home = tmp_path / ".hermes"
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        "model:\n"
        "  provider: custom:local-127.0.0.1:11434\n"
        "  default: qwen3.5:9b\n"
        "providers:\n"
        "  local-127.0.0.1:11434:\n"
        "    name: Local Ollama\n"
        "    base_url: http://127.0.0.1:11434/v1\n"
        "    default_model: qwen3.5:9b\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(doctor_mod, "HERMES_HOME", home)
    monkeypatch.setattr(doctor_mod, "PROJECT_ROOT", tmp_path / "project")
    monkeypatch.setattr(doctor_mod, "_DHH", str(home))
    (tmp_path / "project").mkdir(exist_ok=True)

    fake_model_tools = types.SimpleNamespace(
        check_tool_availability=lambda *a, **kw: ([], []),
        TOOLSET_REQUIREMENTS={},
    )
    monkeypatch.setitem(sys.modules, "model_tools", fake_model_tools)

    try:
        from hermes_cli import auth as _auth_mod
        monkeypatch.setattr(_auth_mod, "get_codex_auth_status", lambda: {})
        monkeypatch.setattr(_auth_mod, "get_xai_oauth_auth_status", lambda: {})
    except Exception:
        pass

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        doctor_mod.run_doctor(Namespace(fix=False))

    out = buf.getvalue()
    assert (
        "model.provider 'custom:local-127.0.0.1:11434' is not a recognised provider"
        not in out
    )
    assert "model.provider 'custom:local-127.0.0.1:11434' is unknown" not in out


def test_run_doctor_accepts_bare_custom_provider(monkeypatch, tmp_path):
    home = tmp_path / ".hermes"
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        "model:\n"
        "  provider: custom\n"
        "  default: local-model\n"
        "  base_url: http://localhost:8000/v1\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(doctor_mod, "HERMES_HOME", home)
    monkeypatch.setattr(doctor_mod, "PROJECT_ROOT", tmp_path / "project")
    monkeypatch.setattr(doctor_mod, "_DHH", str(home))
    (tmp_path / "project").mkdir(exist_ok=True)

    fake_model_tools = types.SimpleNamespace(
        check_tool_availability=lambda *a, **kw: ([], []),
        TOOLSET_REQUIREMENTS={},
    )
    monkeypatch.setitem(sys.modules, "model_tools", fake_model_tools)

    try:
        from hermes_cli import auth as _auth_mod
        monkeypatch.setattr(_auth_mod, "get_codex_auth_status", lambda: {})
        monkeypatch.setattr(_auth_mod, "get_xai_oauth_auth_status", lambda: {})
    except Exception:
        pass

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        doctor_mod.run_doctor(Namespace(fix=False))

    out = buf.getvalue()
    assert "model.provider 'custom' is not a recognised provider" not in out




def test_run_doctor_accepts_vendor_slugs_for_named_custom_provider(monkeypatch, tmp_path):
    home = tmp_path / ".hermes"
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        "model:\n"
        "  provider: custom:hpc-ai\n"
        "  default: deepseek/deepseek-v4-flash\n"
        "custom_providers:\n"
        "  - name: hpc-ai\n"
        "    base_url: https://hpc-ai.example/v1\n"
        "    api_key: test-key\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(doctor_mod, "HERMES_HOME", home)
    monkeypatch.setattr(doctor_mod, "PROJECT_ROOT", tmp_path / "project")
    monkeypatch.setattr(doctor_mod, "_DHH", str(home))
    (tmp_path / "project").mkdir(exist_ok=True)

    fake_model_tools = types.SimpleNamespace(
        check_tool_availability=lambda *a, **kw: ([], []),
        TOOLSET_REQUIREMENTS={},
    )
    monkeypatch.setitem(sys.modules, "model_tools", fake_model_tools)

    try:
        from hermes_cli import auth as _auth_mod
        monkeypatch.setattr(_auth_mod, "get_codex_auth_status", lambda: {})
        monkeypatch.setattr(_auth_mod, "get_xai_oauth_auth_status", lambda: {})
    except Exception:
        pass

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        doctor_mod.run_doctor(Namespace(fix=False))

    out = buf.getvalue()
    assert "model.provider 'custom:hpc-ai' is not a recognised provider" not in out
    assert "model.provider 'custom:hpc-ai' is unknown" not in out
    assert (
        "model.default 'deepseek/deepseek-v4-flash' uses a vendor/model slug but provider is "
        "'custom:hpc-ai'"
        not in out
    )
    assert "Either set model.provider to 'openrouter', or drop the vendor prefix." not in out






def test_run_doctor_termux_does_not_mark_browser_available_without_agent_browser(monkeypatch, tmp_path):
    home = tmp_path / ".hermes"
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text("memory: {}\n", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)

    monkeypatch.setenv("TERMUX_VERSION", "0.118.3")
    monkeypatch.setenv("PREFIX", "/data/data/com.termux/files/usr")
    monkeypatch.setattr(doctor_mod, "HERMES_HOME", home)
    monkeypatch.setattr(doctor_mod, "PROJECT_ROOT", project)
    monkeypatch.setattr(doctor_mod, "_DHH", str(home))
    monkeypatch.setattr(doctor_mod.shutil, "which", lambda cmd: "/data/data/com.termux/files/usr/bin/node" if cmd in {"node", "npm"} else None)

    fake_model_tools = types.SimpleNamespace(
        check_tool_availability=lambda *a, **kw: (["terminal"], [{"name": "browser", "env_vars": [], "tools": ["browser_navigate"]}]),
        TOOLSET_REQUIREMENTS={
            "terminal": {"name": "terminal"},
            "browser": {"name": "browser"},
        },
    )
    monkeypatch.setitem(sys.modules, "model_tools", fake_model_tools)

    try:
        from hermes_cli import auth as _auth_mod
        monkeypatch.setattr(_auth_mod, "get_codex_auth_status", lambda: {})
        monkeypatch.setattr(_auth_mod, "get_xai_oauth_auth_status", lambda: {})
    except Exception:
        pass

    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        doctor_mod.run_doctor(Namespace(fix=False))
    out = buf.getvalue()

    assert "✓ browser" not in out
    assert "browser" in out
    assert "system dependency not met" in out
    assert "agent-browser is not installed (expected in the tested Termux path)" in out
    assert "npm install -g agent-browser && agent-browser install" in out


def _doctor_env_for_agent_browser(monkeypatch, tmp_path):
    """Shared non-Termux fixture setup for the agent-browser npx-resolution
    branch in run_doctor (hermes_cli/doctor.py ~1557-1605)."""
    home = tmp_path / ".hermes"
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text("memory: {}\n", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)

    monkeypatch.delenv("TERMUX_VERSION", raising=False)
    monkeypatch.setenv("PREFIX", "/usr")
    monkeypatch.setattr(doctor_mod, "HERMES_HOME", home)
    monkeypatch.setattr(doctor_mod, "PROJECT_ROOT", project)
    monkeypatch.setattr(doctor_mod, "_DHH", str(home))
    monkeypatch.setattr(
        doctor_mod.shutil,
        "which",
        lambda cmd: "/usr/bin/node" if cmd in {"node", "npm"} else None,
    )

    fake_model_tools = types.SimpleNamespace(
        check_tool_availability=lambda *a, **kw: ([], []),
        TOOLSET_REQUIREMENTS={},
    )
    monkeypatch.setitem(sys.modules, "model_tools", fake_model_tools)

    try:
        from hermes_cli import auth as _auth_mod
        monkeypatch.setattr(_auth_mod, "get_codex_auth_status", lambda: {})
        monkeypatch.setattr(_auth_mod, "get_xai_oauth_auth_status", lambda: {})
    except Exception:
        pass


def test_run_doctor_reports_agent_browser_resolves_via_npx(monkeypatch, tmp_path):
    """When agent-browser has no local/global install, _find_agent_browser
    falls through to 'npx agent-browser' — doctor must report that as OK
    (#43564: agent-browser is no longer a root package.json dependency, so
    this is the expected common case now, not a warning)."""
    _doctor_env_for_agent_browser(monkeypatch, tmp_path)

    import tools.browser_tool as bt
    monkeypatch.setattr(bt, "_find_agent_browser", lambda **_kw: "npx agent-browser")
    warm_calls = []
    monkeypatch.setattr(
        bt, "warm_agent_browser_npx_cache", lambda *a, **kw: warm_calls.append(1) or True
    )

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        doctor_mod.run_doctor(Namespace(fix=False))
    out = buf.getvalue()

    assert "agent-browser" in out
    assert "resolves via npx on first use" in out
    assert "agent-browser not installed" not in out
    # --fix was not requested: the warm-up must not fire on a plain check.
    assert not warm_calls


def test_run_doctor_fix_warms_npx_cache_when_agent_browser_resolves_via_npx(
    monkeypatch, tmp_path
):
    """`hermes doctor --fix` must actually call warm_agent_browser_npx_cache()
    when agent-browser resolves via npx, and report success."""
    _doctor_env_for_agent_browser(monkeypatch, tmp_path)

    import tools.browser_tool as bt
    monkeypatch.setattr(bt, "_find_agent_browser", lambda **_kw: "npx agent-browser")
    warm_calls = []
    monkeypatch.setattr(
        bt, "warm_agent_browser_npx_cache", lambda *a, **kw: warm_calls.append(1) or True
    )

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        doctor_mod.run_doctor(Namespace(fix=True))
    out = buf.getvalue()

    assert warm_calls, "warm_agent_browser_npx_cache() must be called under --fix"
    assert "Warmed npx cache for agent-browser" in out
    assert "Could not warm npx cache" not in out


def test_run_doctor_fix_reports_when_npx_warmup_fails(monkeypatch, tmp_path):
    """If warm_agent_browser_npx_cache() fails (offline, npx missing from
    PATH at call time, etc.), doctor must say so instead of silently
    claiming success — and must not count it as a fix."""
    _doctor_env_for_agent_browser(monkeypatch, tmp_path)

    import tools.browser_tool as bt
    monkeypatch.setattr(bt, "_find_agent_browser", lambda **_kw: "npx agent-browser")
    monkeypatch.setattr(bt, "warm_agent_browser_npx_cache", lambda *a, **kw: False)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        doctor_mod.run_doctor(Namespace(fix=True))
    out = buf.getvalue()

    assert "Could not warm npx cache (offline or npx unavailable)" in out
    assert "Warmed npx cache for agent-browser" not in out




class TestGitHubTokenCheck:
    """Tests for GitHub token / gh auth detection in doctor."""

    @staticmethod
    def _isolate_home(monkeypatch, home):
        """Point doctor at the temp HERMES_HOME.

        ``run_doctor`` reads the module-level ``HERMES_HOME`` constant (cached
        at import time), NOT the env var — so ``setenv("HERMES_HOME")`` alone
        leaves doctor probing the REAL ~/.hermes. On a dev machine with a
        large state.db that meant a multi-minute ``PRAGMA integrity_check``
        that blew the 300s per-file budget and killed the whole file.
        """
        monkeypatch.setattr(doctor_mod, "HERMES_HOME", home)
        monkeypatch.setattr(doctor_mod, "_DHH", str(home))
        monkeypatch.setenv("HERMES_HOME", str(home))

    def test_no_token_and_not_gh_authenticated_shows_warn(self, monkeypatch, tmp_path):
        home = tmp_path / ".hermes"
        home.mkdir(parents=True, exist_ok=True)
        self._isolate_home(monkeypatch, home)
        monkeypatch.setenv("PATH", "/nonexistent")  # gh not found

        from hermes_cli.doctor import run_doctor
        import io, contextlib

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            run_doctor(Namespace(fix=False))
        out = buf.getvalue()

        assert "No GITHUB_TOKEN" in out
        assert "60 req/hr" in out


    def test_gh_authenticated_without_env_token_shows_ok(self, monkeypatch, tmp_path):
        home = tmp_path / ".hermes"
        home.mkdir(parents=True, exist_ok=True)
        self._isolate_home(monkeypatch, home)
        # No GITHUB_TOKEN or GH_TOKEN
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GH_TOKEN", raising=False)

        # Mock gh to return success
        import shutil
        real_which = shutil.which
        def mock_which(cmd):
            return "/usr/local/bin/gh" if cmd == "gh" else real_which(cmd)
        monkeypatch.setattr(shutil, "which", mock_which)

        call_log = []
        def mock_run(cmd, **kwargs):
            call_log.append(cmd)
            if cmd[:2] == ["gh", "auth"]:
                result = types.SimpleNamespace(returncode=0, stdout="", stderr="")
            else:
                result = types.SimpleNamespace(returncode=1, stdout="", stderr="")
            return result

        import subprocess
        monkeypatch.setattr(subprocess, "run", mock_run)

        from hermes_cli.doctor import run_doctor
        import io, contextlib

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            run_doctor(Namespace(fix=False))
        out = buf.getvalue()

        assert "gh auth" in str(call_log) or any(c[0] == "gh" for c in call_log), f"gh not called: {call_log}"
        assert "GitHub authenticated via gh CLI" in out or "token configured" in out




def test_has_healthy_oauth_fallback_returns_false_for_unknown_provider():
    from hermes_cli.doctor import _has_healthy_oauth_fallback_for_apikey_provider
    assert _has_healthy_oauth_fallback_for_apikey_provider("unknown-provider") is False




class TestDoctorStaleMaxIterationsDrift:
    """Regression for #17534: a stale HERMES_MAX_ITERATIONS in .env shadows
    agent.max_turns in config.yaml. The repro symptom is config.yaml saying
    400 while the gateway activity line reads N/90. Doctor must detect the
    drift, and `--fix` must remove the .env ghost (config.yaml wins).

    The detector reads the .env FILE directly, NOT os.environ — the gateway
    startup bridge can already have overridden os.environ to the config value,
    so the ghost is only visible in the file.
    """

    def _run_config_section(self, monkeypatch, tmp_path, *, fix, ghost, cfg_turns,
                            os_environ_value=None):
        import pathlib
        import contextlib
        import io
        from argparse import Namespace

        hermes_home = tmp_path / ".hermes"
        hermes_home.mkdir(parents=True)
        (hermes_home / "config.yaml").write_text(
            f"agent:\n  max_turns: {cfg_turns}\n", encoding="utf-8"
        )
        env_lines = ["OPENAI_API_KEY=sk-test\n"]
        if ghost is not None:
            env_lines.append(f"HERMES_MAX_ITERATIONS={ghost}\n")
        (hermes_home / ".env").write_text("".join(env_lines), encoding="utf-8")

        monkeypatch.setattr(doctor_mod, "HERMES_HOME", hermes_home)
        monkeypatch.setattr(doctor_mod, "get_hermes_home", lambda: hermes_home)
        # Point the config helpers at the temp home.
        monkeypatch.setenv("HERMES_HOME", str(hermes_home))
        if os_environ_value is not None:
            # Simulate the gateway bridge having already overridden os.environ.
            monkeypatch.setenv("HERMES_MAX_ITERATIONS", str(os_environ_value))
        else:
            monkeypatch.delenv("HERMES_MAX_ITERATIONS", raising=False)

        # Short-circuit at the Tool Availability stage — the drift check runs
        # well before it in the Configuration Files section.
        fake_model_tools = types.SimpleNamespace(
            check_tool_availability=lambda *a, **kw: (_ for _ in ()).throw(SystemExit(0)),
            TOOLSET_REQUIREMENTS={},
        )
        monkeypatch.setitem(sys.modules, "model_tools", fake_model_tools)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), pytest.raises(SystemExit):
            doctor_mod.run_doctor(Namespace(fix=fix))
        return buf.getvalue(), hermes_home

    def test_detects_drift_warn_only(self, monkeypatch, tmp_path):
        out, hermes_home = self._run_config_section(
            monkeypatch, tmp_path, fix=False, ghost=90, cfg_turns=400,
            os_environ_value=400,  # bridge contaminated os.environ
        )
        assert "HERMES_MAX_ITERATIONS=90" in out
        assert "shadows" in out
        # Warn-only must NOT mutate .env.
        assert "HERMES_MAX_ITERATIONS=90" in (hermes_home / ".env").read_text(encoding="utf-8")

    def test_fix_removes_ghost(self, monkeypatch, tmp_path):
        out, hermes_home = self._run_config_section(
            monkeypatch, tmp_path, fix=True, ghost=90, cfg_turns=400,
            os_environ_value=400,
        )
        assert "Removed stale HERMES_MAX_ITERATIONS" in out
        env_after = (hermes_home / ".env").read_text(encoding="utf-8")
        assert "HERMES_MAX_ITERATIONS" not in env_after
        assert "OPENAI_API_KEY=sk-test" in env_after  # other keys preserved


    def test_no_drift_when_ghost_absent(self, monkeypatch, tmp_path):
        out, _ = self._run_config_section(
            monkeypatch, tmp_path, fix=False, ghost=None, cfg_turns=400,
        )
        assert "shadows" not in out




class TestDoctorDeprecatedConfigAndEnv:
    """Doctor must surface deprecated/legacy config keys and env vars with
    modern replacements as non-failing warnings — without auto-migrating.
    """



    def test_collect_deprecated_env_vars_ignores_empty(self):
        assert doctor_mod.collect_deprecated_env_vars({"TERMINAL_CWD": "  "}) == []
        assert doctor_mod.collect_deprecated_env_vars({}) == []
        assert doctor_mod.collect_deprecated_env_vars(None) == []

    def test_hermes_tool_progress_warning_says_unsupported_since_floor(self):
        """HERMES_TOOL_PROGRESS lost its last consumer (the retired v3→4
        migration) when the v12 support floor landed — doctor must say the
        variable is ignored rather than merely 'deprecated but read'."""
        findings = dict(
            doctor_mod.collect_deprecated_env_vars({"HERMES_TOOL_PROGRESS": "true"})
        )
        assert "ignored/unsupported since config floor v12" in findings["HERMES_TOOL_PROGRESS"]
        # The MODE variant is still read by the gateway fallback → keeps the
        # plain deprecation wording.
        mode = dict(
            doctor_mod.collect_deprecated_env_vars({"HERMES_TOOL_PROGRESS_MODE": "all"})
        )
        assert mode["HERMES_TOOL_PROGRESS_MODE"] == "display.tool_progress in config.yaml"

    def _run_doctor_with_config(self, monkeypatch, tmp_path, *, config_yaml: str, env_text: str = ""):
        hermes_home = tmp_path / ".hermes"
        hermes_home.mkdir(parents=True)
        (hermes_home / "config.yaml").write_text(config_yaml, encoding="utf-8")
        env_body = env_text if env_text else "OPENAI_API_KEY=sk-test\n"
        (hermes_home / ".env").write_text(env_body, encoding="utf-8")

        monkeypatch.setattr(doctor_mod, "HERMES_HOME", hermes_home)
        monkeypatch.setattr(doctor_mod, "get_hermes_home", lambda: hermes_home)
        monkeypatch.setenv("HERMES_HOME", str(hermes_home))
        # Clear process-level legacy env so tests only see the on-disk .env.
        for k in (
            "HERMES_TOOL_PROGRESS",
            "HERMES_TOOL_PROGRESS_MODE",
            "TERMINAL_CWD",
            "MESSAGING_CWD",
            "QQ_HOME_CHANNEL",
            "QQ_HOME_CHANNEL_NAME",
        ):
            monkeypatch.delenv(k, raising=False)

        fake_model_tools = types.SimpleNamespace(
            check_tool_availability=lambda *a, **kw: (_ for _ in ()).throw(SystemExit(0)),
            TOOLSET_REQUIREMENTS={},
        )
        monkeypatch.setitem(sys.modules, "model_tools", fake_model_tools)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), pytest.raises(SystemExit):
            doctor_mod.run_doctor(Namespace(fix=False))
        return buf.getvalue(), hermes_home




    def test_report_does_not_count_as_blocking_issue(self, monkeypatch, tmp_path, capsys):
        """report_deprecated_config_and_env is warn-only — no issues list mutation."""
        findings = doctor_mod.report_deprecated_config_and_env(
            {"delegation": {"max_async_children": 2}},
            {"HERMES_TOOL_PROGRESS_MODE": "verbose"},
        )
        out = capsys.readouterr().out
        assert len(findings) == 2
        assert "Deprecated: delegation.max_async_children" in out
        assert "Deprecated: HERMES_TOOL_PROGRESS_MODE" in out
        assert "⚠" in out or "Deprecated" in out
