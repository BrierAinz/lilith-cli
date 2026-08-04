"""Tests for ``lilith_tools.mcp_client``.

The only real process allowed in this suite is ``fake_mcp_server.py``
invoked over stdio (the spec's stated exception). Everything else is
either the in-process ``MCPClientManager`` or pure Python.

Covered:
* tools/list round-trip and tool registration into ``ToolRegistry``.
* ``execute()`` of the remote ``echo`` tool returns ``ToolResult(ok=True)``.
* Disabled servers are skipped, broken servers report ``error:`` status,
  ``shutdown()`` cleans up without raising, ``reload()`` re-spawns.
* Per-call timeout returns ``ToolResult(success=False)`` instead of hanging.
* Schema normalisation produces the flat ``parameters`` shape expected by
  ``BaseTool`` (``required`` is propagated to each property).
"""

from __future__ import annotations

import sys
from typing import Any

import pytest

from lilith_tools.base import ToolResult
from lilith_tools.mcp_client import (
    DEFAULT_TIMEOUT_SECONDS,
    MCPClient,
    MCPClientManager,
    _normalize_parameters,
    _safe_remote_name,
    mcp_session,
)
from lilith_tools.registry import ToolRegistry


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def fake_server_cfg() -> dict[str, Any]:
    """Minimal config dict that boots the bundled ``fake_mcp_server``."""
    return {
        "command": sys.executable,
        "args": ["-m", "lilith_tools.fake_mcp_server"],
        "timeout": 5.0,
    }


@pytest.fixture(autouse=True)
def _isolate_registry():
    """Snapshot ``ToolRegistry._tools`` around each test.

    The MCP client registers dynamically-named tools (``mcp_<server>_<tool>``)
    that would otherwise leak across tests. We restore the original mapping
    on teardown so the rest of the suite is unaffected.
    """
    snapshot = dict(ToolRegistry._tools)
    try:
        yield
    finally:
        ToolRegistry._tools.clear()
        ToolRegistry._tools.update(snapshot)


# ── Schema helpers ────────────────────────────────────────────────────


class TestSchemaHelpers:
    def test_safe_remote_name_passes_through_alnum(self):
        assert _safe_remote_name("fake", "echo") == "mcp_fake_echo"

    def test_safe_remote_name_replaces_special_chars(self):
        # Spaces, dashes, slashes — anything not alnum/_ becomes underscore.
        assert _safe_remote_name("my server", "weird-name/thing") == (
            "mcp_my_server_weird_name_thing"
        )

    def test_normalize_parameters_handles_none(self):
        assert _normalize_parameters(None) == {}

    def test_normalize_parameters_handles_non_dict(self):
        assert _normalize_parameters("garbage") == {}  # type: ignore[arg-type]

    def test_normalize_parameters_propagates_required(self):
        schema = {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "msg to echo"},
                "upper": {"type": "boolean", "default": False},
                "mode": {"type": "string", "enum": ["a", "b"]},
            },
            "required": ["message"],
        }
        out = _normalize_parameters(schema)
        assert out["message"]["required"] is True
        assert out["message"]["description"] == "msg to echo"
        # Non-required props don't get a ``required`` key.
        assert "required" not in out["upper"]
        assert out["upper"]["default"] is False
        assert out["mode"]["enum"] == ["a", "b"]


# ── Manager: live fake server ────────────────────────────────────────


class TestManagerWithFakeServer:
    """End-to-end through ``MCPClientManager`` and the bundled fake server.

    Each test boots ``python -m lilith_tools.fake_mcp_server`` as a real
    stdio subprocess — the only real process the suite is allowed to spawn.
    """

    def test_start_all_mounts_echo_tool(self, fake_server_cfg):
        mgr = MCPClientManager({"fake": fake_server_cfg})
        statuses = mgr.start_all()
        assert statuses == {"fake": "ok"}
        assert mgr.mounted_tools == {"fake": ["mcp_fake_echo"]}
        # ToolRegistry should now expose the synthetic tool.
        assert ToolRegistry.get("mcp_fake_echo") is not None
        mgr.shutdown()

    def test_execute_echo_returns_ok(self, fake_server_cfg):
        mgr = MCPClientManager({"fake": fake_server_cfg})
        mgr.start_all()
        try:
            ToolCls = ToolRegistry.get("mcp_fake_echo")
            assert ToolCls is not None
            result = ToolCls().execute(message="hola mundo")
            assert isinstance(result, ToolResult)
            assert result.success is True
            assert result.error == ""
            assert "hola mundo" in (result.data or "")
        finally:
            mgr.shutdown()

    def test_shutdown_is_idempotent(self, fake_server_cfg):
        mgr = MCPClientManager({"fake": fake_server_cfg})
        mgr.start_all()
        mgr.shutdown()
        # Second shutdown must not raise even though everything is gone.
        mgr.shutdown()
        assert mgr.mounted_tools == {}

    def test_shutdown_unregisters_tool(self, fake_server_cfg):
        mgr = MCPClientManager({"fake": fake_server_cfg})
        mgr.start_all()
        assert ToolRegistry.get("mcp_fake_echo") is not None
        mgr.shutdown()
        # Registry entry was removed.
        assert ToolRegistry.get("mcp_fake_echo") is None

    def test_status_reports_ok_after_start(self, fake_server_cfg):
        mgr = MCPClientManager({"fake": fake_server_cfg})
        mgr.start_all()
        try:
            rows = mgr.status()
            assert len(rows) == 1
            assert rows[0]["server"] == "fake"
            assert rows[0]["status"] == "ok"
            assert rows[0]["tools"] == 1
        finally:
            mgr.shutdown()

    def test_status_marks_disabled_servers(self):
        mgr = MCPClientManager({"quiet": {"command": "ignored", "enabled": False}})
        statuses = mgr.start_all()
        assert statuses == {"quiet": "disabled"}
        rows = mgr.status()
        assert rows[0]["status"] == "disabled"
        assert rows[0]["tools"] == 0

    def test_reload_re_spawns_running_server(self, fake_server_cfg):
        mgr = MCPClientManager({"fake": fake_server_cfg})
        mgr.start_all()
        try:
            status = mgr.reload("fake")
            assert status == "ok"
            assert mgr.mounted_tools["fake"] == ["mcp_fake_echo"]
        finally:
            mgr.shutdown()

    def test_reload_unknown_server_returns_error(self):
        mgr = MCPClientManager({})
        msg = mgr.reload("does_not_exist")
        assert "no está configurado" in msg


# ── Manager: failure paths (no real subprocess beyond what's needed) ──


class TestManagerFailurePaths:
    """These tests rely on a deliberately bogus ``command`` — no process
    actually starts, so they are cheap and fully deterministic."""

    def test_start_one_rejects_empty_command(self):
        mgr = MCPClientManager()
        status = mgr._start_one("bad", {"command": ""})
        assert "command" in status

    def test_start_one_rejects_non_list_args(self):
        mgr = MCPClientManager()
        status = mgr._start_one(
            "bad", {"command": "x", "args": "not-a-list"}
        )
        assert "args" in status

    def test_start_one_rejects_non_mapping_env(self):
        mgr = MCPClientManager()
        status = mgr._start_one(
            "bad", {"command": "x", "env": "not-a-dict"}
        )
        assert "env" in status

    def test_broken_command_reports_error_without_crashing(self):
        mgr = MCPClientManager(
            {"broken": {"command": "definitely_not_a_binary_xyz_123"}}
        )
        # Must not raise.
        statuses = mgr.start_all()
        assert "broken" in statuses
        assert statuses["broken"].startswith("error")
        # And nothing got mounted.
        assert mgr.mounted_tools == {}

    def test_broken_server_does_not_block_healthy_ones(self, fake_server_cfg):
        mgr = MCPClientManager(
            {
                "broken": {"command": "definitely_not_a_binary_xyz_123"},
                "fake": fake_server_cfg,
            }
        )
        statuses = mgr.start_all()
        try:
            assert statuses["broken"].startswith("error")
            assert statuses["fake"] == "ok"
            # The healthy server still mounted its tools.
            assert mgr.mounted_tools.get("fake") == ["mcp_fake_echo"]
        finally:
            mgr.shutdown()


# ── Per-call timeout ─────────────────────────────────────────────────


class TestPerCallTimeout:
    """The fake server is instant; to exercise the timeout branch we mock
    the loop-bound ``ClientSession`` so ``call_tool`` blocks longer than
    the configured timeout. We don't replace the full MCPClient (the
    threading setup is intentional), only the async path."""

    def test_call_tool_returns_error_on_timeout(self, monkeypatch):
        import asyncio as _asyncio
        import warnings as _warnings

        # Build a client WITHOUT spawning anything (we override start()).
        client = MCPClient(
            server_name="slow",
            command="ignored",
            args=[],
            timeout=0.05,
        )
        # Pretend the session is ready: stub _loop + _session.
        loop = _asyncio.new_event_loop()
        client._loop = loop

        async def _hang_forever(*_args, **_kwargs):
            await _asyncio.sleep(60)

        class _FakeSession:
            async def call_tool(self, name, arguments):
                return await _hang_forever()

        client._session = _FakeSession()
        # ``run_coroutine_threadsafe`` schedules a coroutine on a loop
        # that no thread is running; the loop's __del__ complains about
        # the abandoned coroutine. Silence just that one warning — it is
        # a structural artefact of the timeout path, not a test failure.
        with _warnings.catch_warnings():
            _warnings.filterwarnings(
                "ignore",
                message="coroutine .* was never awaited",
                category=RuntimeWarning,
            )
            try:
                result = client.call_tool("anything", {"k": "v"})
                assert isinstance(result, ToolResult)
                assert result.success is False
                assert "Timeout" in (result.error or "")
                assert "anything" in result.error
            finally:
                try:
                    client._loop.close()
                except Exception:
                    pass


# ── ``mcp_session`` context manager ──────────────────────────────────


class TestMcpSession:
    def test_context_manager_starts_and_shuts_down(self, fake_server_cfg):
        with mcp_session({"fake": fake_server_cfg}) as manager:
            assert manager.mounted_tools["fake"] == ["mcp_fake_echo"]
            assert ToolRegistry.get("mcp_fake_echo") is not None
        # Teardown happened.
        assert ToolRegistry.get("mcp_fake_echo") is None

    def test_context_manager_swallows_shutdown_errors(
        self, monkeypatch, fake_server_cfg
    ):
        from lilith_tools import mcp_client as mcp_mod

        forced: list[str] = []
        original_force_close = mcp_mod.MCPClient.force_close

        def _boom(self):
            raise RuntimeError("simulated shutdown failure")

        def _record_force_close(self):
            forced.append(self.server_name)
            return original_force_close(self)

        monkeypatch.setattr(mcp_mod.MCPClient, "close", _boom)
        monkeypatch.setattr(
            mcp_mod.MCPClient, "force_close", _record_force_close
        )
        # Must not raise even though every client's close() explodes.
        with mcp_session({"fake": fake_server_cfg}) as _manager:
            pass
        assert forced == ["fake"]


# ── Defaults sanity check ────────────────────────────────────────────


def test_default_timeout_is_a_finite_positive_number():
    assert DEFAULT_TIMEOUT_SECONDS > 0
