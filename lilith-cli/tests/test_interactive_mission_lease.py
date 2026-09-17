import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

from lilith_cli.agent import AgentSession
from lilith_cli.config import YggdrasilConfig
from lilith_cli.mission.runtime import AutonomousCampaignRuntime
from lilith_cli.providers import ToolCall
from lilith_cli.robust_kit import configure_session
from lilith_tools.orchestration_state import OrchestrationStateStore


async def _exercise_session(project: Path, state: Path, longruns: Path):
    cfg = YggdrasilConfig(
        memory={"enabled": False},
        require_calibration_for_edits=False,
        confirm_write=False,
    )
    session = AgentSession(cfg, provider=MagicMock())
    configure_session(session, "agent")
    session._mission_lease_seconds = 15.0
    session._mission_heartbeat_seconds = 0.05

    prepare = ToolCall(
        id="prepare",
        name="mission_prepare",
        arguments={
            "objective": "interactive lease mission",
            "project_root": str(project),
            "success_criteria": ["close mission"],
        },
    )
    result = await session.execute_tool(prepare)
    assert not result.content.startswith("Error:"), result.content
    payload = json.loads(result.content)
    registration = payload["registration"]
    task_id = registration["task_id"]
    mission_id = registration["mission_id"]
    assert registration["status"] == "delegada"

    store = OrchestrationStateStore(state)
    current = next(row for row in store.get()["tasks"] if row["id"] == task_id)
    assert current["status"] == "delegada"
    assert current["lease_owner"] == session._mission_owner
    assert session._mission_task_id == task_id
    assert session._mission_id == mission_id

    runtime = AutonomousCampaignRuntime(
        state_path=state,
        longrun_root=longruns,
        max_missions=1,
        session_factory=lambda root: None,
    )
    assert runtime.next_eligible() is None

    await asyncio.sleep(0.12)
    events = store.events(limit=100, task_id=task_id)
    assert any(row["event_type"] == "task.lease_renewed" for row in events)

    complete = ToolCall(
        id="complete",
        name="mission_complete",
        arguments={
            "task_id": task_id,
            "success": True,
            "summary": "interactive mission complete",
        },
    )
    completed = await session.execute_tool(complete)
    assert not completed.content.startswith("Error:"), completed.content
    current = next(row for row in store.get()["tasks"] if row["id"] == task_id)
    assert current["status"] == "completada"
    assert current.get("lease_owner") is None
    assert session._mission_task_id is None
    assert session._mission_id is None
    return session


def test_interactive_session_owns_and_heartbeats_mission(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    state = tmp_path / "state.sqlite3"
    longruns = tmp_path / "longruns"
    monkeypatch.setenv("YGGDRASIL_ORCHESTRATION_STATE", str(state))
    monkeypatch.setenv("YGGDRASIL_LONGRUN_ROOT", str(longruns))
    asyncio.run(_exercise_session(project, state, longruns))
