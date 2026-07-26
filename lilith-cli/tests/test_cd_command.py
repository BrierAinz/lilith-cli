"""Focused tests for the /cd working-directory command."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from lilith_cli.extra_commands import run_cd_command


@pytest.mark.asyncio
async def test_cd_without_args_shows_current_directory(fake_session, tmp_path, monkeypatch, capsys):
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)

    await run_cd_command(fake_session, "")

    output = capsys.readouterr().out
    assert "Directorio actual" in output
    assert work.name in output
    assert Path.cwd() == work


@pytest.mark.asyncio
async def test_cd_changes_directory_and_reports_destination(fake_session, tmp_path, monkeypatch, capsys):
    start = tmp_path / "start"
    target = tmp_path / "target folder"
    start.mkdir()
    target.mkdir()
    monkeypatch.chdir(start)

    await run_cd_command(fake_session, str(target))

    output = capsys.readouterr().out
    assert Path.cwd() == target
    assert "Directorio actual" in output
    assert target.name in output


@pytest.mark.asyncio
async def test_cd_expands_home(fake_session, tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    start = tmp_path / "start"
    home.mkdir()
    start.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.chdir(start)

    await run_cd_command(fake_session, "~")

    assert Path.cwd() == home
    assert home.name in capsys.readouterr().out


@pytest.mark.asyncio
async def test_cd_missing_directory_preserves_cwd(fake_session, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    await run_cd_command(fake_session, "does-not-exist")

    assert Path.cwd() == tmp_path
    output = capsys.readouterr().out.lower()
    assert "no existe" in output


@pytest.mark.asyncio
async def test_cd_rejects_files(fake_session, tmp_path, monkeypatch, capsys):
    file_path = tmp_path / "archivo.txt"
    file_path.write_text("x", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    await run_cd_command(fake_session, str(file_path))

    assert Path.cwd() == tmp_path
    assert "directorio" in capsys.readouterr().out.lower()


@pytest.mark.asyncio
async def test_cd_accepts_quoted_path_with_spaces(fake_session, tmp_path, monkeypatch, capsys):
    """Rutas con espacios entre comillas: sin desenvolverlas se toman como relativas."""
    start = tmp_path / "start"
    target = tmp_path / "Influencer IA"
    start.mkdir()
    target.mkdir()
    monkeypatch.chdir(start)

    await run_cd_command(fake_session, f'"{target}"')

    assert Path.cwd() == target
    assert "Directorio actual" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_cd_accepts_single_quoted_path(fake_session, tmp_path, monkeypatch):
    start = tmp_path / "start"
    target = tmp_path / "otra carpeta"
    start.mkdir()
    target.mkdir()
    monkeypatch.chdir(start)

    await run_cd_command(fake_session, f"'{target}'")

    assert Path.cwd() == target


@pytest.mark.asyncio
async def test_cd_with_only_quotes_is_an_error(fake_session, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    await run_cd_command(fake_session, '""')

    assert Path.cwd() == tmp_path
    assert "uso" in capsys.readouterr().out.lower()


def test_cd_is_wired_in_repl():
    import lilith_cli.repl as repl_module

    assert "/cd" in repl_module._SLASH_COMMANDS
    source = inspect.getsource(repl_module.run_repl)
    assert 'cmd_name == "cd"' in source
    assert "run_cd_command(session, cmd_args)" in source
