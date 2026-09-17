"""El interruptor de los paneles en vivo.

El operador pidio el 2026-09-15 "solo su pensamiento y lo que dice al final".
Antes de esto la unica compuerta era ``sys.stdout.isatty()``, o sea que en una
terminal los paneles se abrian SIEMPRE y no habia forma de callarlos.
"""

from __future__ import annotations

import pytest

from lilith_cli.config import YggdrasilConfig
from lilith_cli.tool_progress import (
    DelegationLive,
    DelegationStreamBuffer,
    ToolProgressTracker,
    set_tool_panels,
    tool_panels_enabled,
)


@pytest.fixture(autouse=True)
def _restaurar_interruptor():
    """Deja el interruptor como estaba: es estado de modulo."""
    previo = tool_panels_enabled()
    yield
    set_tool_panels(previo)


def _crea_live() -> bool:
    tracker = ToolProgressTracker()
    tracker.start("herramienta_de_prueba")
    creado = tracker._live is not None
    tracker.__exit__(None, None, None)
    return creado


def _buffer() -> DelegationStreamBuffer:
    return DelegationStreamBuffer(preset="ratatoskr", model="MiniMax-M3", agentic=False)


def test_por_omision_los_paneles_estan_apagados():
    assert YggdrasilConfig().show_tool_panels is False


def test_apagado_no_crea_ningun_live():
    set_tool_panels(False)
    assert _crea_live() is False


def test_encendido_vuelve_a_crear_el_live():
    set_tool_panels(True)
    assert _crea_live() is True


def test_apagado_desactiva_el_panel_de_delegacion():
    set_tool_panels(False)
    assert DelegationLive(_buffer())._enabled is False


def test_el_interruptor_manda_sobre_isatty(monkeypatch):
    """Lo que de verdad importa: apagado gana aunque haya terminal.

    Si esta comprobacion se colara DESPUES del isatty, el panel volveria a
    abrirse en la consola del operador, que es el caso que se quiere callar.
    """
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)
    set_tool_panels(False)
    assert DelegationLive(_buffer())._enabled is False
    set_tool_panels(True)
    assert DelegationLive(_buffer())._enabled is True
