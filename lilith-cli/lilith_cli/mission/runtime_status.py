from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from lilith_tools.orchestration_state import OrchestrationStateStore

from .budget_guard import AggregateBudgetGuard, campaign_id_for_task
from .runtime import AutonomousCampaignRuntime

_TASK_NAME = "Lilith-Campaign-Runtime"
_TASK_STATES = {
    0: "unknown",
    1: "disabled",
    2: "queued",
    3: "ready",
    4: "running",
}


def _scheduled_task_status() -> dict[str, Any]:
    if os.name != "nt":
        return {"installed": False, "reason": "windows-only"}
    try:
        import win32com.client

        service = win32com.client.Dispatch("Schedule.Service")
        service.Connect()
        task = service.GetFolder("\\").GetTask(_TASK_NAME)
        definition = task.Definition
        return {
            "installed": True,
            "task_name": _TASK_NAME,
            "enabled": bool(definition.Settings.Enabled),
            "state": _TASK_STATES.get(int(task.State), str(task.State)),
            "last_run_time": str(task.LastRunTime),
            "last_result": int(task.LastTaskResult),
            "next_run_time": str(task.NextRunTime),
        }
    except Exception as exc:  # noqa: BLE001 - optional OS integration boundary
        return {
            "installed": False,
            "task_name": _TASK_NAME,
            "reason": type(exc).__name__,
        }


def aggregate_budget_status(
    state_path: str | Path,
    *,
    task: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from ..config import load_config

    try:
        cfg = load_config()
        policy = cfg.campaign_budgets
        guard = AggregateBudgetGuard(policy=policy, state_path=str(state_path))
        campaign_id = (
            campaign_id_for_task(OrchestrationStateStore(state_path), task)
            if task is not None
            else ""
        )
        provider = str(cfg.provider or "")
        decision = guard.check(campaign_id=campaign_id, provider=provider)
        return {
            "enabled": bool(policy.enabled),
            "limits": policy.model_dump(),
            "usage": guard.snapshot(campaign_id=campaign_id, provider=provider),
            "preflight": decision.as_dict(),
        }
    except (OSError, TypeError, ValueError) as exc:
        return {"enabled": False, "error": f"{type(exc).__name__}: {exc}"}


def runtime_status() -> dict[str, Any]:
    runtime = AutonomousCampaignRuntime()
    store = OrchestrationStateStore(runtime.state_path)
    state = store.get()
    missions = [
        row for row in state.get("tasks", [])
        if row.get("preset") == "lilith-mission"
    ]
    next_task = runtime.next_eligible()
    log = Path.home() / ".yggdrasil" / "logs" / "campaign-runtime.log"
    return {
        "schema_version": 1,
        "scheduler": _scheduled_task_status(),
        "queue": {
            "missions": len(missions),
            "pending": sum(row.get("status") == "pendiente" for row in missions),
            "running": sum(row.get("status") == "delegada" for row in missions),
            "blocked": sum(row.get("status") == "bloqueada" for row in missions),
            "completed": sum(row.get("status") == "completada" for row in missions),
            "failed": sum(row.get("status") == "fallida" for row in missions),
            "next_task_id": next_task.get("id") if next_task else None,
        },
        "budgets": aggregate_budget_status(
            runtime.state_path, task=next_task
        ),
        "state_path": str(runtime.state_path),
        "longrun_root": str(runtime.longrun_root),
        "log_path": str(log),
        "model_calls": 0,
    }
