"""Focused tests for the /macro copy subcommand."""

from __future__ import annotations

import json

import pytest

from lilith_cli.commands import MacroCommand


class _Session:
    pass


@pytest.fixture
def macros_path(tmp_path, monkeypatch):
    path = tmp_path / "macros.json"
    path.write_text(
        json.dumps(
            {
                "deploy": ["/status", "/env APP_ENV"],
                "existing": ["/help"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("lilith_cli.commands._MACROS_PATH", path)
    return path


def _read_macros(path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_macro_copy_duplicates_commands_under_new_name(macros_path, capsys):
    await MacroCommand(_Session()).execute("copy deploy release")

    macros = _read_macros(macros_path)
    assert macros["deploy"] == ["/status", "/env APP_ENV"]
    assert macros["release"] == macros["deploy"]
    assert "Macro copiada" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_macro_copy_alias_duplicates_commands(macros_path):
    await MacroCommand(_Session()).execute("cp deploy release")

    assert _read_macros(macros_path)["release"] == ["/status", "/env APP_ENV"]


@pytest.mark.asyncio
async def test_macro_copy_requires_source_and_destination(macros_path, capsys):
    before = _read_macros(macros_path)

    await MacroCommand(_Session()).execute("copy deploy")

    assert _read_macros(macros_path) == before
    assert "Uso: /macro copy <origen> <copia>" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_macro_copy_rejects_unknown_source(macros_path, capsys):
    before = _read_macros(macros_path)

    await MacroCommand(_Session()).execute("copy missing release")

    assert _read_macros(macros_path) == before
    assert "Macro no encontrada: missing" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_macro_copy_rejects_existing_destination(macros_path, capsys):
    before = _read_macros(macros_path)

    await MacroCommand(_Session()).execute("copy deploy existing")

    assert _read_macros(macros_path) == before
    assert "ya existe" in capsys.readouterr().out


@pytest.mark.asyncio
@pytest.mark.parametrize("destination", ["two words", "bad/name"])
async def test_macro_copy_rejects_invalid_destination(
    macros_path, capsys, destination
):
    before = _read_macros(macros_path)

    await MacroCommand(_Session()).execute(f"copy deploy {destination}")

    assert _read_macros(macros_path) == before
    assert "no puede contener" in capsys.readouterr().out
