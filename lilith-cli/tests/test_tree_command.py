"""Tests for the /tree slash command."""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

import pytest

from lilith_cli.extra_commands import run_tree_command


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


def _capture_output(renderable):
    """Captura el renderizable de Rich como texto plano."""
    from rich.console import Console

    buf = StringIO()
    c = Console(file=buf, force_terminal=False, color_system=None, width=120)
    c.print(renderable)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_tree_command_lists_directory_tree(tmp_path, monkeypatch):
    """/tree muestra directorios, archivos y tamaños respetando la profundidad."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('ok')", encoding="utf-8")
    (tmp_path / "README.md").write_text("# test", encoding="utf-8")
    (tmp_path / ".git").mkdir()

    session = DummySession()
    output = ""

    def capture(renderable):
        nonlocal output
        output += _capture_output(renderable)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        await run_tree_command(session, "")

    assert "Árbol de archivos" in output
    assert "src" in output
    assert "main.py" in output
    assert "README.md" in output
    assert ".git" not in output
    assert "Archivos: 2" in output
    assert "Directorios: 1" in output


@pytest.mark.asyncio
async def test_tree_command_respects_custom_path_and_depth(tmp_path, monkeypatch):
    """/tree <path> depth=N limita la profundidad y omite archivos más profundos."""
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    (nested / "deep.txt").write_text("deep", encoding="utf-8")
    (tmp_path / "a" / "top.txt").write_text("top", encoding="utf-8")

    session = DummySession()
    output = ""

    def capture(renderable):
        nonlocal output
        output += _capture_output(renderable)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        await run_tree_command(session, f"{tmp_path} depth=2")

    assert "a" in output
    assert "top.txt" in output
    assert "deep.txt" not in output
    assert "Profundidad: 2" in output


@pytest.mark.asyncio
async def test_tree_command_rejects_non_directory():
    """/tree con un archivo muestra error."""
    session = DummySession()
    errors = []

    def capture_error(text: str = ""):
        errors.append(str(text))

    with patch("lilith_cli.extra_commands.render_error", side_effect=capture_error):
        await run_tree_command(session, "__this_file_does_not_exist_123__")

    assert any("no encontrada" in e for e in errors)
