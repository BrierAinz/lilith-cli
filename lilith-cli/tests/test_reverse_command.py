"""Tests for the /reverse slash command."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_reverse_reverses_string(fake_session, capsys):
    """/reverse <text> must print the reversed string."""
    from lilith_cli.extra_commands import run_reverse_command

    await run_reverse_command(fake_session, "hola")

    out = capsys.readouterr().out
    assert "aloh" in out


@pytest.mark.asyncio
async def test_reverse_with_empty_input_reports_error(fake_session, capsys):
    """/reverse with no args must print a usage error and not crash."""
    from lilith_cli.extra_commands import run_reverse_command

    await run_reverse_command(fake_session, "")

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "uso" in combined.lower()


@pytest.mark.asyncio
async def test_reverse_lines_mode_reverses_lines(fake_session, capsys):
    """/reverse --lines <multi-line> must reverse line order."""
    from lilith_cli.extra_commands import run_reverse_command

    await run_reverse_command(fake_session, "--lines uno\ndos\ntres")

    out = capsys.readouterr().out
    # Order matters: tres first, then dos, then uno.
    assert out.index("tres") < out.index("dos") < out.index("uno")


@pytest.mark.asyncio
async def test_reverse_lines_without_text_reports_error(fake_session, capsys):
    """/reverse --lines (no text) must print a usage error."""
    from lilith_cli.extra_commands import run_reverse_command

    await run_reverse_command(fake_session, "--lines")

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "uso" in combined.lower()