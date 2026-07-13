"""Tests for the /auto slash command."""

from __future__ import annotations

import pytest

from lilith_cli.agent import AgentSession
from lilith_cli.commands import AutoCommand, CommandRegistry
from lilith_cli.config import YggdrasilConfig


def _make_session() -> AgentSession:
    cfg = YggdrasilConfig(provider="local", model="local-model")
    return AgentSession(cfg)


@pytest.mark.asyncio
async def test_auto_on_off_list(capsys):
    """/auto on/off/list toggles and shows the current state."""
    session = _make_session()
    assert session._auto_execute is False
    cmd = AutoCommand(session)

    await cmd.execute("on")
    captured = capsys.readouterr().out
    assert "✓ auto" in captured
    assert "ON" in captured
    assert session._auto_execute is True

    await cmd.execute("list")
    captured = capsys.readouterr().out
    assert "Modo auto" in captured
    assert "ON" in captured
    assert "patrones aprobados" in captured

    await cmd.execute("off")
    captured = capsys.readouterr().out
    assert "OFF" in captured
    assert session._auto_execute is False


@pytest.mark.asyncio
async def test_auto_add_remove_patterns(capsys):
    """/auto add/remove/list manages pre-approved regex patterns."""
    session = _make_session()
    cmd = AutoCommand(session)

    await cmd.execute("add test_.*\\.py")
    captured = capsys.readouterr().out
    assert "✓ Patrón agregado" in captured
    assert session._auto_approved_patterns == ["test_.*\\.py"]

    await cmd.execute("add /path/to/.*")
    assert session._auto_approved_patterns == ["test_.*\\.py", "/path/to/.*"]

    await cmd.execute("list")
    captured = capsys.readouterr().out
    assert "test_.*\\.py" in captured
    assert "/path/to/.*" in captured

    await cmd.execute("remove /path/to/.*")
    captured = capsys.readouterr().out
    assert "✓ Patrón eliminado" in captured
    assert session._auto_approved_patterns == ["test_.*\\.py"]

    await cmd.execute("remove missing")
    captured = capsys.readouterr().out
    assert "No se encontró" in captured


@pytest.mark.asyncio
async def test_auto_matches_pattern(capsys):
    """Matching a pre-approved pattern should bypass confirm_write."""
    session = _make_session()
    session.config.confirm_write = True
    session._auto_execute = True
    session._auto_approved_patterns = ["test_"]

    assert session._matches_auto_pattern("file_write", {"path": "tests/test_foo.py"})
    assert not session._matches_auto_pattern("file_write", {"path": "src/main.py"})

    # Pattern matching itself is independent of the auto-execute gate -- the
    # gate is enforced at the call site (see AgentSession._run_tool_call):
    #     if self._auto_execute and self._matches_auto_pattern(...): ...
    # So toggling _auto_execute here does NOT change _matches_auto_pattern output.
    session._auto_execute = False
    assert session._matches_auto_pattern("file_write", {"path": "tests/test_foo.py"})


@pytest.mark.asyncio
async def test_auto_command_registry_registered():
    """The /auto command should be discoverable in the registry."""
    session = _make_session()
    registry = CommandRegistry(session)
    registry.discover()
    assert registry.get("auto") is not None
    assert registry.get("auto").name == "auto"
