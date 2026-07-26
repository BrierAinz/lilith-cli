"""Focused tests for ``/macro validate``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lilith_cli.commands import MacroCommand


class _Session:
    """Minimal session accepted by ``MacroCommand`` and ``CommandRegistry``."""


@pytest.fixture
def macros_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "macros.json"
    monkeypatch.setattr("lilith_cli.commands._MACROS_PATH", path)
    return path


def _seed(path: Path, commands: list[object], name: str = "deploy") -> None:
    path.write_text(
        json.dumps({name: commands}, ensure_ascii=False),
        encoding="utf-8",
    )


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_macro_validate_accepts_registered_commands(macros_path: Path, capsys) -> None:
    """Registered command names and aliases are reported as valid."""
    _seed(macros_path, ["/help", "/h", "/macro list"])

    await MacroCommand(_Session()).execute("validate deploy")

    out = capsys.readouterr().out
    assert "es válida" in out
    assert "3 comando(s)" in out


@pytest.mark.asyncio
async def test_macro_validate_reports_unknown_and_malformed_lines(
    macros_path: Path, capsys
) -> None:
    """Unknown commands and lines without a slash are listed with line numbers."""
    _seed(macros_path, ["/help", "/does-not-exist", "plain text"])

    await MacroCommand(_Session()).execute("validate deploy")

    out = capsys.readouterr().out
    assert "línea 2" in out
    assert "línea 3" in out
    assert "does-not-exist" in out
    assert "falta el '/' inicial" in out
    assert "comando desconocido" in out


@pytest.mark.asyncio
async def test_macro_validate_ignores_comments_and_blank_lines(
    macros_path: Path, capsys
) -> None:
    """Comments and blank lines follow the edit format and are not validated."""
    _seed(macros_path, ["# explanatory note", "", "  ", "/help"])

    await MacroCommand(_Session()).execute("check deploy")

    out = capsys.readouterr().out
    assert "es válida" in out
    assert "1 comando(s)" in out
    assert "ignorada" in out


@pytest.mark.asyncio
async def test_macro_validate_handles_non_string_entries(
    macros_path: Path, capsys
) -> None:
    """Malformed JSON entries produce a validation error instead of a traceback."""
    _seed(macros_path, ["/help", 42])

    await MacroCommand(_Session()).execute("validate deploy")

    out = capsys.readouterr().out
    assert "línea 2" in out
    assert "no es texto" in out


@pytest.mark.asyncio
async def test_macro_validate_errors_for_missing_macro_without_mutating_file(
    macros_path: Path, capsys
) -> None:
    """Missing names are rejected and validation never rewrites macros.json."""
    _seed(macros_path, ["/help"])
    before = _read(macros_path)

    await MacroCommand(_Session()).execute("validate missing")

    assert _read(macros_path) == before
    out = capsys.readouterr().out
    assert "missing" in out
    assert "no encontrada" in out


@pytest.mark.asyncio
async def test_macro_validate_requires_a_name(macros_path: Path, capsys) -> None:
    """The subcommand without a macro name prints its usage."""
    await MacroCommand(_Session()).execute("validate")

    out = capsys.readouterr().out
    assert "Uso: /macro validate <nombre>" in out
