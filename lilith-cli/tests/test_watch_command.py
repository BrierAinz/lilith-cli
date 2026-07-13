"""Tests for the /watch slash command."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from lilith_cli.extra_commands import run_watch_command


class DummyConfig:
    def __init__(self):
        self.model = "test"
        self.provider = "test"
        self.providers = {}
        self.api_key = ""
        self.system_prompt = ""


class DummySession:
    def __init__(self):
        self.config = DummyConfig()
        self.memory = None
        self.history = []
        self.provider = None
        self.system_prompt = ""


@pytest.mark.asyncio
async def test_watch_command_is_importable_and_callable():
    """La función run_watch_command existe y es awaitable."""
    session = DummySession()
    prints = []

    def capture(text: str = ""):
        prints.append(str(text))

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        await run_watch_command(session, "")

    assert any("No hay watchers activos" in p for p in prints)


@pytest.mark.asyncio
async def test_watch_start_and_stop_and_list(tmp_path, monkeypatch):
    """/watch <path>, /watch list y /watch stop no fallan."""
    monkeypatch.chdir(tmp_path)
    session = DummySession()
    prints = []

    def capture(text: str = ""):
        prints.append(str(text))

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        await run_watch_command(session, str(tmp_path))
        await run_watch_command(session, "list")
        await run_watch_command(session, "stop watch_1")

    output = "".join(prints)
    assert "Watcher iniciado" in output
    assert "watch_1" in output
    assert "Watcher detenido" in output


@pytest.mark.asyncio
async def test_watch_events_without_active_watch():
    """/watch events con un id inexistente muestra error."""
    session = DummySession()
    errors = []

    def capture_error(text: str = ""):
        errors.append(str(text))

    with patch("lilith_cli.extra_commands.render_error", side_effect=capture_error):
        await run_watch_command(session, "events missing_id")

    assert any("Watcher no encontrado" in e for e in errors)
