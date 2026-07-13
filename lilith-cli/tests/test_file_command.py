"""Tests for the /file slash command."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_file_list_when_empty(fake_session, capsys):
    """/file (no args) on a session without attachments must print the empty marker."""
    from lilith_cli.extra_commands import run_file_command

    # Ensure session has no _user_files attribute to start from.
    if hasattr(fake_session, "_user_files"):
        delattr(fake_session, "_user_files")

    await run_file_command(fake_session, "")

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "no hay" in combined.lower() or "vac" in combined.lower()


@pytest.mark.asyncio
async def test_file_adds_existing_path_to_session(fake_session, tmp_path: Path):
    """/file <path> must append the path to session._user_files."""
    from lilith_cli.extra_commands import run_file_command

    target = tmp_path / "notes.txt"
    target.write_text("hola\n", encoding="utf-8")
    fake_session._user_files = []

    await run_file_command(fake_session, str(target))

    assert str(target) in fake_session._user_files


@pytest.mark.asyncio
async def test_file_clear_empties_session(fake_session, tmp_path: Path):
    """/file clear must empty session._user_files."""
    from lilith_cli.extra_commands import run_file_command

    target = tmp_path / "a.txt"
    target.write_text("x\n", encoding="utf-8")
    fake_session._user_files = [str(target)]

    await run_file_command(fake_session, "clear")

    assert fake_session._user_files == []


@pytest.mark.asyncio
async def test_file_missing_path_reports_error(fake_session, tmp_path: Path, capsys):
    """/file with a non-existent path must print a not-found error."""
    from lilith_cli.extra_commands import run_file_command

    missing = tmp_path / "does-not-exist.txt"
    fake_session._user_files = []

    await run_file_command(fake_session, str(missing))

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "no encontr" in combined.lower() or "no existe" in combined.lower()
    assert fake_session._user_files == []


@pytest.mark.asyncio
async def test_file_with_directory_reports_error(fake_session, tmp_path: Path, capsys):
    """/file on a directory path must print a 'not a file' error."""
    from lilith_cli.extra_commands import run_file_command

    fake_session._user_files = []

    await run_file_command(fake_session, str(tmp_path))

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "no es un archivo" in combined.lower() or "directorio" in combined.lower() or "ruta" in combined.lower()
    assert fake_session._user_files == []


@pytest.mark.asyncio
async def test_file_list_renders_attached_paths(fake_session, tmp_path: Path, capsys):
    """/file --list (alias for list/ls) must print every attached path."""
    from lilith_cli.extra_commands import run_file_command

    a = tmp_path / "alpha_one.py"
    b = tmp_path / "beta_two.py"
    a.write_text("# a\n", encoding="utf-8")
    b.write_text("# b\n", encoding="utf-8")
    fake_session._user_files = [str(a), str(b)]

    await run_file_command(fake_session, "--list")

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    # Rich abbreviates long Windows paths with an ellipsis in the middle, so
    # assert on basenames which are always rendered verbatim.
    assert "alpha_one.py" in combined
    assert "beta_two.py" in combined