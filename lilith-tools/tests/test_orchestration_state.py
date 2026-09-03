"""Persistent orchestration state: active plan and delegated tasks."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest


def test_state_survives_new_store_instance(tmp_path: Path) -> None:
    from lilith_tools.orchestration_state import OrchestrationStateStore

    path = tmp_path / "orchestration_state.json"
    first = OrchestrationStateStore(path)
    first.set_plan("Release v7", "Finish persistent delegation")
    first.add_task("Write tests", "Cover restart behavior", task_id="tests")

    state = OrchestrationStateStore(path).get()
    assert state["plan"]["name"] == "Release v7"
    assert state["tasks"][0]["id"] == "tests"
    assert state["tasks"][0]["status"] == "pendiente"


def test_state_validates_transitions_and_accumulates_usage(tmp_path: Path) -> None:
    from lilith_tools.orchestration_state import OrchestrationStateStore

    store = OrchestrationStateStore(tmp_path / "state.json")
    store.set_plan("Plan")
    store.add_task("Task", task_id="t1")
    store.update_task("t1", status="delegada", usage={"total_tokens": 10})
    updated = store.update_task(
        "t1",
        status="completada",
        result="done",
        usage={"prompt_tokens": 3, "total_tokens": 5},
    )
    assert updated["usage"] == {"prompt_tokens": 3, "total_tokens": 15}
    assert updated["completed_at"] is not None

    with pytest.raises(ValueError, match="transición"):
        store.update_task("t1", status="delegada")


def test_tool_actions_get_set_add_update_clear(tmp_path: Path) -> None:
    from lilith_tools.orchestration_state import OrchestrationStateTool

    tool = OrchestrationStateTool()
    path = str(tmp_path / "state.json")
    assert tool.execute(action="set_plan", name="Roadmap", state_path=path).success
    added = tool.execute(
        action="add_task", title="Implement", description="Feature A", state_path=path,
        success_criteria=["tests pass"], budget={"max_tokens": 100},
        correlation_id="corr-test", trace_id="trace-test",
    )
    task_id = added.data["task"]["id"]
    assert tool.execute(
        action="update_task", task_id=task_id, status="bloqueada", state_path=path,
        verification={"verified": False, "evidence": []},
    ).success
    task = tool.execute(action="get", state_path=path).data["tasks"][0]
    assert task["status"] == "bloqueada"
    assert task["success_criteria"] == ["tests pass"]
    assert task["budget"] == {"max_tokens": 100}
    assert task["correlation_id"] == "corr-test"
    assert task["verification"]["verified"] is False
    assert tool.execute(action="post_mortems", state_path=path).data == {"post_mortems": []}
    assert tool.execute(action="clear", state_path=path).data["cleared"] is True


def test_delegate_registers_success_and_usage(monkeypatch, tmp_path: Path) -> None:
    from types import SimpleNamespace
    from lilith_tools.delegate import DelegateSubagentTool
    from lilith_tools.orchestration_state import OrchestrationStateStore

    class Provider:
        async def complete(self, messages, *, tools=None, **kwargs):
            return {"content": "implemented", "usage": {"total_tokens": 17}, "tool_calls": []}

        async def close(self):
            return None

    profile = SimpleNamespace(model="fake-model", max_tokens=None)
    cfg = SimpleNamespace(provider="fake", model="fake-model", providers={"fake": profile}, max_tokens=100, temperature=0.0)
    monkeypatch.setattr("lilith_cli.config.load_config", lambda: cfg)
    monkeypatch.setattr("lilith_cli.main._load_subagent_presets", lambda config_path=None: {"fake-preset": {"provider": "fake"}})
    monkeypatch.setattr("lilith_cli.providers.LLMProviderWrapper", lambda _cfg: Provider())
    state_path = tmp_path / "state.json"
    monkeypatch.setenv("YGGDRASIL_ORCHESTRATION_STATE", str(state_path))

    result = DelegateSubagentTool().execute(
        preset="fake-preset", prompt="Implement persistent memory"
    )
    assert result.success
    task = OrchestrationStateStore(state_path).get()["tasks"][0]
    assert task["preset"] == "fake-preset"
    assert task["status"] == "completada"
    assert task["result"] == "implemented"
    assert task["usage"]["total_tokens"] == 17
    assert task["description"] == "Implement persistent memory"


def test_delegate_registers_failure(monkeypatch, tmp_path: Path) -> None:
    from types import SimpleNamespace
    from lilith_tools.delegate import DelegateSubagentTool
    from lilith_tools.orchestration_state import OrchestrationStateStore

    class Provider:
        async def complete(self, messages, *, tools=None, **kwargs):
            raise RuntimeError("provider failed")

        async def close(self):
            return None

    profile = SimpleNamespace(model="fake-model", max_tokens=None)
    cfg = SimpleNamespace(provider="fake", model="fake-model", providers={"fake": profile}, max_tokens=100, temperature=0.0)
    monkeypatch.setattr("lilith_cli.config.load_config", lambda: cfg)
    monkeypatch.setattr("lilith_cli.main._load_subagent_presets", lambda config_path=None: {"fake-preset": {"provider": "fake"}})
    monkeypatch.setattr("lilith_cli.providers.LLMProviderWrapper", lambda _cfg: Provider())
    state_path = tmp_path / "state.json"
    monkeypatch.setenv("YGGDRASIL_ORCHESTRATION_STATE", str(state_path))

    result = DelegateSubagentTool().execute(preset="fake-preset", prompt="Fail safely")
    assert not result.success
    task = OrchestrationStateStore(state_path).get()["tasks"][0]
    assert task["status"] == "fallida"
    assert "provider failed" in task["result"]
