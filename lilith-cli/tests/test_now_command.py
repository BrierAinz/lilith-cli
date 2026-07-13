"""Tests for /now slash command."""

from __future__ import annotations

import asyncio
import re


def _run(coro):
    return asyncio.run(coro)


def test_now_default_shows_local(fake_session, capsys):
    """/now with no args shows local time."""
    from lilith_cli.extra_commands import run_now_command

    _run(run_now_command(fake_session, ""))

    out = capsys.readouterr().out
    assert "Local:" in out
    # Match a datetime pattern like 2026-07-11 03:15:42
    assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", out)


def test_now_utc_only(fake_session, capsys):
    """/now --utc shows only UTC."""
    from lilith_cli.extra_commands import run_now_command

    _run(run_now_command(fake_session, "--utc"))

    out = capsys.readouterr().out
    assert "UTC:" in out
    assert "Local:" not in out


def test_now_unix_only(fake_session, capsys):
    """/now --unix shows only unix timestamp."""
    from lilith_cli.extra_commands import run_now_command

    _run(run_now_command(fake_session, "--unix"))

    out = capsys.readouterr().out
    assert "Unix:" in out
    # Unix timestamp is ~10 digits (around 1.7e9 to 2.0e9)
    assert re.search(r"\b1[6-9]\d{8}\b|\b2\d{9}\b", out)


def test_now_combined_flags(fake_session, capsys):
    """/now --utc --unix shows both UTC and unix."""
    from lilith_cli.extra_commands import run_now_command

    _run(run_now_command(fake_session, "--utc --unix"))

    out = capsys.readouterr().out
    assert "UTC:" in out
    assert "Unix:" in out
    assert "Local:" not in out