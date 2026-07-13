"""Tests for the /template slash command."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from lilith_cli.commands import TemplateCommand, _DEFAULT_TEMPLATES


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
        self._last_user_message = ""


@pytest.fixture
def template_path(tmp_path, monkeypatch):
    """Use a temporary templates.json file for each test."""
    path = tmp_path / "templates.json"
    monkeypatch.setattr(TemplateCommand, "_templates_path", classmethod(lambda cls: path))
    return path


@pytest.mark.asyncio
async def test_template_list_shows_defaults(template_path, monkeypatch):
    """/template list muestra las plantillas predefinidas."""
    session = DummySession()
    cmd = TemplateCommand(session)
    prints = []

    def capture(text: str = ""):
        prints.append(text)

    with patch("lilith_cli.commands.console.print", side_effect=capture):
        await cmd.execute("list")

    output = " ".join(str(p) for p in prints)
    for name in _DEFAULT_TEMPLATES:
        assert name in output


@pytest.mark.asyncio
async def test_template_save_list_show_delete_roundtrip(template_path, monkeypatch):
    """Guardar, listar, mostrar y eliminar una plantilla funciona en secuencia."""
    session = DummySession()
    cmd = TemplateCommand(session)
    prints = []

    def capture(text: str = ""):
        prints.append(text)

    with patch("lilith_cli.commands.console.print", side_effect=capture):
        await cmd.execute("save test-hola decir hola")
        await cmd.execute("list")
        await cmd.execute("show test-hola")
        await cmd.execute("delete test-hola")
        await cmd.execute("list")

    output = "\n".join(str(p) for p in prints)
    assert "Plantilla guardada" in output
    assert "test-hola" in output
    assert "decir hola" in output
    assert "Plantilla eliminada" in output


@pytest.mark.asyncio
async def test_template_use_loads_prompt_into_history(template_path, monkeypatch):
    """/template use <name> carga el contenido como mensaje del usuario."""
    session = DummySession()
    cmd = TemplateCommand(session)
    prints = []

    def capture(text: str = ""):
        prints.append(text)

    with patch("lilith_cli.commands.console.print", side_effect=capture):
        await cmd.execute("use explain-code")

    assert session.history
    assert session.history[-1]["role"] == "user"
    assert "Explain what this code does" in session.history[-1]["content"]
    assert session._last_user_message == session.history[-1]["content"]


@pytest.mark.asyncio
async def test_template_delete_unknown_shows_error(template_path, monkeypatch):
    """Eliminar una plantilla inexistente muestra error."""
    session = DummySession()
    cmd = TemplateCommand(session)
    errors = []

    def capture_error(text: str = ""):
        errors.append(text)

    with patch("lilith_cli.commands.render_error", side_effect=capture_error):
        await cmd.execute("delete no-existe")

    assert any("no encontrada" in str(e) for e in errors)
