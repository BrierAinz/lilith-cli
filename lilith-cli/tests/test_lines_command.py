"""Tests for /lines slash command."""

from __future__ import annotations

import asyncio


def _run(coro):
    return asyncio.run(coro)


def test_lines_counts_correctly(fake_session, capsys, tmp_path):
    """/lines reports line count for a multi-line file."""
    from lilith_cli.extra_commands import run_lines_command

    f = tmp_path / "data.txt"
    f.write_text("line 1\nline 2\nline 3\n")

    _run(run_lines_command(fake_session, str(f)))

    out = capsys.readouterr().out
    assert "L\u00edneas:" in out
    assert "3" in out


def test_lines_single_line_no_newline(fake_session, capsys, tmp_path):
    """/lines counts a single-line file as 1 line."""
    from lilith_cli.extra_commands import run_lines_command

    f = tmp_path / "single.txt"
    f.write_text("only line")

    _run(run_lines_command(fake_session, str(f)))

    out = capsys.readouterr().out
    assert "1" in out


def test_lines_empty_file(fake_session, capsys, tmp_path):
    """/lines on empty file shows 0."""
    from lilith_cli.extra_commands import run_lines_command

    f = tmp_path / "empty.txt"
    f.write_text("")

    _run(run_lines_command(fake_session, str(f)))

    out = capsys.readouterr().out
    assert "0" in out


def test_lines_shows_words_and_chars(fake_session, capsys, tmp_path):
    """/lines shows word and char counts."""
    from lilith_cli.extra_commands import run_lines_command

    f = tmp_path / "words.txt"
    f.write_text("hello world foo bar\n")

    _run(run_lines_command(fake_session, str(f)))

    out = capsys.readouterr().out
    assert "palabras:" in out
    assert "chars:" in out
    assert "4" in out  # 4 words


def test_lines_missing_file(fake_session, capsys):
    """/lines with missing file shows error."""
    from lilith_cli.extra_commands import run_lines_command

    _run(run_lines_command(fake_session, "/no/existe/file.txt"))

    out = capsys.readouterr().out
    assert "No existe" in out or "no encontrado" in out.lower()


def test_lines_no_args(fake_session, capsys):
    """/lines with no args shows usage."""
    from lilith_cli.extra_commands import run_lines_command

    _run(run_lines_command(fake_session, ""))

    out = capsys.readouterr().out
    assert "Uso:" in out


def test_lines_directory_target(fake_session, capsys, tmp_path):
    """/lines on a directory shows error (not crash)."""
    from lilith_cli.extra_commands import run_lines_command

    _run(run_lines_command(fake_session, str(tmp_path)))

    out = capsys.readouterr().out
    assert "no es un archivo" in out.lower() or "not a file" in out.lower()