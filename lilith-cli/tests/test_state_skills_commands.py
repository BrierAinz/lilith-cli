"""REPL wiring tests for /state and /skills."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def session():
    return SimpleNamespace(
        config=SimpleNamespace(confirm_write=True),
        _command_history=[],
        _tool_call_history=[],
        last_user_message="",
    )


def test_commands_registered_and_autocomplete_present(session) -> None:
    from lilith_cli.commands import CommandRegistry
    from lilith_cli.repl import _SLASH_COMMANDS

    registry = CommandRegistry(session)
    registry.discover()
    assert registry.get("state") is not None
    assert registry.get("skills") is not None
    assert "/state" in _SLASH_COMMANDS
    assert "/skills" in _SLASH_COMMANDS


@pytest.mark.asyncio
async def test_state_command_renders_plan_and_tasks(
    session, monkeypatch, tmp_path: Path
) -> None:
    from lilith_cli.commands import CommandRegistry
    from lilith_tools.orchestration_state import OrchestrationStateStore

    path = tmp_path / "state.json"
    monkeypatch.setenv("YGGDRASIL_ORCHESTRATION_STATE", str(path))
    store = OrchestrationStateStore(path)
    store.set_plan("Release v7")
    store.add_task("Implement memory", task_id="memory")

    registry = CommandRegistry(session)
    registry.discover()
    with registry.get("state").session_console_capture() as capture:
        await registry.get("state").execute("")
    output = capture.get()
    assert "Release v7" in output
    assert "Implement memory" in output
    assert "pendiente" in output


@pytest.mark.asyncio
async def test_state_clear_requires_explicit_confirmation(
    session, monkeypatch, tmp_path: Path
) -> None:
    from lilith_cli.commands import CommandRegistry
    from lilith_tools.orchestration_state import OrchestrationStateStore

    path = tmp_path / "state.json"
    monkeypatch.setenv("YGGDRASIL_ORCHESTRATION_STATE", str(path))
    OrchestrationStateStore(path).set_plan("Keep me")
    registry = CommandRegistry(session)
    registry.discover()
    await registry.get("state").execute("clear")
    assert OrchestrationStateStore(path).get()["plan"]["name"] == "Keep me"
    await registry.get("state").execute("clear CONFIRMAR")
    assert OrchestrationStateStore(path).get()["plan"] is None


@pytest.mark.asyncio
async def test_skills_list_show_save_delete(session, monkeypatch, tmp_path: Path) -> None:
    from lilith_cli.commands import CommandRegistry

    monkeypatch.setenv("YGGDRASIL_DELEGATION_SKILLS", str(tmp_path / "skills"))
    session._tool_call_history.append(
        {
            "name": "delegate_subagent",
            "arguments": {
                "preset": "ejecutor-kimi",
                "prompt": "Implement {TASK}",
                "agentic": True,
            },
            "success": True,
        }
    )
    registry = CommandRegistry(session)
    registry.discover()
    command = registry.get("skills")
    await command.execute("save latest --name latest-run --description reusable")
    with command.session_console_capture() as capture:
        await command.execute("show latest-run")
    assert "ejecutor-kimi" in capture.get()
    await command.execute("delete latest-run CONFIRMAR")
    with command.session_console_capture() as capture:
        await command.execute("list")
    assert "latest-run" not in capture.get()
