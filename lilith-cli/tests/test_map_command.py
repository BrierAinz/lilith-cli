"""Pruebas del mapa de símbolos para /tree symbols."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from lilith_cli.extra_commands import run_tree_command


class DummySession:
    pass


@pytest.mark.asyncio
async def test_tree_symbols_muestra_modulos_y_simbolos(tmp_path, capsys):
    """El subcomando symbols resume funciones y clases públicas."""
    (tmp_path / "modulo.py").write_text(
        "class Publica:\n    pass\n\ndef funciona():\n    return 1\n\ndef _privada():\n    return 2\n",
        encoding="utf-8",
    )

    await run_tree_command(DummySession(), f"symbols {tmp_path}")

    output = capsys.readouterr().out
    assert "Mapa del código" in output
    assert "modulo.py" in output
    assert "Publica, funciona" in output
    assert "_privada" not in output


@pytest.mark.asyncio
async def test_tree_symbols_json_es_machinereadable(tmp_path):
    """El formato JSON permite automatizar el inventario de símbolos."""
    (tmp_path / "modulo.py").write_text("def ejecuta():\n    pass\n", encoding="utf-8")
    prints: list[str] = []

    # `prints.append` pelado no acepta kwargs, y la salida JSON pasa
    # soft_wrap/markup/highlight para que Rich no parta el JSON en varias
    # lineas. Se ignoran los kwargs y se guarda solo el texto.
    with patch(
        "lilith_cli.extra_commands.console.print",
        side_effect=lambda *a, **kw: prints.append(a[0] if a else ""),
    ):
        await run_tree_command(DummySession(), f"symbols {tmp_path} --json")

    payload = json.loads(prints[0])
    assert payload[0]["archivo"] == "modulo.py"
    assert payload[0]["símbolos"] == "ejecuta"
    assert payload[0]["cantidad"] == 1


@pytest.mark.asyncio
async def test_tree_symbols_rejects_ambiguous_extra_arguments(tmp_path, capsys):
    """El mapa no debe ignorar argumentos posicionales adicionales."""
    await run_tree_command(DummySession(), f"symbols {tmp_path} inesperado")

    output = capsys.readouterr().out
    assert "Uso: /map" in output
