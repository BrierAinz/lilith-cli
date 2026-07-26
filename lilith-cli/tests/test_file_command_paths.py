"""Regression tests for quoted paths accepted by /file."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_file_adds_quoted_path_with_spaces(fake_session, tmp_path: Path):
    """/file accepts a path enclosed in quotes when its name has spaces."""
    from lilith_cli.extra_commands import run_file_command

    target = tmp_path / "folder with spaces" / "notes.txt"
    target.parent.mkdir()
    target.write_text("hola\n", encoding="utf-8")
    fake_session._user_files = []

    await run_file_command(fake_session, f'"{target}"')

    assert fake_session._user_files == [str(target)]


@pytest.mark.asyncio
async def test_file_unbalanced_quotes_report_error(fake_session, capsys):
    """Malformed quoted paths produce a Spanish error and no attachment."""
    from lilith_cli.extra_commands import run_file_command

    fake_session._user_files = []
    await run_file_command(fake_session, '"missing file.py')

    output = capsys.readouterr().out.lower()
    assert "comilla" in output or "inválida" in output
    assert fake_session._user_files == []
