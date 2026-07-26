"""Tests de /goal: objetivo de sesión persistente y visible para el modelo."""

from __future__ import annotations

import json

import pytest


@pytest.mark.asyncio
async def test_goal_set_es_visible_y_controla_presupuesto(fake_session, capsys):
    from lilith_cli.extra_commands import _GOAL_MARKER, run_goal_command

    fake_session._total_usage["total_tokens"] = 1_000
    await run_goal_command(fake_session, "entregar el refactor verificado --budget 8k")

    assert fake_session._session_goal["objective"] == "entregar el refactor verificado"
    assert fake_session._session_goal["budget_tokens"] == 8_000
    goal_messages = [
        message
        for message in fake_session.history
        if message.get("role") == "system"
        and str(message.get("content", "")).startswith(_GOAL_MARKER)
    ]
    assert len(goal_messages) == 1
    assert "Do not claim it is complete" in goal_messages[0]["content"]
    built = fake_session._build_messages()
    assert "SESSION GOAL (active): entregar el refactor verificado" in built[0]["content"]
    assert all(
        not str(message.get("content", "")).startswith(_GOAL_MARKER)
        for message in built[1:]
    )
    assert "compartido con el modelo" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_goal_lifecycle_se_recupera_del_historial(fake_session, capsys):
    from lilith_cli.extra_commands import run_goal_command

    await run_goal_command(fake_session, "corregir el parser")
    await run_goal_command(fake_session, "pause")
    saved_history = list(fake_session.history)

    del fake_session._session_goal
    fake_session.history = saved_history
    await run_goal_command(fake_session, "resume")
    assert fake_session._session_goal["status"] == "active"

    await run_goal_command(fake_session, "complete")
    assert fake_session._session_goal["status"] == "completed"
    assert "completed_at" in fake_session._session_goal

    capsys.readouterr()
    await run_goal_command(fake_session, "json")
    payload = json.loads(capsys.readouterr().out)
    assert payload["objective"] == "corregir el parser"
    assert payload["status"] == "completed"


def test_goal_esta_conectado_al_repl():
    import inspect

    from lilith_cli import repl

    assert "/goal" in repl._SLASH_COMMANDS
    assert 'cmd_name == "goal"' in inspect.getsource(repl.run_repl)
