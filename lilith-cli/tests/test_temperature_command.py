"""Pruebas del comando /temperature (ver y ajustar la temperatura de sampling)."""

from __future__ import annotations

import asyncio

import pytest

from lilith_cli.temperature_command import run_temperature_command


def _run(coro):
    return asyncio.run(coro)


def test_sin_argumentos_muestra_el_valor_actual(fake_session, capsys):
    """/temperature sin argumentos muestra el valor vigente y el rango."""
    fake_session.config.temperature = 0.7

    _run(run_temperature_command(fake_session, ""))

    out = capsys.readouterr().out
    assert "0.7" in out
    assert "Temperatura" in out


def test_fija_un_valor_valido(fake_session, capsys):
    """Un valor dentro de rango se aplica a la config en caliente."""
    fake_session.config.temperature = 0.7

    _run(run_temperature_command(fake_session, "0.3"))

    assert fake_session.config.temperature == 0.3
    assert "0.3" in capsys.readouterr().out


def test_acepta_coma_decimal(fake_session, capsys):
    """``0,25`` se interpreta como 0.25 — el teclado local usa coma."""
    fake_session.config.temperature = 0.7

    _run(run_temperature_command(fake_session, "0,25"))

    capsys.readouterr()
    assert fake_session.config.temperature == 0.25


def test_reset_vuelve_al_por_defecto(fake_session, capsys):
    """``reset`` restaura 0.7 sin tener que recordar el número."""
    fake_session.config.temperature = 1.9

    _run(run_temperature_command(fake_session, "reset"))

    capsys.readouterr()
    assert fake_session.config.temperature == 0.7


@pytest.mark.parametrize("valor", ["-0.1", "2.5", "10"])
def test_rechaza_valores_fuera_de_rango(fake_session, capsys, valor):
    """Fuera de 0.0–2.0 se rechaza sin tocar la config."""
    fake_session.config.temperature = 0.7

    _run(run_temperature_command(fake_session, valor))

    out = capsys.readouterr().out
    assert "rango" in out.lower()
    assert fake_session.config.temperature == 0.7


def test_rechaza_texto_no_numerico(fake_session, capsys):
    """Un argumento que no es número da error y deja la config intacta."""
    fake_session.config.temperature = 0.7

    _run(run_temperature_command(fake_session, "caliente"))

    out = capsys.readouterr().out
    assert "inválido" in out.lower()
    assert fake_session.config.temperature == 0.7


def test_avisa_cuando_el_valor_es_alto(fake_session, capsys):
    """Por encima de 1.2 se aplica igual, pero con advertencia."""
    fake_session.config.temperature = 0.7

    _run(run_temperature_command(fake_session, "1.5"))

    out = capsys.readouterr().out
    assert fake_session.config.temperature == 1.5
    assert "incoherente" in out


def test_save_persiste_la_config(fake_session, capsys, monkeypatch):
    """``--save`` delega en save_config; sin el flag no se toca el disco."""
    guardados = []
    monkeypatch.setattr(
        "lilith_cli.temperature_command.save_config",
        lambda cfg, path=None: guardados.append(cfg),
    )
    fake_session.config.temperature = 0.7

    _run(run_temperature_command(fake_session, "0.4"))
    assert guardados == []

    _run(run_temperature_command(fake_session, "0.5 --save"))
    capsys.readouterr()
    assert len(guardados) == 1
    assert fake_session.config.temperature == 0.5
