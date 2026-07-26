"""Pruebas del comando de barra /pin."""

from __future__ import annotations

import pytest

from lilith_cli.extra_commands import run_pin_command


@pytest.mark.asyncio
async def test_pin_fija_mensaje_por_indice_y_lo_lista(fake_session, capsys):
    """El índice conserva la convención existente: 1 es el mensaje más reciente."""
    fake_session.history = [
        {"role": "user", "content": "primero"},
        {"role": "assistant", "content": "segundo"},
        {"role": "user", "content": "tercero"},
    ]

    await run_pin_command(fake_session, "1")
    await run_pin_command(fake_session, "list")

    salida = capsys.readouterr().out
    assert len(fake_session._pinned_messages) == 1
    assert fake_session._pinned_messages[0]["content"] == "tercero"
    assert "Mensaje fijado en el índice 1: tercero" in salida
    assert "1." in salida and "tercero" in salida


@pytest.mark.asyncio
async def test_pin_sin_argumentos_muestra_ayuda_y_clear_limpia(fake_session, capsys):
    """Sin argumentos informa el estado sin fijar; clear vacía la sesión."""
    fake_session.history = [{"role": "user", "content": "único"}]

    await run_pin_command(fake_session, "")
    ayuda = capsys.readouterr().out
    assert "Uso de /pin" in ayuda
    assert "/pin <n>" in ayuda
    assert "No hay mensajes" in ayuda
    assert fake_session._pinned_messages == []

    await run_pin_command(fake_session, "1")
    capsys.readouterr()
    await run_pin_command(fake_session, "ClEaR")

    salida = capsys.readouterr().out
    assert fake_session._pinned_messages == []
    assert "Mensajes fijados eliminados: 1" in salida


@pytest.mark.asyncio
async def test_pin_indice_fuera_de_rango_y_no_numerico_reportan_error(fake_session, capsys):
    """Los índices fuera de rango y los argumentos no numéricos se reportan sin fijar nada."""
    fake_session.history = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
    ]

    # Índice fuera de rango: no debe añadir nada a _pinned_messages.
    await run_pin_command(fake_session, "99")
    out_rango = capsys.readouterr().out
    assert fake_session._pinned_messages == []
    assert "fuera de rango" in out_rango

    # Argumento no numérico: tampoco debe modificar el estado.
    await run_pin_command(fake_session, "abc")
    out_nan = capsys.readouterr().out
    assert fake_session._pinned_messages == []
    assert "Uso: /pin" in out_nan


@pytest.mark.asyncio
async def test_pin_sobrevive_al_reinicio_del_repl(fake_session, tmp_path, monkeypatch):
    """Los fijados se guardan en disco, no solo en memoria de la sesión.

    La reescritura de /pin los había dejado solo en el espejo en memoria: se
    perdían al cerrar el REPL, que es justo lo contrario de lo que se espera
    de "fijar" un mensaje importante.
    """
    from lilith_cli import extra_commands

    monkeypatch.setattr(extra_commands, "_pin_storage_path",
                        lambda: tmp_path / "pins.json")
    fake_session.session_id = "sesion-de-prueba"
    fake_session.history = [{"role": "assistant", "content": "no me olvides"}]

    await run_pin_command(fake_session, "1")

    assert (tmp_path / "pins.json").exists(), "el fijado no llegó al disco"

    # Sesión nueva (mismo id): el REPL arrancó de cero, sin espejo en memoria.
    class SesionNueva:
        session_id = "sesion-de-prueba"
        history: list = []

    recuperados = extra_commands._load_pin_entries(SesionNueva())
    assert [e["content"] for e in recuperados] == ["no me olvides"]


@pytest.mark.asyncio
async def test_pin_no_revienta_si_el_disco_falla(fake_session, monkeypatch):
    """El fijado vale en memoria aunque no se pueda escribir el archivo."""
    from lilith_cli import extra_commands

    class RutaRota:
        def exists(self):
            return False

        def write_text(self, *a, **kw):
            raise OSError("disco lleno")

    monkeypatch.setattr(extra_commands, "_pin_storage_path", lambda: RutaRota())
    fake_session.history = [{"role": "assistant", "content": "igual sirve"}]

    await run_pin_command(fake_session, "1")

    assert fake_session._pinned_messages[0]["content"] == "igual sirve"
