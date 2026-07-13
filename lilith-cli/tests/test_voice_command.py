"""Tests for the /voice slash command."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from lilith_cli.extra_commands import _speak_text, run_voice_command


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
        self._voice_enabled = False


@pytest.mark.asyncio
async def test_voice_command_status_and_toggle():
    """/voice status y on/off togglean session._voice_enabled."""
    session = DummySession()
    prints = []

    def capture(text: str = ""):
        prints.append(text)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture), \
         patch("lilith_cli.extra_commands._speak_text"):
        # Status (off)
        await run_voice_command(session, "status")
        assert session._voice_enabled is False
        # Turn on
        await run_voice_command(session, "on")
        assert session._voice_enabled is True
        # Turn off
        await run_voice_command(session, "off")
        assert session._voice_enabled is False

    output = "\n".join(str(p) for p in prints)
    assert "Voice mode" in output


def test_speak_text_handles_empty_input():
    """/voice empty input returns False without crashing."""
    result = _speak_text("")
    assert result is False
    result = _speak_text("   ")
    assert result is False
