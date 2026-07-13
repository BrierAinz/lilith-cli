"""Tests for lilith_cli.extra_commands.run_log_command (/log slash command).

Cubre los subcomandos: default (línea de tiempo + conteos), ``stats``
(sólo conteos), ``help`` (uso en español), ``clear`` (cuando no hay
archivo y cuando sí), y ``path`` (ruta absoluta del log).

Sigue el patrón de test_compare_command.py: parchea
``lilith_cli.extra_commands.console.print`` y ``render_error`` para
capturar la salida, y usa ``monkeypatch`` + ``tmp_path`` para redirigir
``_LOG_FILE`` sin contaminar el config real del usuario.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest


def _run(coro):
    """Helper para ejecutar coroutines en tests síncronos (sin pytest-asyncio)."""
    return asyncio.run(coro)


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def isolated_log_file(tmp_path, monkeypatch):
    """Redirige ``_LOG_FILE`` a un archivo bajo ``tmp_path``.

    Importante: se hace ``monkeypatch.setattr`` sobre el módulo ya
    importado para que cualquier llamada subsiguiente a la constante
    vea la ruta temporal. Se asegura también de que el archivo no
    exista al inicio (cada test parte de cero).
    """
    from lilith_cli import extra_commands as ec

    fake_file = tmp_path / "session.log"
    # Si un test anterior lo creó, lo borramos para garantizar aislamiento.
    if fake_file.exists():
        fake_file.unlink()
    monkeypatch.setattr(ec, "_LOG_FILE", fake_file)
    return fake_file


def _capture_prints():
    """Devuelve (prints_list, errors_list, patched_prints, patched_errors)."""
    prints: list[str] = []
    errors: list[str] = []

    def _capture_print(*args, **kwargs):
        for a in args:
            if a is None:
                continue
            # Renderable de Rich: usar str() como fallback estable.
            prints.append(str(a))
        for v in kwargs.values():
            if v is not None:
                prints.append(str(v))

    def _capture_error(*args, **kwargs):
        for a in args:
            if a is None:
                continue
            errors.append(str(a))
        for v in kwargs.values():
            if v is not None:
                errors.append(str(v))

    return prints, errors, _capture_print, _capture_error


# ── Tests ────────────────────────────────────────────────────────────


def test_log_no_args_empty_history(fake_session, capsys, isolated_log_file):
    """/log sin argumentos y sin historial imprime la cabecera con 0 turnos."""
    from lilith_cli.extra_commands import run_log_command

    # fake_session tiene history=[] por defecto en conftest.
    fake_session.history = []
    fake_session._tool_call_history = []

    prints, errors, cap_print, cap_error = _capture_prints()
    with patch("lilith_cli.extra_commands.console.print", side_effect=cap_print):
        with patch("lilith_cli.extra_commands.render_error", side_effect=cap_error):
            _run(run_log_command(fake_session, ""))

    rendered = "\n".join(prints)
    assert "Sesión" in rendered or "sesión" in rendered.lower(), (
        f"Esperaba cabecera 'Sesión' en la salida. Obtuve:\n{rendered[:600]}"
    )
    # Turnos totales explícitamente en cero.
    assert "Turnos totales" in rendered, rendered
    assert "0" in rendered
    # No debe llamar a render_error.
    assert errors == [], f"No esperaba errores: {errors}"
    # Mensaje amable cuando no hay turnos en la línea de tiempo.
    assert "línea de tiempo" in rendered.lower() or "no hay turnos" in rendered.lower()


def test_log_with_n_shows_last_n(fake_session, capsys, isolated_log_file):
    """/log 5 con 10 mensajes muestra sólo los últimos 5 en la línea de tiempo."""
    from lilith_cli.extra_commands import run_log_command

    fake_session.history = [
        {"role": "user", "content": f"mensaje-{i}", "timestamp": f"2026-07-11T10:00:{i:02d}"}
        for i in range(10)
    ]
    fake_session._tool_call_history = []

    prints, errors, cap_print, cap_error = _capture_prints()
    with patch("lilith_cli.extra_commands.console.print", side_effect=cap_print):
        with patch("lilith_cli.extra_commands.render_error", side_effect=cap_error):
            _run(run_log_command(fake_session, "5"))

    rendered = "\n".join(prints)
    # La cabecera debe decir "últimos 5".
    assert "últimos 5" in rendered, f"Esperaba 'últimos 5' en:\n{rendered[:800]}"
    # Los últimos 5 (índices 5..9) deben estar, los primeros 5 no.
    assert "mensaje-9" in rendered
    assert "mensaje-5" in rendered
    assert "mensaje-0" not in rendered, (
        "mensaje-0 no debería aparecer (queda fuera de los últimos 5)"
    )
    # Turnos totales sigue mostrando el total real (10).
    assert "10" in rendered
    # Sin errores.
    assert errors == [], errors


def test_log_stats_spanish_keywords(fake_session, capsys, isolated_log_file):
    """/log stats imprime el panel agregado con palabras clave en español."""
    from lilith_cli.extra_commands import run_log_command

    fake_session.history = [
        {"role": "user", "content": "hola"},
        {"role": "assistant", "content": "buenas"},
        {"role": "user", "content": "otra pregunta"},
    ]
    fake_session._tool_call_history = [
        {"name": "file_read", "arguments": {"path": "a.py"}},
        {"name": "file_read", "arguments": {"path": "b.py"}},
        {"name": "shell", "arguments": {"command": "ls"}},
    ]

    prints, errors, cap_print, cap_error = _capture_prints()
    with patch("lilith_cli.extra_commands.console.print", side_effect=cap_print):
        with patch("lilith_cli.extra_commands.render_error", side_effect=cap_error):
            _run(run_log_command(fake_session, "stats"))

    rendered = "\n".join(prints)
    # Palabras clave en español (case-insensitive).
    lowered = rendered.lower()
    assert "turnos" in lowered, rendered
    assert "llamadas" in lowered, rendered
    assert "usuario" in lowered, rendered
    assert "asistente" in lowered, rendered
    # Desglose por nombre de herramienta.
    assert "file_read" in rendered
    assert "shell" in rendered
    # NO debe imprimir la línea de tiempo en modo stats.
    assert "Línea de tiempo" not in rendered and "línea de tiempo" not in lowered, (
        "/log stats no debería imprimir línea de tiempo"
    )
    assert errors == [], errors


def test_log_help_contains_command_name(fake_session, capsys, isolated_log_file):
    """/log help imprime la ayuda y menciona /log."""
    from lilith_cli.extra_commands import run_log_command

    prints, errors, cap_print, cap_error = _capture_prints()
    with patch("lilith_cli.extra_commands.console.print", side_effect=cap_print):
        with patch("lilith_cli.extra_commands.render_error", side_effect=cap_error):
            _run(run_log_command(fake_session, "help"))

    rendered = "\n".join(prints)
    assert "/log" in rendered
    # La ayuda debe listar los subcomandos principales.
    assert "stats" in rendered
    assert "clear" in rendered
    assert "path" in rendered
    assert "Uso:" in rendered or "Uso" in rendered
    assert errors == [], errors


def test_log_clear_when_no_file(fake_session, capsys, isolated_log_file):
    """/log clear sin archivo previo imprime mensaje amable y no falla."""
    from lilith_cli.extra_commands import run_log_command

    # Garantizar que el archivo no existe (isolated_log_file ya lo asegura).
    assert not isolated_log_file.exists()

    prints, errors, cap_print, cap_error = _capture_prints()
    with patch("lilith_cli.extra_commands.console.print", side_effect=cap_print):
        with patch("lilith_cli.extra_commands.render_error", side_effect=cap_error):
            _run(run_log_command(fake_session, "clear"))

    rendered = "\n".join(prints)
    # Mensaje amable: el archivo no se creó, no hubo error.
    assert "No hay" in rendered or "nada que" in rendered.lower() or "no existe" in rendered.lower(), (
        f"Esperaba mensaje amable cuando no hay archivo. Obtuve:\n{rendered}"
    )
    # El archivo sigue sin existir.
    assert not isolated_log_file.exists()
    # No se llamó a render_error.
    assert errors == [], f"No esperaba errores: {errors}"


def test_log_path_prints_path(fake_session, capsys, isolated_log_file):
    """/log path imprime la ruta absoluta del archivo de log."""
    from lilith_cli.extra_commands import run_log_command

    prints, errors, cap_print, cap_error = _capture_prints()
    with patch("lilith_cli.extra_commands.console.print", side_effect=cap_print):
        with patch("lilith_cli.extra_commands.render_error", side_effect=cap_error):
            _run(run_log_command(fake_session, "path"))

    rendered = "\n".join(prints)
    # El nombre del archivo debe aparecer en la salida.
    assert "session.log" in rendered, (
        f"Esperaba 'session.log' en la salida. Obtuve:\n{rendered}"
    )
    # Y la ruta completa del fake debe aparecer.
    assert str(isolated_log_file) in rendered, (
        f"Esperaba la ruta completa {isolated_log_file} en:\n{rendered}"
    )
    assert errors == [], errors


def test_log_clear_when_file_exists(fake_session, capsys, isolated_log_file):
    """/log clear con archivo existente lo borra y reporta éxito."""
    from lilith_cli.extra_commands import run_log_command

    # Pre-poblar el archivo.
    isolated_log_file.write_text("entrada previa\n", encoding="utf-8")
    assert isolated_log_file.exists()

    prints, errors, cap_print, cap_error = _capture_prints()
    with patch("lilith_cli.extra_commands.console.print", side_effect=cap_print):
        with patch("lilith_cli.extra_commands.render_error", side_effect=cap_error):
            _run(run_log_command(fake_session, "clear"))

    rendered = "\n".join(prints)
    assert "borrado" in rendered.lower(), rendered
    assert not isolated_log_file.exists(), (
        "El archivo debería haberse borrado después de /log clear"
    )
    assert errors == [], errors


def test_log_unknown_subcommand(fake_session, capsys, isolated_log_file):
    """/log con subcomando desconocido invoca render_error."""
    from lilith_cli.extra_commands import run_log_command

    prints, errors, cap_print, cap_error = _capture_prints()
    with patch("lilith_cli.extra_commands.console.print", side_effect=cap_print):
        with patch("lilith_cli.extra_commands.render_error", side_effect=cap_error):
            _run(run_log_command(fake_session, "no-existe"))

    assert errors != [], "Esperaba al menos un error para subcomando desconocido"
    joined = "\n".join(errors)
    assert "no-existe" in joined or "desconocido" in joined.lower(), (
        f"Esperaba mención del subcomando desconocido. Errores: {joined}"
    )