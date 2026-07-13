"""Tests for lilith_cli.extra_commands.run_compare_command (/compare).

Cubre los tres modos (files, json, text) y el manejo de errores del comando
nuevo, siguiendo el mismo patrón DummyConfig/DummySession que
test_extra_commands.py.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from lilith_cli.extra_commands import run_compare_command


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
async def test_compare_files_shows_diff(tmp_path, monkeypatch):
    """/compare files debe mostrar tanto líneas añadidas como eliminadas."""
    file_a = tmp_path / "a.py"
    file_b = tmp_path / "b.py"
    file_a.write_text("def saludar():\n    print('hola viejo')\n", encoding="utf-8")
    file_b.write_text("def saludar():\n    print('hola nuevo')\n", encoding="utf-8")

    # El cache a ~/.yggdrasil/compare_last.json puede no existir; redireccionamos
    # HOME a tmp_path para no contaminar el HOME real.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    session = DummySession()
    rendered_pieces: list[str] = []

    def capture(*args, **kwargs):
        from rich.syntax import Syntax as _Syntax

        for a in args:
            if isinstance(a, _Syntax):
                # El diff propiamente vive en .code dentro del renderable.
                rendered_pieces.append(str(a.code))
            else:
                rendered_pieces.append(str(a))
        for v in kwargs.values():
            if v is not None:
                rendered_pieces.append(str(v))

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        await run_compare_command(session, f"files {file_a} {file_b}")

    rendered = "\n".join(rendered_pieces)
    assert "hola viejo" in rendered, (
        f"Esperaba ver la línea eliminada 'hola viejo' en: {rendered[:500]}"
    )
    assert "hola nuevo" in rendered, (
        f"Esperaba ver la línea añadida 'hola nuevo' en: {rendered[:500]}"
    )


@pytest.mark.asyncio
async def test_compare_json_reports_change(tmp_path, monkeypatch):
    """/compare json debe reportar la clave anidada que cambió como 'cambiado'."""
    file_a = tmp_path / "a.json"
    file_b = tmp_path / "b.json"
    # Estructura idéntica salvo que 'valor' dentro de c cambia de 1 a 2.
    file_a.write_text(
        '{"a": {"b": {"c": {"valor": 1, "k": "x"}}, "z": 9}}',
        encoding="utf-8",
    )
    file_b.write_text(
        '{"a": {"b": {"c": {"valor": 2, "k": "x"}}, "z": 9}}',
        encoding="utf-8",
    )

    monkeypatch.setenv("HOME", str(tmp_path))

    session = DummySession()
    prints: list[str] = []

    def capture(*args, **kwargs):
        for a in args:
            prints.append(str(a))
        for v in kwargs.values():
            if v is not None:
                prints.append(str(v))

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        await run_compare_command(session, f"json {file_a} {file_b}")

    rendered = "\n".join(prints)
    assert "cambiado" in rendered, (
        f"Esperaba la palabra 'cambiado' en la salida, obtuve: {rendered[:500]}"
    )


@pytest.mark.asyncio
async def test_compare_missing_args_renders_error(tmp_path):
    """/compare sin subcomando debe imprimir ayuda y no fallar."""
    session = DummySession()
    prints: list = []

    def capture(*args, **kwargs):
        for a in args:
            prints.append(str(a))

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        await run_compare_command(session, "")

    rendered = "\n".join(prints)
    assert "/compare" in rendered
    assert "files" in rendered
    assert "json" in rendered
    assert "text" in rendered


@pytest.mark.asyncio
async def test_compare_missing_file_renders_error(tmp_path, monkeypatch):
    """/compare files con una ruta inexistente debe llamar render_error."""
    file_a = tmp_path / "exists.txt"
    file_a.write_text("hola\n", encoding="utf-8")
    file_b = tmp_path / "no_existe.txt"  # nunca se crea

    monkeypatch.setenv("HOME", str(tmp_path))
    session = DummySession()
    prints: list = []
    errors: list = []

    def capture_print(*args, **kwargs):
        for a in args:
            prints.append(str(a))

    def capture_error(*args, **kwargs):
        for a in args:
            errors.append(str(a))

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture_print):
        with patch("lilith_cli.extra_commands.render_error", side_effect=capture_error):
            await run_compare_command(session, f"files {file_a} {file_b}")

    joined_errors = "\n".join(errors)
    assert "no_existe" in joined_errors or "no existe" in joined_errors.lower(), (
        f"Esperaba render_error mencionando la ruta inexistente: {joined_errors}"
    )
