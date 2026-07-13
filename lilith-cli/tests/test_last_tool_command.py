"""Tests for the /last-tool slash command."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from rich.syntax import Syntax

from lilith_cli.extra_commands import run_last_tool_command


class DummySession:
    def __init__(self, history):
        self._tool_call_history = history


def _make_history():
    return [
        {
            "name": "file_read",
            "arguments": {"path": "a.txt"},
            "duration": 0.12,
            "timestamp": "2026-01-01T00:00:00",
            "success": True,
        },
        {
            "name": "file_write",
            "arguments": {"path": "b.txt", "content": "hola"},
            "duration": 0.34,
            "timestamp": "2026-01-01T00:00:01",
            "success": True,
        },
        {
            "name": "file_read",
            "arguments": {"path": "c.txt"},
            "duration": 0.56,
            "timestamp": "2026-01-01T00:00:02",
            "success": False,
        },
    ]


def _printed_text(prints: list[Any]) -> str:
    parts = []
    for p in prints:
        if isinstance(p, Syntax):
            parts.append(p.code)
        else:
            parts.append(str(p))
    return "\n".join(parts)


@pytest.mark.asyncio
async def test_last_tool_default_shows_most_recent_call():
    """/last-tool sin argumentos muestra la llamada más reciente."""
    session = DummySession(_make_history())
    prints: list[Any] = []

    with patch("lilith_cli.extra_commands.console.print", side_effect=prints.append):
        await run_last_tool_command(session, "")

    output = _printed_text(prints)
    assert "file_read" in output
    assert "c.txt" in output
    assert "0.5600s" in output


@pytest.mark.asyncio
async def test_last_tool_by_index():
    """/last-tool 2 muestra la segunda llamada más reciente."""
    session = DummySession(_make_history())
    prints: list[Any] = []

    with patch("lilith_cli.extra_commands.console.print", side_effect=prints.append):
        await run_last_tool_command(session, "2")

    output = _printed_text(prints)
    assert "file_write" in output
    assert "b.txt" in output


@pytest.mark.asyncio
async def test_last_tool_by_name_finds_most_recent_match():
    """/last-tool file_read muestra la última llamada a file_read."""
    session = DummySession(_make_history())
    prints: list[Any] = []

    with patch("lilith_cli.extra_commands.console.print", side_effect=prints.append):
        await run_last_tool_command(session, "file_read")

    output = _printed_text(prints)
    assert "c.txt" in output


@pytest.mark.asyncio
async def test_last_tool_empty_history():
    """/last-tool sin historial muestra un mensaje informativo."""
    session = DummySession([])
    prints: list[Any] = []

    with patch("lilith_cli.extra_commands.console.print", side_effect=prints.append):
        await run_last_tool_command(session, "")

    output = _printed_text(prints)
    assert "No hay llamadas" in output


@pytest.mark.asyncio
async def test_last_tool_index_out_of_range():
    """/last-tool con un índice fuera de rango muestra un error."""
    session = DummySession(_make_history())
    errors = []

    with patch("lilith_cli.extra_commands.render_error", side_effect=errors.append):
        await run_last_tool_command(session, "10")

    assert any("fuera de rango" in e for e in errors)
