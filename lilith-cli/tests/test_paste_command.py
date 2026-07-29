"""Tests for the /paste slash command.

``/paste`` lee el portapapeles del sistema y lo reenvía al agente. Acá
no probamos la lectura real (depende de wl-paste, xclip, pbpaste o
PowerShell — varía por host) sino la lógica de orquestación: el
comando rechaza el portapapeles vacío, corta cuando supera el límite,
soporta ``--prepend`` y delega correctamente al agente cuando todo
está bien.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from lilith_cli.paste_command import (
    _MAX_BYTES,
    _preview,
    _read_clipboard,
    run_paste_command,
)


class _FakeSession:
    """Suficiente para probar ``/paste`` sin tocar la red ni el agente."""

    def __init__(self) -> None:
        self.history = []
        self.calls: list[str] = []

    async def process_message(self, text: str) -> None:
        self.calls.append(text)


def _run(coro):
    """Helper chiquito porque ``run_paste_command`` es ``async``."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── _preview ────────────────────────────────────────────────────────────────


def test_preview_colapsa_espacios_y_newlines() -> None:
    """Salidas multi-línea deben caber en una sola línea para paneles."""
    text = "línea uno\n\n   línea    dos\r\nlínea tres"
    out = _preview(text)
    assert "\n" not in out
    assert "línea uno" in out and "línea dos" in out and "línea tres" in out


def test_preview_corta_a_limite() -> None:
    """Si el preview pasa de N caracteres, agrega elipsis."""
    text = "x" * 500
    out = _preview(text, limit=10)
    assert out.endswith("…")
    assert len(out) <= 11


# ── _read_clipboard (helper testeable indirectamente) ───────────────────────


def test_read_clipboard_devuelve_none_si_nada_sirve() -> None:
    """Si ningún backend está disponible, debemos devolver ``None``."""
    with patch(
        "lilith_cli.paste_command.subprocess.run",
        side_effect=FileNotFoundError("no backend"),
    ):
        result = _read_clipboard()
    assert result is None


# ── run_paste_command: dispatching al agente ────────────────────────────────


def test_paste_invoca_agente_con_contenido_del_portapapeles() -> None:
    """El camino feliz: leo el portapapeles y delego al agente."""
    session = _FakeSession()

    with patch(
        "lilith_cli.paste_command._read_clipboard",
        return_value="selección del usuario",
    ):
        result = _run(run_paste_command(session, ""))

    assert result is None
    assert session.calls == ["selección del usuario"]


def test_paste_antepone_prefijo_si_pasa_flag() -> None:
    """``/paste --prepend "Resumí: "`` concatena el prefijo antes del contenido."""
    session = _FakeSession()

    with patch(
        "lilith_cli.paste_command._read_clipboard",
        return_value="lorem ipsum",
    ):
        _run(run_paste_command(session, '--prepend "Resumí: "'))

    assert session.calls == ["Resumí: lorem ipsum"]


def test_paste_reporta_si_clipboard_vacio() -> None:
    """Un portapapeles vacío no debe caer al agente silenciosamente."""
    session = _FakeSession()

    with patch(
        "lilith_cli.paste_command._read_clipboard",
        return_value="",
    ):
        _run(run_paste_command(session, ""))

    assert session.calls == []


def test_paste_reporta_si_no_se_pudo_leer() -> None:
    """Si ningún backend devolvió datos, no delegamos al agente."""
    session = _FakeSession()

    with patch(
        "lilith_cli.paste_command._read_clipboard",
        return_value=None,
    ):
        _run(run_paste_command(session, ""))

    assert session.calls == []


def test_paste_bloquea_si_supera_limite_de_bytes() -> None:
    """Pasarse del límite tiene que fallar limpio, no delegar al agente."""
    session = _FakeSession()
    huge = "A" * (_MAX_BYTES + 1)

    with patch(
        "lilith_cli.paste_command._read_clipboard",
        return_value=huge,
    ):
        _run(run_paste_command(session, ""))

    assert session.calls == []


def test_paste_help_imprime_pantalla_y_no_delega() -> None:
    """``/paste help`` debe mostrar ayuda y no tocar al agente."""
    session = _FakeSession()
    _run(run_paste_command(session, "help"))
    assert session.calls == []


def test_paste_argumento_desconocido_falla_sin_delegar() -> None:
    """Bandera no reconocida → argumento inválido, no se delega."""
    session = _FakeSession()
    _run(run_paste_command(session, "--nope foo"))
    assert session.calls == []


def test_paste_prepend_sin_valor_falla_sin_delegar() -> None:
    """``/paste --prepend`` sin valor a continuación debe reportar y salir."""
    session = _FakeSession()
    _run(run_paste_command(session, "--prepend"))
    assert session.calls == []


def test_paste_usa_process_message_stream_si_existe() -> None:
    """Si la sesión expone el streaming, lo preferimos sobre ``process_message``."""

    class StreamSession(_FakeSession):
        def __init__(self) -> None:
            super().__init__()
            self.stream_calls: list[str] = []

        async def process_message_stream(self, text: str):
            self.stream_calls.append(text)
            self.calls.append(text)

    session = StreamSession()

    with patch(
        "lilith_cli.paste_command._read_clipboard",
        return_value="stream me",
    ):
        _run(run_paste_command(session, ""))

    assert session.stream_calls == ["stream me"]
    assert session.calls == ["stream me"]
