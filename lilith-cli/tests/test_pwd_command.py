"""Focused tests for the /pwd working-directory command."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from lilith_cli.extra_commands import run_pwd_command
from lilith_cli.slash_router import route, slash_commands


@pytest.mark.asyncio
async def test_pwd_shows_resolved_current_directory(
    fake_session, tmp_path, monkeypatch
) -> None:
    work = tmp_path / "workspace"
    work.mkdir()
    monkeypatch.chdir(work)
    printed: list[str] = []
    monkeypatch.setattr(
        "lilith_cli.render.console.print",
        lambda *objects, **_kwargs: printed.extend(str(obj) for obj in objects),
    )

    await run_pwd_command(fake_session, "")

    output = "\n".join(printed)
    assert "Directorio actual" in output
    assert str(work.resolve()) in output
    assert Path.cwd() == work


@pytest.mark.asyncio
async def test_pwd_rejects_arguments(
    fake_session, tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)

    await run_pwd_command(fake_session, "unexpected")

    output = capsys.readouterr().out
    assert "Uso: /pwd" in output
    assert Path.cwd() == tmp_path


def test_pwd_is_wired_in_repl_and_help() -> None:
    from lilith_cli.extra_commands import run_pwd_command
    from lilith_cli.slash_commands.help import HELP_CATALOG

    assert "/pwd" in slash_commands()
    assert route("pwd").handler is run_pwd_command
    entries = [entry for family in HELP_CATALOG.values() for entry in family]
    assert ("pwd", "Mostrar el directorio de trabajo actual") in entries
