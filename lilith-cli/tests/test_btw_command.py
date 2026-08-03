"""Pruebas del comando ``/btw`` (pregunta rápida al modelo, sin tocar historial).

``/btw`` está pensado para esos desvíos cortos en los que el usuario no quiere
ensuciar el contexto de la conversación — la duda al vuelo, la consulta del
sintaxis exacta, el "¿esto funciona en Python 3.10?". El handler pregunta al
proveedor sin tocar ``session.history``, por lo que la conversación del
siguiente turno no debe crecer.

Cubrimos:
  * rechazo de input vacío,
  * llamada al provider con mensajes que NO incluyen el historial,
  * extracción de la respuesta desde el sobre OpenAI estándar,
  * no-mutación de ``session.history`` antes y después,
  * manejo defensivo de respuestas vacías / mal formadas.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from lilith_cli.btw_command import run_btw_command


def _run(coro):
    return asyncio.run(coro)


# ── Helpers ────────────────────────────────────────────────────────


def _patch_provider(session, response: dict) -> None:
    """Reemplaza ``session.provider.complete`` por un mock que devuelve *response*."""
    session.provider = type(session.provider)()
    session.provider.complete = AsyncMock(return_value=response)


# ── Tests ──────────────────────────────────────────────────────────


def test_input_vacio_reporta_error_y_no_llama_al_proveedor(fake_session, capsys):
    """``/btw`` sin argumentos no debe gastar una llamada al modelo."""
    called = {"n": 0}

    async def _spy(messages, **_):
        called["n"] += 1
        return {"choices": [{"message": {"content": "noop"}}]}

    session = fake_session
    session.provider.complete = _spy
    pre_history_len = len(session.history)

    _run(run_btw_command(session, ""))

    out = capsys.readouterr().out
    assert "/btw necesita una pregunta" in out
    assert called["n"] == 0, "el provider no debería recibir llamadas con input vacío"
    assert len(session.history) == pre_history_len, "el historial no debe crecer en error path"


def test_pregunta_valida_llama_al_proveedor_con_solo_sistema_y_usuario(fake_session, capsys):
    """El handler arma una lista mínima: 1 system + 1 user, sin historial."""
    captured: dict = {}

    async def _capture(messages, **_):
        captured["messages"] = messages
        return {
            "choices": [
                {"message": {"role": "assistant", "content": "Python 3.10+"}}
            ]
        }

    session = fake_session
    # Sembrar historial con datos de prueba para verificar que NO se filtran.
    from lilith_cli.agent import Message

    session.history.append(Message.user("hola"))
    session.history.append(Message.assistant("buenos días"))

    session.provider.complete = _capture

    _run(run_btw_command(session, "¿esto es Python 3.10+?"))

    msgs = captured["messages"]
    assert len(msgs) == 2, f"se esperaba [system, user], se obtuvo {len(msgs)} mensajes"
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
    assert msgs[1]["content"] == "¿esto es Python 3.10+?"
    # El system NO debe contener el contenido del historial de la sesión.
    assert "buenos días" not in msgs[0]["content"]
    assert "hola" not in msgs[1]["content"]

    out = capsys.readouterr().out
    assert "Python 3.10+" in out
    assert "BTW" in out
    assert "no se guardó en el historial" in out


def test_no_muta_el_historial(fake_session, capsys):
    """Después de la pregunta, ``session.history`` debe quedar igual."""
    from lilith_cli.agent import Message

    session = fake_session
    session.history.append(Message.user("hola"))
    snapshot = list(session.history)

    _patch_provider(
        session,
        {"choices": [{"message": {"role": "assistant", "content": "respuesta"}}]},
    )

    _run(run_btw_command(session, "¿cómo era?"))

    assert list(session.history) == snapshot, (
        "/btw no debe modificar session.history — eso es justo lo que aísla "
        "al slash command del flujo normal"
    )


def test_extraccion_de_respuesta_flat_content(fake_session, capsys):
    """Algunos mocks locales exponen ``response['content']`` plano."""
    session = fake_session
    _patch_provider(session, {"content": "Respuesta en formato plano."})

    _run(run_btw_command(session, "trivial"))

    out = capsys.readouterr().out
    assert "Respuesta en formato plano." in out


def test_respuesta_vacia_se_muestra_amigablemente(fake_session, capsys):
    """Si el modelo no devuelve texto, mostramos un mensaje y NO caemos."""
    session = fake_session
    _patch_provider(session, {"choices": [{"message": {"content": ""}}]})

    _run(run_btw_command(session, "trivial"))

    out = capsys.readouterr().out
    assert "El modelo no devolvió contenido" in out


def test_error_del_proveedor_no_crashea_el_repl(fake_session, capsys):
    """Si ``provider.complete`` lanza, mostramos el error y salimos limpio."""
    session = fake_session

    async def _boom(messages, **_):
        raise RuntimeError("upstream 502")

    session.provider.complete = _boom

    _run(run_btw_command(session, "trivial"))

    out = capsys.readouterr().out
    assert "upstream 502" in out or "Error" in out


def test_alias_aside_y_side_funcionan_igual(fake_session, capsys):
    """``/aside`` y ``/side`` son aliases — el dispatcher los mapea, pero el
    handler es único. Este test verifica que el handler no asuma que el
    primer token del input es parte del comando.
    """
    session = fake_session
    captured: dict = {}

    async def _cap(messages, **_):
        captured["messages"] = messages
        return {"choices": [{"message": {"content": "ok"}}]}

    session.provider.complete = _cap

    _run(run_btw_command(session, "  hola mundo  "))  # espacios al borde

    msgs = captured["messages"]
    assert msgs[1]["content"] == "hola mundo", "se debe hacer strip del input"


@pytest.mark.parametrize("ancla", ["/btw", "/aside", "/side"])
def test_alias_listados_en_slash_commands(ancla):
    """Las tres formas deben estar en ``_SLASH_COMMANDS`` para autocompletado."""
    from lilith_cli.repl import _SLASH_COMMANDS

    assert ancla in _SLASH_COMMANDS, (
        f"{ancla} no aparece en _SLASH_COMMANDS — el usuario no lo descubrirá "
        "tabulando. Agregalo junto a los otros comandos de sesión."
    )


def test_alias_listados_en_help_catalog():
    """``/help`` debe documentar al menos ``/btw`` (los alias se mencionan en
    el docstring del módulo, pero el catálogo sólo lista el nombre canónico).
    """
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "lilith_cli" / "extra_commands.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    run_help = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "run_help_command"
    )
    catalog = next(
        n
        for n in ast.walk(run_help)
        if isinstance(n, ast.AnnAssign)
        and isinstance(n.target, ast.Name)
        and n.target.id == "catalog"
        and isinstance(n.value, ast.Dict)
    )
    names = {
        elt.elts[0].value
        for value in catalog.value.values
        if isinstance(value, ast.List)
        for elt in value.elts
        if isinstance(elt, ast.Tuple) and elt.elts
        and isinstance(elt.elts[0], ast.Constant)
        and isinstance(elt.elts[0].value, str)
    }
    assert "btw" in names, (
        "/btw debe figurar en el catálogo de /help. Sin eso, "
        "test_autocomplete_covers_help_catalog falla y el usuario nunca lo "
        "descubre en la ayuda interactiva."
    )