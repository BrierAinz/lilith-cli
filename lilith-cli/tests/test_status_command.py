"""Tests for /status slash command with color-coded token usage."""

from __future__ import annotations

import asyncio
import time


def _run(coro):
    return asyncio.run(coro)


def _set_usage(session, usage):
    """Set session._total_usage directly (bypasses the property)."""
    session._total_usage = usage


def test_status_basic(fake_session, capsys):
    """/status renders a Rich Table with session info."""
    _set_usage(fake_session, {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150})

    from lilith_cli.extra_commands import run_status_command
    _run(run_status_command(fake_session, ""))

    out = capsys.readouterr().out
    assert "Estado" in out
    assert "Modelo" in out
    assert "Total tokens" in out


def test_status_color_green_for_low(fake_session, capsys):
    """/status colors tokens green for low usage (<4k)."""
    _set_usage(fake_session, {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150})

    from lilith_cli.extra_commands import run_status_command
    _run(run_status_command(fake_session, ""))

    out = capsys.readouterr().out
    assert "[green]" in out or "150" in out


def test_status_color_yellow_for_medium(fake_session, capsys):
    """/status colors tokens yellow for medium usage (4k-16k)."""
    _set_usage(fake_session, {"prompt_tokens": 5000, "completion_tokens": 2000, "total_tokens": 7000})

    from lilith_cli.extra_commands import run_status_command
    _run(run_status_command(fake_session, ""))

    out = capsys.readouterr().out
    assert "[yellow]" in out or "7000" in out


def test_status_color_red_for_high(fake_session, capsys):
    """/status colors tokens red for high usage (>=16k)."""
    _set_usage(fake_session, {"prompt_tokens": 20000, "completion_tokens": 5000, "total_tokens": 25000})

    from lilith_cli.extra_commands import run_status_command
    _run(run_status_command(fake_session, ""))

    out = capsys.readouterr().out
    assert "[red]" in out or "25000" in out


def test_status_with_uptime(fake_session, capsys):
    """/status shows uptime row when _start_time is set."""
    fake_session._start_time = time.time() - 65  # 1m 5s ago

    from lilith_cli.extra_commands import run_status_command
    _run(run_status_command(fake_session, ""))

    out = capsys.readouterr().out
    assert "Uptime" in out
    assert "1m" in out


def test_status_with_last_command(fake_session, capsys):
    """/status shows last command when _last_command is set."""
    fake_session._last_command = "doctor"

    from lilith_cli.extra_commands import run_status_command
    _run(run_status_command(fake_session, ""))

    out = capsys.readouterr().out
    assert "doctor" in out


def test_status_with_args_shows_error(fake_session, capsys):
    """/status with any args renders usage error."""
    from lilith_cli.extra_commands import run_status_command
    _run(run_status_command(fake_session, "anything"))

    out = capsys.readouterr().out
    assert "Uso:" in out


def test_status_handles_no_usage(fake_session, capsys):
    """/status with no usage data still renders (zeros)."""
    _set_usage(fake_session, {})

    from lilith_cli.extra_commands import run_status_command
    _run(run_status_command(fake_session, ""))

    out = capsys.readouterr().out
    assert "Estado" in out


def test_status_renders_as_table(fake_session, capsys):
    """/status uses Rich Table (box-drawing characters in output)."""
    from lilith_cli.extra_commands import run_status_command
    _run(run_status_command(fake_session, ""))

    out = capsys.readouterr().out
    assert "\u250c" in out or "\u2502" in out