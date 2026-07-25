"""Tests para el comando /calc (calculadora segura con ast)."""

from __future__ import annotations

import asyncio

import pytest


def _run(coro):
    return asyncio.run(coro)


def test_calc_expresion_simple(fake_session, capsys):
    """/calc evalúa aritmética básica respetando precedencia."""
    from lilith_cli.extra_commands import run_calc_command

    _run(run_calc_command(fake_session, "2 + 3 * 4"))

    out = capsys.readouterr().out
    assert "= 14" in out


def test_calc_constantes_y_funciones(fake_session, capsys):
    """/calc acepta constantes (pi) y funciones matemáticas (sqrt, round)."""
    from lilith_cli.extra_commands import run_calc_command

    _run(run_calc_command(fake_session, "round(sqrt(2) ** 2 + pi, 2)"))

    out = capsys.readouterr().out
    assert "5.14" in out


def test_calc_expresion_invalida_muestra_error(fake_session, capsys):
    """/calc rechaza nombres desconocidos sin ejecutar código arbitrario."""
    from lilith_cli.extra_commands import run_calc_command

    _run(run_calc_command(fake_session, "os.system('echo hola')"))

    out = capsys.readouterr().out
    assert "No pude evaluar" in out
    assert "hola" not in out


def test_calc_division_por_cero(fake_session, capsys):
    """/calc reporta la división por cero como error amigable."""
    from lilith_cli.extra_commands import run_calc_command

    _run(run_calc_command(fake_session, "1 / 0"))

    out = capsys.readouterr().out
    assert "No pude evaluar" in out


def test_calc_sin_argumentos_muestra_uso(fake_session, capsys):
    """/calc sin argumentos muestra la ayuda de uso."""
    from lilith_cli.extra_commands import run_calc_command

    _run(run_calc_command(fake_session, ""))

    out = capsys.readouterr().out
    assert "Uso: /calc" in out


def test_calc_eval_unidad():
    """calc_eval evalúa expresiones válidas y rechaza construcciones peligrosas."""
    from lilith_cli.extra_commands import calc_eval

    assert calc_eval("10 % 3") == 1
    assert calc_eval("2 ** 10") == 1024
    assert calc_eval("max(3, 7, 5)") == 7
    with pytest.raises(ValueError):
        calc_eval("__import__('os')")
    with pytest.raises(ValueError):
        calc_eval("variable_inexistente + 1")
