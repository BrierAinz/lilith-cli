"""Tests for the /mcp slash command.

Covers ``MCPCommand`` sub-commands ``list`` and ``reload <server>`` plus
its graceful behaviour when the REPL never attached a manager (the
common case in unit tests that bypass ``run_repl``).
"""

from __future__ import annotations

import asyncio
import sys

import pytest


def _run(coro):
    return asyncio.run(coro)


class _Session:
    """Minimal stand-in for the attributes ``MCPCommand`` touches."""

    def __init__(self, manager=None) -> None:
        self._mcp_manager = manager


class _FakeManager:
    """Tiny stand-in for ``MCPClientManager`` — implements the surface
    that ``MCPCommand._list`` / ``MCPCommand._reload`` actually call."""

    def __init__(self, status_rows, mounted, reload_responses=None):
        self._rows = status_rows
        self._mounted = mounted
        self._reload_responses = reload_responses or {}

    def status(self):
        return list(self._rows)

    @property
    def mounted_tools(self):
        return {k: list(v) for k, v in self._mounted.items()}

    def reload(self, name):
        return self._reload_responses.get(name, "ok")


# ── /mcp list ────────────────────────────────────────────────────────


def test_mcp_list_no_manager_does_not_raise(fake_session, capsys):
    """When the REPL never attached a manager, /mcp list prints a hint
    and returns cleanly instead of AttributeError."""
    from lilith_cli.commands import MCPCommand

    fake_session._mcp_manager = None
    _run(MCPCommand(fake_session).execute("list"))

    out = capsys.readouterr().out
    assert "MCP no inicializado" in out


def test_mcp_list_renders_rows(fake_session, capsys):
    from lilith_cli.commands import MCPCommand

    mgr = _FakeManager(
        status_rows=[
            {"server": "alpha", "status": "ok", "tools": 2, "error": ""},
            {"server": "beta", "status": "down", "tools": 0, "error": "boom"},
            {"server": "gamma", "status": "disabled", "tools": 0, "error": ""},
        ],
        mounted={"alpha": ["mcp_alpha_a", "mcp_alpha_b"]},
    )
    fake_session._mcp_manager = mgr
    _run(MCPCommand(fake_session).execute("list"))

    out = capsys.readouterr().out
    # All three server names appear; status markers render via rich tags.
    assert "alpha" in out
    assert "beta" in out
    assert "gamma" in out
    assert "ok" in out
    assert "down" in out
    assert "disabled" in out
    # Tool counts show up.
    assert "2" in out
    # The error from beta surfaces verbatim.
    assert "boom" in out


def test_mcp_list_empty_manager(capsys):
    """Manager present but no servers configured."""
    from lilith_cli.commands import MCPCommand

    session = _Session(manager=_FakeManager([], {}))
    _run(MCPCommand(session).execute("list"))

    out = capsys.readouterr().out
    assert "No hay servidores MCP" in out


def test_mcp_list_accepts_ls_and_status_aliases(fake_session, capsys):
    """``list``, ``ls`` and ``status`` all dispatch to the same handler."""
    from lilith_cli.commands import MCPCommand

    fake_session._mcp_manager = _FakeManager(
        status_rows=[{"server": "x", "status": "ok", "tools": 1, "error": ""}],
        mounted={"x": ["mcp_x_y"]},
    )
    for sub in ("list", "ls", "status"):
        _run(MCPCommand(fake_session).execute(sub))
        out = capsys.readouterr().out
        assert "x" in out, f"sub={sub} missing server name in output"


# ── /mcp reload <server> ─────────────────────────────────────────────


def test_mcp_reload_success_shows_mounted_tools(fake_session, capsys):
    from lilith_cli.commands import MCPCommand

    mgr = _FakeManager(
        status_rows=[],
        mounted={"alpha": ["mcp_alpha_a", "mcp_alpha_b"]},
        reload_responses={"alpha": "ok"},
    )
    fake_session._mcp_manager = mgr
    _run(MCPCommand(fake_session).execute("reload alpha"))

    out = capsys.readouterr().out
    assert "alpha" in out
    assert "mcp_alpha_a" in out
    assert "mcp_alpha_b" in out


def test_mcp_reload_failure_renders_error(fake_session, capsys):
    from lilith_cli.commands import MCPCommand

    mgr = _FakeManager(
        status_rows=[],
        mounted={},
        reload_responses={"broken": "error: FileNotFoundError"},
    )
    fake_session._mcp_manager = mgr
    _run(MCPCommand(fake_session).execute("reload broken"))

    out = capsys.readouterr().out
    assert "broken" in out
    assert "FileNotFoundError" in out


def test_mcp_reload_without_manager(fake_session, capsys):
    from lilith_cli.commands import MCPCommand

    fake_session._mcp_manager = None
    _run(MCPCommand(fake_session).execute("reload anything"))

    out = capsys.readouterr().out
    assert "MCP no inicializado" in out


def test_mcp_unknown_subcommand_prints_usage(fake_session, capsys):
    from lilith_cli.commands import MCPCommand

    fake_session._mcp_manager = None
    _run(MCPCommand(fake_session).execute("frobnicate"))

    out = capsys.readouterr().out
    assert "Uso:" in out


def test_mcp_reload_requires_one_arg(fake_session, capsys):
    """``/mcp reload`` with no server or with two servers should print
    the usage hint, not call the manager."""
    from lilith_cli.commands import MCPCommand

    called = {"reload": 0}

    class _Spy(_FakeManager):
        def reload(self, name):  # type: ignore[override]
            called["reload"] += 1
            return "ok"

    fake_session._mcp_manager = _Spy([], {})
    for args in ("reload", "reload a b"):
        _run(MCPCommand(fake_session).execute(args))
        out = capsys.readouterr().out
        assert "Uso:" in args or "Uso:" in out

    assert called["reload"] == 0


# ── Command metadata ─────────────────────────────────────────────────


def test_mcp_command_metadata():
    from lilith_cli.commands import MCPCommand

    cmd = MCPCommand(_Session())
    assert cmd.name == "mcp"
    assert "mcps" in cmd.aliases