"""Focused tests for the ``/macro export`` subcommand."""

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
    path.write_text(
        json.dumps({"deploy": ["/status", "/help providers"]}),
        encoding="utf-8",
    )
    monkeypatch.setattr("lilith_cli.commands._MACROS_PATH", path)
    return path


@pytest.mark.asyncio
async def test_macro_export_writes_one_command_per_line(
    macros_path: Path, tmp_path: Path, capsys
) -> None:
    """An explicit destination receives the saved commands as UTF-8 text."""
    destination = tmp_path / "release.txt"

    await MacroCommand(_Session()).execute(f"export deploy {destination}")

    assert destination.read_text(encoding="utf-8") == "/status\n/help providers\n"
    assert json.loads(macros_path.read_text(encoding="utf-8")) == {
        "deploy": ["/status", "/help providers"]
    }
    assert "exportada" in capsys.readouterr().out.lower()


@pytest.mark.asyncio
async def test_macro_export_defaults_to_macro_name_txt_in_current_directory(
    macros_path: Path, tmp_path: Path, monkeypatch
) -> None:
    """Omitting the destination creates ``<name>.txt`` in the current directory."""
    monkeypatch.chdir(tmp_path)

    await MacroCommand(_Session()).execute("export deploy")

    assert (tmp_path / "deploy.txt").read_text(encoding="utf-8") == (
        "/status\n/help providers\n"
    )


@pytest.mark.asyncio
async def test_macro_export_accepts_quoted_destination_with_spaces(
    macros_path: Path, tmp_path: Path
) -> None:
    """Quoted paths work when the destination contains spaces."""
    destination = tmp_path / "release notes.txt"

    await MacroCommand(_Session()).execute(f'export deploy "{destination}"')

    assert destination.read_text(encoding="utf-8") == "/status\n/help providers\n"


@pytest.mark.asyncio
async def test_macro_export_missing_macro_does_not_create_destination(
    macros_path: Path, tmp_path: Path, capsys
) -> None:
    """Unknown macros are rejected before the destination is touched."""
    destination = tmp_path / "missing.txt"

    await MacroCommand(_Session()).execute(f"export missing {destination}")

    assert not destination.exists()
    assert "no encontrada" in capsys.readouterr().out.lower()


@pytest.mark.asyncio
async def test_macro_export_refuses_to_overwrite_existing_file(
    macros_path: Path, tmp_path: Path, capsys
) -> None:
    """An existing destination remains unchanged unless explicitly removed."""
    destination = tmp_path / "release.txt"
    destination.write_text("keep this file\n", encoding="utf-8")

    await MacroCommand(_Session()).execute(f"export deploy {destination}")

    assert destination.read_text(encoding="utf-8") == "keep this file\n"
    assert "ya existe" in capsys.readouterr().out.lower()


@pytest.mark.asyncio
async def test_macro_export_requires_a_macro_name(
    macros_path: Path, tmp_path: Path, capsys
) -> None:
    """The export subcommand requires at least the source macro name."""
    await MacroCommand(_Session()).execute("export")

    assert not (tmp_path / "export.txt").exists()
    assert "uso: /macro export" in capsys.readouterr().out.lower()
