"""Tests for /now slash command."""

from __future__ import annotations

import asyncio
import json
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


def test_now_json_survives_a_narrow_console(fake_session, capsys, monkeypatch):
    """El JSON sigue parseando aunque la consola sea mas angosta que la salida.

    Rich envuelve al ancho de la consola, y al hacerlo mete un ``\\n``
    DENTRO del JSON: la salida deja de parsear con "Invalid control
    character". Los otros tests de --json no lo detectaban porque la
    consola del modulo se construye al importar y en la maquina de
    desarrollo suele quedar mas ancha que el payload; en CI, con 80
    columnas, los dos reventaron.

    Fijar un ancho chico hace la regresion determinista en cualquier
    maquina, sin depender del tamano de terminal del runner.
    """
    from lilith_cli.extra_commands import run_now_command
    from lilith_cli.render import console

    monkeypatch.setattr(console, "width", 40)

    _run(run_now_command(fake_session, "--json"))

    out = capsys.readouterr().out.strip()
    assert "\n" not in out, "Rich envolvio el JSON en varias lineas"
    payload = json.loads(out)
    assert set(payload.keys()) == {"unix", "utc", "iso", "rfc", "local"}


def test_now_json_emits_machine_readable(fake_session, capsys):
    """/now --json emite un único objeto JSON con las 5 formas de timestamp."""
    from lilith_cli.extra_commands import run_now_command

    _run(run_now_command(fake_session, "--json"))

    out = capsys.readouterr().out.strip()
    # Sin prefijos Rich como "Local:" ni "[info]" — solo JSON limpio.
    assert "Local:" not in out
    assert "UTC:" not in out
    assert "[" not in out  # Rich markup ausente
    payload = json.loads(out)
    assert set(payload.keys()) == {"unix", "utc", "iso", "rfc", "local"}
    # unix debe ser un entero positivo en el rango esperado (>= 2025).
    assert isinstance(payload["unix"], int)
    assert payload["unix"] > 1_700_000_000
    # iso termina en Z porque construimos desde un datetime aware en UTC.
    assert payload["iso"].endswith("Z")
    # utc e iso deben coincidir en su parte de fecha/hora (mod segundos).
    assert payload["utc"][:10] == payload["iso"][:10]
    # rfc respeta el patrón RFC 2822 canónico.
    assert re.search(
        r"[A-Z][a-z]{2}, \d{2} [A-Z][a-z]{2} \d{4} \d{2}:\d{2}:\d{2} [+-]\d{4}",
        payload["rfc"],
    )


def test_now_json_with_extra_flags(fake_session, capsys):
    """/now --json --utc --unix sigue emitiendo JSON completo (no mezcla Rich)."""
    from lilith_cli.extra_commands import run_now_command

    _run(run_now_command(fake_session, "--json --utc --unix"))

    out = capsys.readouterr().out.strip()
    payload = json.loads(out)
    # Cuando --json esta activo, ninguna etiqueta Rich debe contaminar la salida.
    assert "Local:" not in out
    assert "UTC:" not in out
    assert "Unix:" not in out
    # Pero el payload completo sigue presente.
    assert "unix" in payload and "local" in payload and "utc" in payload