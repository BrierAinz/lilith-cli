"""Focused tests for ``/macro import``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lilith_cli.commands import MacroCommand


class _Session:
    """Minimal session accepted by ``MacroCommand``."""


@pytest.fixture
def macros_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "macros.json"
    monkeypatch.setattr("lilith_cli.commands._MACROS_PATH", path)
    return path


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_macro_import_persists_commands_and_uses_file_stem(
    macros_path: Path, tmp_path: Path, capsys
) -> None:
    """Import reads one command per line and defaults the name to the file stem."""
    source = tmp_path / "deploy.txt"
    source.write_text(
        "# deployment steps\n\n/status\n  /help providers  \n",
        encoding="utf-8",
    )

    await MacroCommand(_Session()).execute(f"import {source}")

    assert _read(macros_path) == {
        "deploy": ["/status", "/help providers"],
    }
    assert "importada" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_macro_import_accepts_explicit_name(macros_path: Path, tmp_path: Path) -> None:
    """An explicit destination name overrides the source file stem."""
    source = tmp_path / "steps.txt"
    source.write_text("/status\n", encoding="utf-8")

    await MacroCommand(_Session()).execute(f"import {source} release")

    assert _read(macros_path) == {"release": ["/status"]}


@pytest.mark.asyncio
async def test_macro_import_missing_source_does_not_mutate_storage(
    macros_path: Path, tmp_path: Path, capsys
) -> None:
    """A missing source is rejected without creating or changing macros.json."""
    macros_path.write_text(json.dumps({"existing": ["/help"]}), encoding="utf-8")
    before = _read(macros_path)

    await MacroCommand(_Session()).execute(f"import {tmp_path / 'missing.txt'}")

    assert _read(macros_path) == before
    assert "no encontrado" in capsys.readouterr().out.lower()


@pytest.mark.asyncio
async def test_macro_import_collision_does_not_overwrite_existing_macro(
    macros_path: Path, tmp_path: Path, capsys
) -> None:
    """Import refuses to replace an existing macro."""
    macros_path.write_text(json.dumps({"deploy": ["/old"]}), encoding="utf-8")
    source = tmp_path / "deploy.txt"
    source.write_text("/new\n", encoding="utf-8")
    before = _read(macros_path)

    await MacroCommand(_Session()).execute(f"import {source}")

    assert _read(macros_path) == before
    assert "ya existe" in capsys.readouterr().out.lower()


@pytest.mark.asyncio
async def test_macro_import_rejects_invalid_destination_name(
    macros_path: Path, tmp_path: Path, capsys
) -> None:
    """Destination names cannot contain path separators or whitespace."""
    source = tmp_path / "steps.txt"
    source.write_text("/status\n", encoding="utf-8")

    await MacroCommand(_Session()).execute(f"import {source} bad/name")

    assert not macros_path.exists()
    assert "no puede contener" in capsys.readouterr().out.lower()


@pytest.mark.asyncio
async def test_macro_import_without_arguments_shows_usage(
    macros_path: Path, capsys
) -> None:
    """The subcommand requires at least a source path."""
    await MacroCommand(_Session()).execute("import")

    assert not macros_path.exists()
    assert "uso: /macro import" in capsys.readouterr().out.lower()
