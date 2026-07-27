"""Tests de /review --agent para revisiones aisladas en segundo plano."""

from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_review_agent_delega_diff_staged_sin_tocar_historial(
    fake_session, monkeypatch, capsys
):
    """La revisión vuelve enseguida y delega el diff capturado fuera del chat."""
    from lilith_cli import extra_commands as ec
    import lilith_tools.delegate as delegate_mod

    calls: dict[str, object] = {}

    def fake_review_git(**kwargs):
        calls["git"] = kwargs
        return SimpleNamespace(
            success=True,
            data={"output": "diff --git a/app.py b/app.py\n+eval(user_input)\n"},
            error=None,
        )

    class FakeDelegate:
        def execute(self, **kwargs):
            calls["delegate"] = kwargs
            return SimpleNamespace(
                success=True,
                data={"content": "HIGH app.py: eval sobre entrada no confiable"},
                error=None,
            )

    monkeypatch.setattr(ec, "_run_review_git", fake_review_git)
    monkeypatch.setattr(delegate_mod, "DelegateSubagentTool", FakeDelegate)
    original_history = list(fake_session.history)

    await ec.run_review_command(fake_session, "--agent --staged revisar seguridad")

    task = fake_session._review_agent_task
    assert task is not None
    await task
    assert calls["git"]["op"] == "diff"
    assert calls["git"]["args"] == "--cached"
    delegated = calls["delegate"]
    assert delegated["preset"] == "investigador-minimax"
    assert "eval(user_input)" in delegated["prompt"]
    assert "revisar seguridad" in delegated["prompt"]
    assert fake_session._review_agent_result["status"] == "done"
    assert fake_session.history == original_history
    assert "segundo plano" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_review_agent_result_muestra_informe_terminado(fake_session, capsys):
    """El resultado completado queda disponible sin volver a ejecutar la revisión."""
    from lilith_cli.extra_commands import run_review_command

    fake_session._review_agent_result = {
        "status": "done",
        "content": "MEDIUM api.py: falta validar el payload",
        "error": "",
    }
    fake_session._review_agent_task = None

    await run_review_command(fake_session, "--agent result")

    output = capsys.readouterr().out
    assert "Revisión del sub-agente" in output
    assert "falta validar el payload" in output