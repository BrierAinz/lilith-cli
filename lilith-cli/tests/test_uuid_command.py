"""Tests for /uuid slash command."""

from __future__ import annotations

import asyncio
import re


def _run(coro):
    return asyncio.run(coro)


UUID_V4_PATTERN = r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[0-9a-f]{4}-[0-9a-f]{12}"
UUID_V1_PATTERN = r"[0-9a-f]{8}-[0-9a-f]{4}-1[0-9a-f]{3}-[0-9a-f]{4}-[0-9a-f]{12}"


def test_uuid_default_generates_one_v4(fake_session, capsys):
    """/uuid (no args) generates a single v4 UUID."""
    from lilith_cli.extra_commands import run_uuid_command

    _run(run_uuid_command(fake_session, ""))

    out = capsys.readouterr().out
    assert re.search(UUID_V4_PATTERN, out)
    assert "v4" in out


def test_uuid_count(fake_session, capsys):
    """/uuid 3 generates 3 UUIDs."""
    from lilith_cli.extra_commands import run_uuid_command

    _run(run_uuid_command(fake_session, "3"))

    out = capsys.readouterr().out
    matches = re.findall(UUID_V4_PATTERN, out)
    assert len(matches) == 3


def test_uuid_v1_flag(fake_session, capsys):
    """/uuid --v1 generates a v1 UUID."""
    from lilith_cli.extra_commands import run_uuid_command

    _run(run_uuid_command(fake_session, "--v1"))

    out = capsys.readouterr().out
    assert re.search(UUID_V1_PATTERN, out)
    assert "v1" in out


def test_uuid_count_capped(fake_session, capsys):
    """/uuid with huge count is capped at 50."""
    from lilith_cli.extra_commands import run_uuid_command

    _run(run_uuid_command(fake_session, "999"))

    out = capsys.readouterr().out
    matches = re.findall(UUID_V4_PATTERN, out)
    assert len(matches) == 50


def test_uuid_multiple_unique(fake_session, capsys):
    """/uuid 5 returns 5 unique UUIDs."""
    from lilith_cli.extra_commands import run_uuid_command

    _run(run_uuid_command(fake_session, "5"))

    out = capsys.readouterr().out
    matches = re.findall(UUID_V4_PATTERN, out)
    assert len(matches) == len(set(matches))  # all unique