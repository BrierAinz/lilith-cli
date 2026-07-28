"""Test configuration for lilith-cli.

Adds the package directory to sys.path so that
`from lilith_cli.main import ...` works without pip install.
"""

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


# ── Salida determinista ─────────────────────────────────────────────
#
# Decenas de tests comparan la salida de Rich contra texto plano
# ("Tiempo total: 00:00.016"). Rich decide si emitir secuencias ANSI al
# construir el ``Console`` de ``render.py``, que se crea al importar el
# módulo — antes de que corra cualquier fixture.
#
# ``FORCE_COLOR`` fuerza ``is_terminal=True`` aunque pytest tenga la salida
# capturada, y entonces Rich emite estilos igual. ``NO_COLOR`` no alcanza
# para taparlo: suprime los colores pero no el ``bold``, así que el texto
# llega como "\x1b[1m00:00\x1b[0m" y las aserciones fallan. Muchos runners
# de agentes y CI exportan FORCE_COLOR, y ahí la suite se cae entera por el
# entorno y no por el código (se vieron 88 fallos así).
#
# Se limpia a nivel de módulo, no en un fixture, porque para cuando el
# primer fixture corre el ``Console`` ya está construido.
for _var in ("FORCE_COLOR", "CLICOLOR_FORCE"):
    os.environ.pop(_var, None)


# Ensure lilith_cli is importable when running tests directly
_pkg_dir = str(Path(__file__).resolve().parent.parent)
if _pkg_dir not in sys.path:
    sys.path.insert(0, _pkg_dir)


# ── Estado de orquestación aislado ──────────────────────────────────
#
# ``DelegateSubagentTool`` persiste cada delegación llamando a
# ``OrchestrationStateStore()`` sin argumentos, que resuelve a
# ``~/.yggdrasil/orchestration_state.json``: el MISMO archivo que /state
# y /costs le muestran al operador. Los tests que ejercitan la
# delegación con presets falsos le metían tareas y costos inventados al
# estado real (se acumularon ~184 tareas "fake-preset").
#
# Apuntar el override documentado ``YGGDRASIL_ORCHESTRATION_STATE`` a un
# archivo temporal por test mantiene esa persistencia bajo cobertura
# pero la manda a un destino descartable. Los tests que definen la
# variable o pasan ``state_path`` explícito siguen mandando: esto solo
# fija el valor por defecto.

@pytest.fixture(autouse=True)
def _isolate_orchestration_state(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "YGGDRASIL_ORCHESTRATION_STATE", str(tmp_path / "orchestration_state.json")
    )


@pytest.fixture
def fake_session():
    """Return a lightweight AgentSession with a mocked provider."""
    from lilith_cli.agent import AgentSession
    from lilith_cli.config import YggdrasilConfig

    cfg = YggdrasilConfig(provider="local", model="local-model")
    session = AgentSession(cfg)
    session.provider = MagicMock()
    session.provider.stream = AsyncMock(return_value=iter([]))
    return session
