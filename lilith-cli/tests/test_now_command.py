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


def test_now_iso_flag(fake_session, capsys):
    """/now --iso shows ISO 8601 timestamp ending in 'Z'."""
    from lilith_cli.extra_commands import run_now_command

    _run(run_now_command(fake_session, "--iso"))

    out = capsys.readouterr().out
    assert "ISO:" in out
    assert "Local:" not in out
    # ISO 8601 UTC: 2026-07-11T03:15:42.123456+00:00 o con Z.
    assert re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}.*Z", out)


def test_now_rfc_flag(fake_session, capsys):
    """/now --rfc shows RFC 2822 timestamp like 'Fri, 11 Jul 2026 03:15:42 +0000'."""
    from lilith_cli.extra_commands import run_now_command

    _run(run_now_command(fake_session, "--rfc"))

    out = capsys.readouterr().out
    assert "RFC:" in out
    assert "Local:" not in out
    # RFC 2822 empieza con día de semana abreviado de 3 letras.
    assert re.search(r"[A-Z][a-z]{2}, \d{2} [A-Z][a-z]{2} \d{4} \d{2}:\d{2}:\d{2} [+-]\d{4}", out)


def test_now_iso_with_utc(fake_session, capsys):
    """/now --utc --iso shows both UTC and ISO 8601 outputs."""
    from lilith_cli.extra_commands import run_now_command

    _run(run_now_command(fake_session, "--utc --iso"))

    out = capsys.readouterr().out
    assert "UTC:" in out
    assert "ISO:" in out
    assert "Local:" not in out


def test_now_explicit_local_with_iso(fake_session, capsys):
    """/now --local --iso shows both Local and ISO 8601 outputs."""
    from lilith_cli.extra_commands import run_now_command

    _run(run_now_command(fake_session, "--local --iso"))

    out = capsys.readouterr().out
    assert "Local:" in out
    assert "ISO:" in out
    # Unix y RFC no se pidieron.
    assert "Unix:" not in out
    assert "RFC:" not in out