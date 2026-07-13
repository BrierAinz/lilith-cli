"""Tests for /json-mode command."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from lilith_cli.extra_commands import run_json_mode_command


class DummyConfig:
    def __init__(self):
        self.model = "test"
        self.provider = "test"


class DummySession:
    def __init__(self):
        self.config = DummyConfig()
        self._json_mode = False


@pytest.mark.asyncio
async def test_json_mode_on_and_off():
    """/json-mode on y off cambian el flag _json_mode de la sesión."""
    session = DummySession()

    with patch("lilith_cli.extra_commands.console.print"):
        await run_json_mode_command(session, "on")
    assert session._json_mode is True

    with patch("lilith_cli.extra_commands.console.print"):
        await run_json_mode_command(session, "off")
    assert session._json_mode is False


@pytest.mark.asyncio
async def test_json_mode_status():
    """/json-mode status refleja el estado actual."""
    session = DummySession()
    prints = []

    def capture(text: str = ""):
        prints.append(text)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        await run_json_mode_command(session, "status")

    assert any("OFF" in str(p) for p in prints)

    session._json_mode = True
    prints.clear()

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        await run_json_mode_command(session, "")

    assert any("ON" in str(p) for p in prints)
