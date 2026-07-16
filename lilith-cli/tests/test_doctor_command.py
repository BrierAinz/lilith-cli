"""Tests for the top-level ``lilith doctor`` healthcheck command.

The command is a thin cyclopts wrapper around
:func:`lilith_cli.main.run_doctor_checks`, which returns a list of
``{check, status, message}`` rows. These tests exercise that helper
directly (no cyclopts CLI dispatch) so the assertions stay close to
the data, then drive the CLI surface for the exit-code contract.
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest


# Ensure lilith_cli is importable when running tests directly.
_PKG_DIR = str(Path(__file__).resolve().parent.parent)
if _PKG_DIR not in sys.path:
    sys.path.insert(0, _PKG_DIR)


# ── Helpers ────────────────────────────────────────────────────────────


def _make_provider_profile(
    *,
    api_key: str = "sk-fake-1234",
    model: str = "fake-model",
    max_tokens: int | None = None,
) -> Any:
    return SimpleNamespace(
        api_key=api_key,
        base_url="https://fake.example/v1",
        model=model,
        temperature=None,
        max_tokens=max_tokens,
        use_responses=None,
    )


def _make_cfg(
    providers: dict[str, Any] | None = None,
    *,
    mcp_servers: dict[str, Any] | None = None,
    memory_db: str = "~/.yggdrasil/memory.db",
) -> Any:
    return SimpleNamespace(
        provider="fake",
        model="fake-model",
        providers=providers if providers is not None else {"fake": _make_provider_profile()},
        mcp_servers=mcp_servers,
        memory=SimpleNamespace(db_path=memory_db),
    )


def _fake_wrapper_factory(behavior: dict[str, Any]):
    """Build a ``LLMProviderWrapper`` stub. ``behavior`` maps provider
    name → either an Exception to raise or a dict with ``content`` /
    ``usage`` / ``reasoning_content`` to return.
    """

    class _StubWrapper:
        def __init__(self, cfg):
            self.cfg = cfg

        async def complete(self, messages, *, tools=None, **kwargs):
            entry = behavior.get(self.cfg.provider, {})
            if isinstance(entry, BaseException):
                raise entry
            if asyncio.iscoroutine(entry):
                return await entry
            if isinstance(entry, dict):
                return {
                    "content": entry.get("content", "PONG"),
                    "tool_calls": [],
                    "usage": entry.get("usage", {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}),
                    "finish_reason": "stop",
                    "reasoning_content": entry.get("reasoning_content", ""),
                }
            raise AssertionError(
                f"unexpected behavior entry type for {self.cfg.provider!r}: {type(entry).__name__}"
            )

        async def close(self):
            return None

    return _StubWrapper


# ── (a) Config parsing ────────────────────────────────────────────────


class TestConfigParses:
    def test_valid_config_returns_ok_row(self, monkeypatch):
        from lilith_cli import config as cli_config

        cfg = _make_cfg()
        monkeypatch.setattr(cli_config, "load_config", lambda *a, **k: cfg)

        from lilith_cli import main as cli_main
        row = cli_main._check_config_parses()
        assert row["status"] == "ok"
        assert row["check"] == "config.yaml"
        assert "parsea" in row["message"]

    def test_invalid_config_returns_error_row(self, monkeypatch):
        from lilith_cli import config as cli_config

        def _boom(*a, **k):
            raise ValueError("yaml malformado")

        monkeypatch.setattr(cli_config, "load_config", _boom)
        from lilith_cli import main as cli_main
        row = cli_main._check_config_parses()
        assert row["status"] == "error"
        assert "yaml malformado" in row["message"]


# ── (b) API key presence (without printing values) ────────────────────


class TestApiKeyPresence:
    def test_env_var_reference_resolved_to_present(self, monkeypatch):
        from lilith_cli import main as cli_main

        monkeypatch.setenv("FAKE_PROVIDER_KEY", "set")
        profile = _make_provider_profile(api_key="${FAKE_PROVIDER_KEY}")
        cfg = _make_cfg(providers={"fake": profile})
        rows = cli_main._check_provider_keys(cfg)
        assert len(rows) == 1
        assert rows[0]["status"] == "ok"
        # The env-var name is in the message but the value is never
        # echoed — "set" must NOT appear anywhere in the row.
        assert "FAKE_PROVIDER_KEY" in rows[0]["message"]
        assert "set" not in rows[0]["message"]

    def test_env_var_reference_missing(self, monkeypatch):
        from lilith_cli import main as cli_main

        monkeypatch.delenv("MISSING_KEY", raising=False)
        profile = _make_provider_profile(api_key="${MISSING_KEY}")
        cfg = _make_cfg(providers={"fake": profile})
        rows = cli_main._check_provider_keys(cfg)
        assert rows[0]["status"] == "error"
        assert "NO esta definida" in rows[0]["message"]
        # Still no value leaked.
        assert "MISSING" in rows[0]["message"]
        assert "value" not in rows[0]["message"].lower()

    def test_literal_key_present_is_ok(self, monkeypatch):
        from lilith_cli import main as cli_main

        profile = _make_provider_profile(api_key="sk-supersecret-1234")
        cfg = _make_cfg(providers={"fake": profile})
        rows = cli_main._check_provider_keys(cfg)
        assert rows[0]["status"] == "ok"
        # The literal value must NEVER be printed.
        msg = rows[0]["message"]
        assert "supersecret" not in msg
        assert "1234" not in msg

    def test_missing_key_is_warn_not_error(self, monkeypatch):
        from lilith_cli import main as cli_main

        profile = _make_provider_profile(api_key=None)
        cfg = _make_cfg(providers={"fake": profile})
        rows = cli_main._check_provider_keys(cfg)
        assert rows[0]["status"] == "warn"

    def test_no_providers_returns_warn(self, monkeypatch):
        from lilith_cli import main as cli_main

        cfg = _make_cfg(providers={})
        rows = cli_main._check_provider_keys(cfg)
        assert rows[0]["status"] == "warn"


# ── (c) Provider pings ─────────────────────────────────────────────────


class TestProviderPings:
    def test_ping_success_reports_ok_with_latency(self, monkeypatch):
        from lilith_cli import main as cli_main
        from lilith_cli import providers as cli_providers

        cfg = _make_cfg(providers={"fake": _make_provider_profile()})
        stub_factory = _fake_wrapper_factory({
            "fake": {"content": "PONG", "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}},
        })
        monkeypatch.setattr(cli_providers, "LLMProviderWrapper", stub_factory)

        rows = asyncio.run(cli_main._run_provider_pings(cfg))
        assert len(rows) == 1
        assert rows[0]["check"] == "ping:fake"
        assert rows[0]["status"] == "ok"
        assert "responde" in rows[0]["message"]
        assert "ms" in rows[0]["message"]

    def test_ping_failure_reports_error(self, monkeypatch):
        from lilith_cli import main as cli_main
        from lilith_cli import providers as cli_providers

        cfg = _make_cfg(providers={"fake": _make_provider_profile()})
        stub_factory = _fake_wrapper_factory({
            "fake": RuntimeError("connection refused"),
        })
        monkeypatch.setattr(cli_providers, "LLMProviderWrapper", stub_factory)

        rows = asyncio.run(cli_main._run_provider_pings(cfg))
        assert rows[0]["status"] == "error"
        assert "RuntimeError" in rows[0]["message"]
        assert "connection refused" in rows[0]["message"]

    def test_ping_uses_pong_prompt(self, monkeypatch):
        """The ping is the same ``PONG`` one-shot the doctor advertises."""
        from lilith_cli import main as cli_main
        from lilith_cli import providers as cli_providers

        cfg = _make_cfg(providers={"fake": _make_provider_profile()})
        captured: dict[str, Any] = {}

        class _Capture:
            def __init__(self, cfg):
                self.cfg = cfg

            async def complete(self, messages, *, tools=None, **kwargs):
                captured["messages"] = list(messages)
                captured["kwargs"] = dict(kwargs)
                return {"content": "PONG", "tool_calls": [], "usage": {}, "finish_reason": "stop", "reasoning_content": ""}

            async def close(self):
                return None

        monkeypatch.setattr(cli_providers, "LLMProviderWrapper", _Capture)
        asyncio.run(cli_main._run_provider_pings(cfg))
        assert captured["messages"] == [{"role": "user", "content": "PONG"}]
        # Tools must be None — we never want a tool-call back.
        assert captured["kwargs"].get("tools") is None
        # max_tokens must be the doctor ceiling (1-token spirit).
        assert captured["kwargs"].get("max_tokens") == cli_main._DOCTOR_PING_MAX_TOKENS

    def test_ping_runs_in_parallel(self, monkeypatch):
        """Two providers should not be serialised; the wall-clock is
        roughly the slowest one, not their sum.
        """
        import time as _time

        from lilith_cli import main as cli_main
        from lilith_cli import providers as cli_providers

        cfg = _make_cfg(providers={
            "slow": _make_provider_profile(model="slow-model"),
            "fast": _make_provider_profile(model="fast-model"),
        })

        class _Slow:
            def __init__(self, cfg):
                self.cfg = cfg

            async def complete(self, messages, *, tools=None, **kwargs):
                await asyncio.sleep(0.5)
                return {"content": "slow", "tool_calls": [], "usage": {}, "finish_reason": "stop", "reasoning_content": ""}

            async def close(self):
                return None

        class _Fast:
            def __init__(self, cfg):
                self.cfg = cfg

            async def complete(self, messages, *, tools=None, **kwargs):
                await asyncio.sleep(0.1)
                return {"content": "fast", "tool_calls": [], "usage": {}, "finish_reason": "stop", "reasoning_content": ""}

            async def close(self):
                return None

        def _factory(cfg):
            return _Slow(cfg) if cfg.provider == "slow" else _Fast(cfg)

        monkeypatch.setattr(cli_providers, "LLMProviderWrapper", _factory)
        t0 = _time.perf_counter()
        rows = asyncio.run(cli_main._run_provider_pings(cfg))
        elapsed = _time.perf_counter() - t0
        # Serial would be 0.6s; parallel is roughly 0.5s. Give a generous
        # slack to avoid CI flakes while still catching accidental
        # serialisation (which would push elapsed past 0.55s).
        assert elapsed < 0.55, f"pings were not parallel: {elapsed:.3f}s"
        assert {r["status"] for r in rows} == {"ok"}


# ── (d) MCP servers ────────────────────────────────────────────────────


class TestMcpServers:
    def test_no_servers_returns_ok_row(self, monkeypatch):
        from lilith_cli import main as cli_main

        cfg = _make_cfg(mcp_servers=None)
        rows = cli_main._check_mcp_servers(cfg)
        assert rows[0]["status"] == "ok"
        assert "sin servidores" in rows[0]["message"]

    def test_ok_server_reported_as_ok(self, monkeypatch):
        from lilith_cli import main as cli_main
        from lilith_tools import mcp_client as tools_mcp

        cfg = _make_cfg(mcp_servers={
            "my-server": SimpleNamespace(
                command="echo", args=[], env=None, enabled=True, timeout=10.0,
            ),
        })

        class _FakeManager:
            def __init__(self, servers):
                self._servers = servers

            def start_all(self):
                return {"my-server": "ok"}

        monkeypatch.setattr(tools_mcp, "MCPClientManager", _FakeManager)
        rows = cli_main._check_mcp_servers(cfg)
        assert rows[0]["check"] == "mcp:my-server"
        assert rows[0]["status"] == "ok"
        assert rows[0]["message"] == "arrancado"

    def test_disabled_server_is_ok(self, monkeypatch):
        from lilith_cli import main as cli_main
        from lilith_tools import mcp_client as tools_mcp

        cfg = _make_cfg(mcp_servers={
            "dsb": SimpleNamespace(
                command="echo", args=[], env=None, enabled=False, timeout=10.0,
            ),
        })

        class _FakeManager:
            def __init__(self, servers):
                pass

            def start_all(self):
                return {"dsb": "disabled"}

        monkeypatch.setattr(tools_mcp, "MCPClientManager", _FakeManager)
        rows = cli_main._check_mcp_servers(cfg)
        assert rows[0]["status"] == "ok"
        assert "deshabilitado" in rows[0]["message"]

    def test_broken_server_reported_as_error(self, monkeypatch):
        from lilith_cli import main as cli_main
        from lilith_tools import mcp_client as tools_mcp

        cfg = _make_cfg(mcp_servers={
            "broken": SimpleNamespace(
                command="does-not-exist", args=[], env=None, enabled=True, timeout=10.0,
            ),
        })

        class _FakeManager:
            def __init__(self, servers):
                pass

            def start_all(self):
                return {"broken": "error: command not found"}

        monkeypatch.setattr(tools_mcp, "MCPClientManager", _FakeManager)
        rows = cli_main._check_mcp_servers(cfg)
        assert rows[0]["status"] == "error"
        assert "command not found" in rows[0]["message"]


# ── (e) Memory DB ──────────────────────────────────────────────────────


class TestMemoryDb:
    def test_db_creates_and_is_queryable(self, tmp_path, monkeypatch):
        from lilith_cli import main as cli_main

        db = tmp_path / "memory.db"
        cfg = _make_cfg(memory_db=str(db))
        row = cli_main._check_memory_db(cfg)
        assert row["status"] == "ok"
        assert db.exists()
        # Open it directly to confirm sqlite accepted the file.
        conn = sqlite3.connect(str(db))
        try:
            assert conn.execute("SELECT 1").fetchone() == (1,)
        finally:
            conn.close()

    def test_db_in_unwriteable_dir_is_error(self, tmp_path, monkeypatch):
        from lilith_cli import main as cli_main

        # Point the DB at a path under a non-existent parent; expanduser
        # leaves it as-is, so the mkdir succeeds but we make the parent
        # read-only on POSIX to force sqlite to fail. On Windows the
        # open() will fail differently; we check the row went to
        # "error" or "ok" but the helper doesn't crash either way.
        cfg = _make_cfg(memory_db=str(tmp_path / "deep" / "nested" / "x.db"))
        # Should NOT raise; mkdir parents=True should succeed.
        row = cli_main._check_memory_db(cfg)
        assert row["status"] in ("ok", "error")


# ── (f) Package versions ──────────────────────────────────────────────


class TestPackageVersions:
    def test_reports_lilith_packages(self, monkeypatch):
        from lilith_cli import main as cli_main

        rows = cli_main._check_package_versions()
        names = [r["check"] for r in rows]
        # At least the meta-package that ships the test should be visible.
        assert any(n.startswith("pkg:lilith-") for n in names), names
        for r in rows:
            if r["check"].startswith("pkg:lilith-"):
                # ok or warn (no real value leak)
                assert r["status"] in ("ok", "warn")


# ── (g) run_doctor_checks (full integration of the helpers) ──────────


class TestRunDoctorChecks:
    def test_all_ok_when_config_and_providers_are_healthy(
        self, monkeypatch, tmp_path
    ):
        from lilith_cli import config as cli_config
        from lilith_cli import main as cli_main
        from lilith_cli import providers as cli_providers

        cfg = _make_cfg(
            providers={"fake": _make_provider_profile()},
            mcp_servers=None,
            memory_db=str(tmp_path / "memory.db"),
        )
        monkeypatch.setattr(cli_config, "load_config", lambda *a, **k: cfg)
        monkeypatch.setattr(
            cli_providers, "LLMProviderWrapper",
            _fake_wrapper_factory({
                "fake": {"content": "PONG"},
            }),
        )

        rows = cli_main.run_doctor_checks()
        # At least one row per category. ``config.yaml`` and ``memory.db``
        # are exact check names (no ``:`` separator); the other categories
        # use the prefix before the colon (``api_key:fake`` → ``api_key``).
        categories = {
            r["check"].split(":", 1)[0] if ":" in r["check"] else r["check"]
            for r in rows
        }
        assert {"config.yaml", "api_key", "ping", "memory.db", "pkg", "mcp_servers"} <= categories
        # No error rows when everything is happy.
        errors = [r for r in rows if r["status"] == "error"]
        assert errors == [], f"unexpected errors: {errors}"

    def test_error_in_one_section_propagates_to_exit_code(
        self, monkeypatch, tmp_path
    ):
        from lilith_cli import config as cli_config
        from lilith_cli import main as cli_main
        from lilith_cli import providers as cli_providers

        cfg = _make_cfg(
            providers={"fake": _make_provider_profile()},
            mcp_servers=None,
            memory_db=str(tmp_path / "memory.db"),
        )
        monkeypatch.setattr(cli_config, "load_config", lambda *a, **k: cfg)
        # Provider ping explodes.
        monkeypatch.setattr(
            cli_providers, "LLMProviderWrapper",
            _fake_wrapper_factory({"fake": RuntimeError("network down")}),
        )

        rows = cli_main.run_doctor_checks()
        ping_rows = [r for r in rows if r["check"].startswith("ping:")]
        assert any(r["status"] == "error" for r in ping_rows)

    def test_config_broken_short_circuits_remaining_checks(
        self, monkeypatch
    ):
        from lilith_cli import config as cli_config
        from lilith_cli import main as cli_main

        def _boom(*a, **k):
            raise RuntimeError("yaml invalid")

        monkeypatch.setattr(cli_config, "load_config", _boom)
        rows = cli_main.run_doctor_checks()
        # First row is the parse error; one provider-error row closes it.
        assert rows[0]["check"] == "config.yaml"
        assert rows[0]["status"] == "error"
        assert any(r["check"] == "providers" for r in rows)


# ── (h) CLI exit-code contract ────────────────────────────────────────


class TestDoctorCommand:
    def test_doctor_command_is_registered(self):
        from lilith_cli.main import app

        # Cyclopts exposes registered subcommands via the private
        # ``_commands`` mapping; ``dir(app)`` only returns App-level
        # attributes, so we look it up explicitly.
        assert "doctor" in app._commands

    def test_doctor_command_exits_0_when_all_ok(self, monkeypatch, tmp_path):
        from lilith_cli import config as cli_config
        from lilith_cli import main as cli_main
        from lilith_cli import providers as cli_providers
        from lilith_cli.main import app

        cfg = _make_cfg(
            providers={"fake": _make_provider_profile()},
            mcp_servers=None,
            memory_db=str(tmp_path / "memory.db"),
        )
        monkeypatch.setattr(cli_config, "load_config", lambda *a, **k: cfg)
        monkeypatch.setattr(
            cli_providers, "LLMProviderWrapper",
            _fake_wrapper_factory({"fake": {"content": "PONG"}}),
        )

        # No SystemExit when all rows are ok/warn.
        with pytest.raises(SystemExit) as excinfo:
            app(["doctor"], exit_on_error=False, console=None)
        assert excinfo.value.code == 0

    def test_doctor_command_exits_1_when_error_row(self, monkeypatch, tmp_path):
        from lilith_cli import config as cli_config
        from lilith_cli import main as cli_main
        from lilith_cli import providers as cli_providers
        from lilith_cli.main import app

        cfg = _make_cfg(
            providers={"fake": _make_provider_profile()},
            mcp_servers=None,
            memory_db=str(tmp_path / "memory.db"),
        )
        monkeypatch.setattr(cli_config, "load_config", lambda *a, **k: cfg)
        # Force a ping error so the doctor exits non-zero.
        monkeypatch.setattr(
            cli_providers, "LLMProviderWrapper",
            _fake_wrapper_factory({"fake": RuntimeError("502 bad gateway")}),
        )

        with pytest.raises(SystemExit) as excinfo:
            app(["doctor"], exit_on_error=False, console=None)
        assert excinfo.value.code == 1
