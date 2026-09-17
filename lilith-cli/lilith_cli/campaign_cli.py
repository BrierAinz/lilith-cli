from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from cyclopts import App
from lilith_tools.orchestration_state import OrchestrationStateStore

from .mission.runtime import AutonomousCampaignRuntime
from .mission.runtime_status import aggregate_budget_status

campaign_app = App(
    name="campaign",
    help="Run and inspect Lilith's durable autonomous campaign queue.",
)


def _runtime(
    *,
    state: str | None,
    longrun_root: str | None,
    max_missions: int = 5,
    max_wall_seconds: float = 14_400,
) -> AutonomousCampaignRuntime:
    return AutonomousCampaignRuntime(
        state_path=Path(state).expanduser() if state else None,
        longrun_root=Path(longrun_root).expanduser() if longrun_root else None,
        max_missions=max_missions,
        max_wall_seconds=max_wall_seconds,
    )


@campaign_app.command(name="status")
def campaign_status(
    state: str | None = None,
    longrun_root: str | None = None,
) -> None:
    runtime = _runtime(state=state, longrun_root=longrun_root)
    store = OrchestrationStateStore(runtime.state_path)
    snapshot = store.get()
    tasks = [
        row for row in snapshot.get("tasks", [])
        if row.get("preset") == "lilith-mission"
    ]
    eligible = runtime.next_eligible()
    payload: dict[str, Any] = {
        "schema_version": 1,
        "owner": runtime.owner,
        "missions": len(tasks),
        "pending": sum(row.get("status") == "pendiente" for row in tasks),
        "running": sum(row.get("status") == "delegada" for row in tasks),
        "blocked": sum(row.get("status") == "bloqueada" for row in tasks),
        "completed": sum(row.get("status") == "completada" for row in tasks),
        "failed": sum(row.get("status") == "fallida" for row in tasks),
        "next_task_id": eligible.get("id") if eligible else None,
        "budgets": aggregate_budget_status(store.path, task=eligible),
        "state_path": str(store.path),
        "longrun_root": str(runtime.longrun_root),
        "model_calls": 0,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


@campaign_app.command(name="recover")
def campaign_recover(state: str | None = None) -> None:
    store = OrchestrationStateStore(Path(state).expanduser() if state else None)
    resumed = store.resume_expired()
    print(json.dumps({
        "schema_version": 1,
        "resumed": len(resumed),
        "task_ids": [row.get("id") for row in resumed],
        "model_calls": 0,
    }, ensure_ascii=False, indent=2))


@campaign_app.command(name="run")
def campaign_run(
    state: str | None = None,
    longrun_root: str | None = None,
    max_missions: int = 5,
    max_wall_seconds: float = 14_400,
    heartbeat_seconds: float | None = None,
) -> None:
    runtime = _runtime(
        state=state,
        longrun_root=longrun_root,
        max_missions=max_missions,
        max_wall_seconds=max_wall_seconds,
    )
    result = runtime.run(heartbeat_seconds=heartbeat_seconds)
    payload = {"schema_version": 1, **asdict(result)}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if result.blocked or result.errors:
        raise SystemExit(2)
