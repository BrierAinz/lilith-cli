"""Tests for /epoch slash command (conversor Unix timestamp ↔ fecha)."""

from __future__ import annotations

import asyncio
import re


def _run(coro):
    return asyncio.run(coro)


def test_epoch_default_shows_current(fake_session, capsys):
    """/epoch sin argumentos muestra el timestamp actual en Unix, UTC y local."""
    from lilith_cli.extra_commands import run_epoch_command

    _run(run_epoch_command(fake_session, ""))

    out = capsys.readouterr().out
    assert "Unix:" in out
    assert "UTC:" in out
    assert "Local:" in out
    assert re.search(r"\b1[6-9]\d{8}\b|\b2\d{9}\b", out)


def test_epoch_timestamp_to_date(fake_session, capsys):
    """/epoch 1700000000 convierte el timestamp a fechas UTC y local."""
    from lilith_cli.extra_commands import run_epoch_command

    _run(run_epoch_command(fake_session, "1700000000"))

    out = capsys.readouterr().out
    assert "Unix:" in out
    assert "1700000000" in out
    # 1700000000 = 2023-11-14 22:13:20 UTC
    assert "2023-11-14 22:13:20" in out


def test_epoch_date_to_timestamp_utc(fake_session, capsys):
    """/epoch 2024-01-01 --utc produce el timestamp exacto de esa fecha en UTC."""
    from lilith_cli.extra_commands import run_epoch_command

    _run(run_epoch_command(fake_session, "2024-01-01 --utc"))

    out = capsys.readouterr().out
    # 2024-01-01 00:00:00 UTC = 1704067200
    assert "1704067200" in out
    assert "UTC" in out


def test_epoch_invalid_date_shows_error(fake_session, capsys):
    """/epoch con texto no parseable muestra error amigable."""
    from lilith_cli.extra_commands import run_epoch_command

    _run(run_epoch_command(fake_session, "no-es-una-fecha"))

    out = capsys.readouterr().out
    assert "Fecha no reconocida" in out
