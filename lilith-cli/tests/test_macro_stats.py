"""Focused tests for the ``/macro stats`` subcommand."""

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


@pytest.mark.asyncio
async def test_macro_stats_reports_counts_average_and_longest(
    macros_path: Path, capsys
) -> None:
    """Stats summarize the saved macro collection without playing it."""
    macros_path.write_text(
        json.dumps(
            {
                "deploy": ["/status", "/help", "/pwd"],
                "quick": ["/clear"],
            }
        ),
        encoding="utf-8",
    )

    await MacroCommand(_Session()).execute("stats")

    out = capsys.readouterr().out
    assert "Macros guardadas" in out
    assert "2" in out
    assert "Comandos totales" in out
    assert "4" in out
    assert "Promedio" in out
    assert "2.00" in out
    assert "Más larga" in out
    assert "deploy" in out
    assert "3 comando(s)" in out


@pytest.mark.asyncio
async def test_macro_stats_handles_empty_storage(macros_path: Path, capsys) -> None:
    """An empty macro store gets a useful message instead of a division error."""
    macros_path.write_text("{}", encoding="utf-8")

    await MacroCommand(_Session()).execute("stats")

    out = capsys.readouterr().out
    assert "No hay macros guardadas" in out


@pytest.mark.asyncio
async def test_macro_stats_does_not_mutate_persisted_macros(
    macros_path: Path, capsys
) -> None:
    """The read-only report leaves the JSON payload byte-for-byte unchanged."""
    macros_path.write_text(
        json.dumps({"one": ["/help"]}, indent=2),
        encoding="utf-8",
    )
    before = macros_path.read_text(encoding="utf-8")

    await MacroCommand(_Session()).execute("stats")

    assert macros_path.read_text(encoding="utf-8") == before
    assert "Comandos totales" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_macro_stats_requires_no_macro_name(macros_path: Path, capsys) -> None:
    """Stats is an aggregate subcommand and ignores an absent macro name."""
    macros_path.write_text(json.dumps({"one": ["/help"]}), encoding="utf-8")

    await MacroCommand(_Session()).execute("stats")

    assert "Uso: /macro stats" not in capsys.readouterr().out
