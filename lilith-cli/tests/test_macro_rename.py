"""Focused tests for the /macro rename subcommand."""

from __future__ import annotations

import json

import pytest

from lilith_cli.commands import MacroCommand


class _Session:
    history: list = []


@pytest.fixture
def macros_path(tmp_path, monkeypatch):
    path = tmp_path / "macros.json"
    path.write_text(
        json.dumps(
            {
                "deploy": ["/status", "/test"],
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
async def test_macro_rename_moves_commands_to_new_name(macros_path, capsys):
    await MacroCommand(_Session()).execute("rename deploy release")

    macros = _read_macros(macros_path)
    assert "deploy" not in macros
    assert macros["release"] == ["/status", "/test"]
    out = capsys.readouterr().out
    assert "deploy" in out
    assert "release" in out


@pytest.mark.asyncio
async def test_macro_rename_requires_old_and_new_names(macros_path, capsys):
    before = _read_macros(macros_path)

    await MacroCommand(_Session()).execute("rename deploy")

    assert _read_macros(macros_path) == before
    assert "Uso: /macro rename <actual> <nuevo>" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_macro_rename_rejects_unknown_source(macros_path, capsys):
    before = _read_macros(macros_path)

    await MacroCommand(_Session()).execute("rename missing release")

    assert _read_macros(macros_path) == before
    assert "missing" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_macro_rename_rejects_existing_destination(macros_path, capsys):
    before = _read_macros(macros_path)

    await MacroCommand(_Session()).execute("rename deploy existing")

    assert _read_macros(macros_path) == before
    assert "existing" in capsys.readouterr().out


@pytest.mark.asyncio
@pytest.mark.parametrize("new_name", ["two words", "bad/name"])
async def test_macro_rename_rejects_invalid_destination(
    macros_path, capsys, new_name
):
    before = _read_macros(macros_path)

    await MacroCommand(_Session()).execute(f"rename deploy {new_name}")

    assert _read_macros(macros_path) == before
    assert "no puede contener" in capsys.readouterr().out
