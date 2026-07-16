"""Persistent delegation cost telemetry and /costs wiring."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


def test_costs_accumulate_by_preset_provider_session_and_history(tmp_path: Path) -> None:
    from lilith_tools.orchestration_state import OrchestrationStateStore

    store = OrchestrationStateStore(tmp_path / "state.json")
    store.record_cost("quick", "openai", {"prompt_tokens": 10, "completion_tokens": 3}, session_id="s1")
    store.record_cost("quick", "openai", {"prompt_tokens": 2, "completion_tokens": 1}, session_id="s1")
    store.record_cost("deep", "anthropic", {"prompt_tokens": 7, "completion_tokens": 4}, session_id="s2")

    s1 = store.cost_summary("s1")
    assert s1["session"]["total"] == {"prompt_tokens": 12, "completion_tokens": 4, "calls": 2}
    assert s1["historical"]["presets"]["quick"]["calls"] == 2
    assert s1["historical"]["providers"]["anthropic"]["completion_tokens"] == 4
    assert s1["historical"]["total"]["calls"] == 3


def test_cost_reset_only_clears_history(tmp_path: Path) -> None:
    from lilith_tools.orchestration_state import OrchestrationStateStore

    store = OrchestrationStateStore(tmp_path / "state.json")
    store.set_plan("keep")
    store.record_cost("quick", "openai", {"prompt_tokens": 1}, session_id="s")
    store.reset_costs()
    state = store.get()
    assert state["plan"]["name"] == "keep"
    assert store.cost_summary("s")["historical"]["total"] == {}


@pytest.fixture
def session():
    return SimpleNamespace(
        config=SimpleNamespace(confirm_write=True),
        _command_history=[], _tool_call_history=[], last_user_message="",
        _session_id="s1",
    )


@pytest.mark.asyncio
async def test_costs_command_renders_and_requires_confirmation(session, monkeypatch, tmp_path: Path) -> None:
    from lilith_cli.commands import CommandRegistry
    from lilith_cli.repl import _SLASH_COMMANDS
    from lilith_tools.orchestration_state import OrchestrationStateStore

    path = tmp_path / "state.json"
    monkeypatch.setenv("YGGDRASIL_ORCHESTRATION_STATE", str(path))
    store = OrchestrationStateStore(path)
    store.record_cost("quick", "openai", {"prompt_tokens": 5, "completion_tokens": 2}, session_id="s1")
    registry = CommandRegistry(session)
    registry.discover()
    command = registry.get("costs")
    assert command is not None
    assert "/costs" in _SLASH_COMMANDS
    with command.session_console_capture() as capture:
        await command.execute("")
    output = capture.get()
    assert "quick" in output and "openai" in output and "5" in output
    await command.execute("reset")
    assert store.cost_summary("s1")["historical"]["total"]["calls"] == 1
    await command.execute("reset CONFIRMAR")
    assert store.cost_summary("s1")["historical"]["total"] == {}
