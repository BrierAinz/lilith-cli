"""Tests for /timer slash command."""

from __future__ import annotations

import asyncio
import re

from lilith_cli.slash_commands import extras as extras_cmds
from lilith_cli.slash_router import route, slash_commands


def _run(coro):
    return asyncio.run(coro)


def test_timer_start_then_stop_reports_elapsed(fake_session, capsys):
    """/timer start arranca, /timer stop devuelve mm:ss.mmm y limpia estado."""
    from lilith_cli.extra_commands import run_timer_command

    try:
        # Estado limpio al inicio.
        extras_cmds._TIMER_STATE = None
        _run(run_timer_command(fake_session, "start"))
        out = capsys.readouterr().out
        assert "Cronómetro iniciado" in out
        assert extras_cmds._TIMER_STATE is not None
        assert "started_at" in extras_cmds._TIMER_STATE
        assert extras_cmds._TIMER_STATE["label"] is None

        _run(run_timer_command(fake_session, "stop"))
        out = capsys.readouterr().out
        # Debe mostrar el tiempo total con formato mm:ss.mmm (sin horas porque <1h).
        assert "Tiempo total" in out
        assert re.search(r"\b\d{2}:\d{2}\.\d{3}\b", out)
        # El estado debe quedar limpio tras stop.
        assert extras_cmds._TIMER_STATE is None
    finally:
        extras_cmds._TIMER_STATE = None


def test_timer_start_with_label_keeps_label(fake_session, capsys):
    """/timer start <etiqueta> guarda la etiqueta y la muestra en stop."""
    from lilith_cli.extra_commands import run_timer_command

    try:
        extras_cmds._TIMER_STATE = None
        _run(run_timer_command(fake_session, "start deploy prod"))
        assert extras_cmds._TIMER_STATE is not None
        assert extras_cmds._TIMER_STATE["label"] == "deploy prod"

        _run(run_timer_command(fake_session, "stop"))
        out = capsys.readouterr().out
        assert "Tiempo total" in out
        assert "deploy prod" in out
    finally:
        extras_cmds._TIMER_STATE = None


def test_timer_stop_without_start_warns(fake_session, capsys):
    """/timer stop sin cronómetro activo imprime error y no rompe."""
    from lilith_cli.extra_commands import run_timer_command

    try:
        extras_cmds._TIMER_STATE = None
        _run(run_timer_command(fake_session, "stop"))
        out = capsys.readouterr().out
        # El error puede ir por stderr (render_error) o por stdout; pedimos algo
        # distintivo en alguna de las dos corrientes.
        err = capsys.readouterr().err
        combined = out + err
        assert "No hay cronómetro activo" in combined
        assert extras_cmds._TIMER_STATE is None
    finally:
        extras_cmds._TIMER_STATE = None


def test_timer_status_when_idle(fake_session, capsys):
    """/timer status sin timer activo avisa al usuario."""
    from lilith_cli.extra_commands import run_timer_command

    try:
        extras_cmds._TIMER_STATE = None
        _run(run_timer_command(fake_session, "status"))
        out = capsys.readouterr().out
        assert "No hay cronómetro activo" in out
    finally:
        extras_cmds._TIMER_STATE = None


def test_timer_format_elapsed_helper():
    """_format_elapsed maneja correctamente horas, minutos y segundos."""
    from lilith_cli.extra_commands import _format_elapsed

    assert _format_elapsed(0) == "00:00.000"
    assert _format_elapsed(12.345) == "00:12.345"
    assert _format_elapsed(72.5) == "01:12.500"
    assert _format_elapsed(3661.25) == "01:01:01.250"
    # No debe explotar con valores negativos.
    assert _format_elapsed(-5) == "00:00.000"


def test_timer_count_completes(fake_session, capsys):
    """/timer count 0.1 termina mostrando 'Tiempo cumplido'."""
    from lilith_cli.extra_commands import run_timer_command

    try:
        extras_cmds._TIMER_STATE = None
        _run(run_timer_command(fake_session, "count 0.1"))
        out = capsys.readouterr().out
        assert "Cuenta atrás" in out
        assert "Tiempo cumplido" in out
    finally:
        extras_cmds._TIMER_STATE = None


def test_timer_count_rejects_zero_or_negative(fake_session, capsys):
    """/timer count 0 y count -1 imprimen error de uso."""
    from lilith_cli.extra_commands import run_timer_command

    try:
        extras_cmds._TIMER_STATE = None
        _run(run_timer_command(fake_session, "count 0"))
        _run(run_timer_command(fake_session, "count -2"))
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "debe ser > 0" in combined
    finally:
        extras_cmds._TIMER_STATE = None


def test_timer_count_rejects_non_numeric(fake_session, capsys):
    """/timer count abc imprime error de parsing."""
    from lilith_cli.extra_commands import run_timer_command

    try:
        extras_cmds._TIMER_STATE = None
        _run(run_timer_command(fake_session, "count abc"))
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "Duración inválida" in combined
    finally:
        extras_cmds._TIMER_STATE = None


def test_timer_help_shows_subcommands(fake_session, capsys):
    """/timer sin argumentos muestra la ayuda con los subcomandos disponibles."""
    from lilith_cli.extra_commands import run_timer_command

    try:
        extras_cmds._TIMER_STATE = None
        _run(run_timer_command(fake_session, ""))
        out = capsys.readouterr().out
        assert "/timer" in out
        assert "start" in out
        assert "stop" in out
        assert "count" in out
    finally:
        extras_cmds._TIMER_STATE = None


def test_timer_is_in_slash_commands_list():
    """/timer debe aparecer en el autocompletado."""
    assert "/timer" in slash_commands()

def test_timer_is_in_dispatcher():
    """El router debe despachar /timer a su handler."""
    from lilith_cli.extra_commands import run_timer_command

    assert route("timer").handler is run_timer_command
