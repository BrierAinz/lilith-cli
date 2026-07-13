"""Tests for /pin command in lilith_cli.extra_commands."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from lilith_cli.extra_commands import run_pin_command


class DummyConfig:
    def __init__(self):
        self.model = "test"
        self.provider = "test"
        self.providers = {}
        self.api_key = ""
        self.system_prompt = ""

    def model_dump(self):
        return {
            "model": self.model,
            "provider": self.provider,
            "providers": self.providers,
            "api_key": self.api_key,
        }


class DummySession:
    def __init__(self):
        self.config = DummyConfig()
        self.memory = None
        self.history = []
        self.provider = None
        self.system_prompt = ""
        self._pinned_messages: list[dict] = []


@pytest.mark.asyncio
async def test_pin_pinned_messages_list_and_clear():
    """/pin list y /pin clear manejan la lista de mensajes pineados."""
    session = DummySession()
    session.history = [
        {"role": "user", "content": "primero"},
        {"role": "assistant", "content": "segundo"},
        {"role": "user", "content": "tercero"},
    ]

    prints = []

    def capture(text: str = ""):
        prints.append(str(text))

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        await run_pin_command(session, "1")
        await run_pin_command(session, "3")
        await run_pin_command(session, "list")
        await run_pin_command(session, "clear")
        await run_pin_command(session, "list")

    assert len(session._pinned_messages) == 0
    assert any("tercero" in p for p in prints)
    assert any("Mensajes pineados" in p for p in prints)
    assert any("No hay mensajes pineados" in p for p in prints)


@pytest.mark.asyncio
async def test_pin_remove_nth_pinned_message():
    """/pin remove <n> elimina el n-ésimo mensaje pineado."""
    session = DummySession()
    session.history = [
        {"role": "user", "content": "primero"},
        {"role": "assistant", "content": "segundo"},
        {"role": "user", "content": "tercero"},
    ]

    prints = []

    def capture(text: str = ""):
        prints.append(str(text))

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        await run_pin_command(session, "3")
        await run_pin_command(session, "2")
        await run_pin_command(session, "remove 1")

    assert len(session._pinned_messages) == 1
    assert session._pinned_messages[0]["content"] == "segundo"
    assert any("Despineado" in p for p in prints)
