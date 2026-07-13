"""Tests for the /tour slash command."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from lilith_cli.extra_commands import _TOUR_STEPS, run_tour_command


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


@pytest.mark.asyncio
async def test_tour_runs_all_steps():
    """/tour muestra los 5 pasos del recorrido interactivo."""
    session = DummySession()
    prints = []

    def capture(text: str = ""):
        prints.append(str(text))

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        await run_tour_command(session, "")

    assert any("Recorrido interactivo" in p for p in prints)
    for title, _ in _TOUR_STEPS:
        assert any(title in p for p in prints)
    assert any("Recorrido completado" in p for p in prints)


@pytest.mark.asyncio
async def test_tour_step_and_skip():
    """/tour step N salta al paso indicado; /tour skip termina el recorrido."""
    session = DummySession()
    prints = []

    def capture(text: str = ""):
        prints.append(str(text))

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        await run_tour_command(session, "step 2")
        await run_tour_command(session, "skip")

    joined = "\n".join(prints)
    assert "Seguridad: confirm_write y undo" in joined
    assert "Paso 2/5" in joined
    assert "Recorrido cancelado" in joined
    assert "Bienvenido a Lilith" not in joined
    assert "Herramientas principales" not in joined
