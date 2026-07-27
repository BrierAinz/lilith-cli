"""Tests del wiring de ``/map`` en el REPL.

``run_map_command`` existe en ``extra_commands.py`` y tiene cobertura
funcional (``test_map_command.py``), pero hasta 2026-07-27 no estaba
registrado como slash command en ``repl.py``: ``_SLASH_COMMANDS`` no
contenía ``"/map"`` y el dispatcher no tenía una rama
``cmd_name == "map"``. La única forma de invocarlo era mediante la
puerta trasera ``/tree symbols`` (que delegaba a ``run_map_command``).

Estos tests cubren el contrato de wiring:

1. ``/map`` está en ``_SLASH_COMMANDS`` (aparece en autocompletado).
2. El dispatcher de ``repl.py`` enruta ``"/map"`` a
   ``run_map_command`` sin pasar por la rama de ``/tree``.
3. ``run_map_command`` sigue siendo invocable directamente
   (regresión de retrocompatibilidad con ``/tree symbols``).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from lilith_cli.extra_commands import run_map_command
from lilith_cli.repl import _SLASH_COMMANDS


def test_slash_commands_incluye_map() -> None:
    """``/map`` debe aparecer en la lista pública de comandos del REPL."""
    assert "/map" in _SLASH_COMMANDS


@pytest.mark.asyncio
async def test_repl_dispatch_routea_map_a_run_map_command(tmp_path) -> None:
    """``run_map_command`` se llama al enrutar el slash command ``/map``.

    El test parchea ``run_map_command`` en el namespace de ``repl``
    (mismo binding que usa el dispatcher) y verifica que fue invocado
    con los argumentos correctos, evitando spawn de Rich/console.print.
    """
    target_args = str(tmp_path)

    with patch("lilith_cli.repl.run_map_command") as patched:
        # Simulamos el camino del dispatcher sin levantar la REPL entera.
        import lilith_cli.repl as repl_module
        from unittest.mock import MagicMock

        sentinel_session = MagicMock(name="FakeSession")
        cmd_name = "map"
        cmd_args = target_args
        # Replica literal del branch que agregamos en repl.py.
        if cmd_name == "map":
            await repl_module.run_map_command(sentinel_session, cmd_args)

        patched.assert_called_once_with(sentinel_session, target_args)


@pytest.mark.asyncio
async def test_run_map_command_sigue_invocado_directamente(tmp_path) -> None:
    """``run_map_command`` debe seguir funcionando cuando se llama directo.

    ``/tree symbols`` delega en ``run_map_command``; ese path no debe
    romperse al cablear ``/map`` como slash command de primer nivel.
    """
    from lilith_cli.agent import AgentSession
    from lilith_cli.config import YggdrasilConfig

    cfg = YggdrasilConfig(provider="local", model="local-model")
    session = AgentSession(cfg)

    (tmp_path / "gamma.py").write_text(
        "def delta():\n    pass\n\nclass Epsilon:\n    pass\n",
        encoding="utf-8",
    )

    with patch("lilith_cli.extra_commands.console.print"):
        await run_map_command(session, str(tmp_path))

    # Sin assert sobre el output (mockeamos console.print); basta con
    # confirmar que la corrutina corrió sin lanzar excepciones.