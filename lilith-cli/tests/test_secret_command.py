"""Tests for /secret slash command (secure secret management)."""

from __future__ import annotations

import asyncio


def _run(coro):
    return asyncio.run(coro)


def test_secret_list_empty(fake_session, capsys):
    """/secret on a fresh session shows the empty-state message."""
    from lilith_cli.extra_commands import run_secret_command

    _run(run_secret_command(fake_session, ""))

    out = capsys.readouterr().out
    assert "No hay secretos" in out or "vacío" in out.lower()


def test_secret_list_with_secrets(fake_session, capsys):
    """/secret list shows configured secret names with redacted values."""
    from lilith_cli.extra_commands import run_secret_command

    fake_session._secrets = {"API_KEY": "abc123", "TOKEN": "xyz789"}

    _run(run_secret_command(fake_session, "list"))

    out = capsys.readouterr().out
    assert "API_KEY" in out
    assert "TOKEN" in out
    # Values must NOT leak in the list output
    assert "abc123" not in out
    assert "xyz789" not in out


def test_secret_set_and_get(fake_session, capsys):
    """/secret set stores a value; /secret get retrieves it."""
    from lilith_cli.extra_commands import run_secret_command

    _run(run_secret_command(fake_session, "set API_KEY super-secret-value"))
    _run(run_secret_command(fake_session, "get API_KEY"))

    out = capsys.readouterr().out
    assert "API_KEY" in out
    assert "super-secret-value" in out
    # Stored on the session
    assert fake_session._secrets.get("API_KEY") == "super-secret-value"


def test_secret_set_missing_value(fake_session, capsys):
    """/secret set without a value shows a usage error and does not store anything."""
    from lilith_cli.extra_commands import run_secret_command

    _run(run_secret_command(fake_session, "set API_KEY"))

    out = capsys.readouterr().out
    assert "Uso:" in out or "uso:" in out.lower()
    assert not getattr(fake_session, "_secrets", {})


def test_secret_get_missing(fake_session, capsys):
    """/secret get on an unknown name shows an error."""
    from lilith_cli.extra_commands import run_secret_command

    fake_session._secrets = {}

    _run(run_secret_command(fake_session, "get NO_SUCH"))

    out = capsys.readouterr().out
    assert "no encontrado" in out.lower() or "not found" in out.lower()


def test_secret_clear(fake_session, capsys):
    """/secret clear wipes all stored secrets."""
    from lilith_cli.extra_commands import run_secret_command

    fake_session._secrets = {"A": "1", "B": "2"}

    _run(run_secret_command(fake_session, "clear"))

    out = capsys.readouterr().out
    assert "eliminados" in out.lower() or "clear" in out.lower()
    assert fake_session._secrets == {}


def test_secret_unknown_subcommand(fake_session, capsys):
    """/secret <garbage> prints usage without mutating state."""
    from lilith_cli.extra_commands import run_secret_command

    fake_session._secrets = {}

    _run(run_secret_command(fake_session, "wiggle"))

    out = capsys.readouterr().out
    assert "Uso:" in out or "uso:" in out.lower()
    assert fake_session._secrets == {}