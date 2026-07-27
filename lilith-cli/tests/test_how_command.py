"""Pruebas del comando /how (ayuda detallada de un comando de barra)."""

from __future__ import annotations

import asyncio


def _run(coro):
    return asyncio.run(coro)


def test_how_sin_argumento_muestra_uso(fake_session, capsys):
    """/how sin argumento explica cómo se usa en lugar de fallar."""
    from lilith_cli.how_command import run_how_command

    _run(run_how_command(fake_session, ""))

    out = capsys.readouterr().out
    assert "/how <comando>" in out


def test_how_describe_comando_conocido(fake_session, capsys):
    """/how help resuelve el comando y lista su nombre y aliases."""
    from lilith_cli.how_command import run_how_command

    _run(run_how_command(fake_session, "help"))

    out = capsys.readouterr().out
    assert "/help" in out
    assert "Aliases" in out


def test_how_acepta_la_barra_inicial(fake_session, capsys):
    """/how /help se comporta igual que /how help."""
    from lilith_cli.how_command import run_how_command

    _run(run_how_command(fake_session, "/help"))

    out = capsys.readouterr().out
    assert "/help" in out


def test_how_reporta_comando_desconocido(fake_session, capsys):
    """Un nombre inexistente produce un error claro, no una traza."""
    from lilith_cli.how_command import run_how_command

    _run(run_how_command(fake_session, "comando-que-no-existe"))

    out = capsys.readouterr().out
    assert "desconocido" in out.lower()
