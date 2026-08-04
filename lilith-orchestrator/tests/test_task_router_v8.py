from __future__ import annotations

import sqlite3
from pathlib import Path

from lilith_orchestrator.task_router import TaskRouter
from lilith_tools.orchestration_state import OrchestrationStateStore


def _store(tmp_path: Path) -> OrchestrationStateStore:
    return OrchestrationStateStore(tmp_path / "orchestration.sqlite3")


def test_success_criteria_gate_completion_until_verified(tmp_path: Path) -> None:
    store = _store(tmp_path)
    router = TaskRouter(
        store=store,
        worker_id="test-worker",
        executor=lambda task: {
            "result": "implemented",
            "usage": {"total_tokens": 10},
            "verification": {"verified": False, "evidence": []},
        },
    )
    task = router.submit(
        "ship feature", success_criteria=["tests pass"], preferred_preset="quick"
    )
    pending_review = router.dispatch(task["id"])
    assert pending_review["status"] == "en_revision"
    assert store.get()["post_mortems"] == []
    assert store.get()["active_leases"] == []

    done = router.verify(
        task["id"], passed=True, evidence=["pytest: 12 passed"], summary="green"
    )
    assert done["status"] == "completada"
    assert done["verification"]["verified"] is True
    assert store.get()["post_mortems"][0]["success"] is True


def test_empirical_outcomes_can_override_heuristic_choice(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for index in range(4):
        store.append_post_mortem({
            "task_id": f"quick-{index}", "preset": "quick", "success": True,
            "quality": 1.0, "usage": {"total_tokens": 10}, "latency_ms": 10,
        })
        store.append_post_mortem({
            "task_id": f"general-{index}", "preset": "generalista", "success": False,
            "quality": 0.0, "usage": {"total_tokens": 100}, "latency_ms": 100,
        })
    router = TaskRouter(store=store, evidence_weight=0.75)
    task = router.submit(
        "medium task", complexity=0.5, risk=0.5, clarity=0.5, volume=0.5
    )
    routed = router.route(task["id"])
    decision = routed["routing"]["decision"]
    assert decision["base_preset"] == "generalista"
    assert routed["preset"] == "quick"
    assert decision["reason"] == "heuristica + resultados historicos"


def test_budget_is_enforced_before_executor_runs(tmp_path: Path) -> None:
    store = _store(tmp_path)
    called = []
    router = TaskRouter(store=store, executor=lambda task: called.append(task))
    task = router.submit("bounded", budget={"max_tokens": 1})
    store.update_task(task["id"], usage={"total_tokens": 1})
    result = router.dispatch(task["id"])
    assert result["status"] == "fallida"
    assert called == []
    assert "presupuesto" in result["escalation"]["reason"]


def test_post_execution_budget_uses_new_usage_and_releases_lease(tmp_path: Path) -> None:
    store = _store(tmp_path)
    router = TaskRouter(
        store=store,
        worker_id="budget-worker",
        executor=lambda task: {"result": "large", "usage": {"total_tokens": 11}},
    )
    task = router.submit("bounded", budget={"max_tokens": 10})

    result = router.dispatch(task["id"])

    assert result["status"] == "bloqueada"
    assert result["usage"]["total_tokens"] == 11
    assert store.get()["active_leases"] == []
    assert "presupuesto" in result["result"]


def test_resume_recovers_task_after_worker_lease_expires(tmp_path: Path) -> None:
    store = _store(tmp_path)
    router = TaskRouter(store=store, worker_id="worker-a")
    task = router.submit("resumable")
    router.route(task["id"])
    store.claim_task(task["id"], "worker-a", lease_seconds=30)
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE task_leases SET expires_at = 0")
    resumed = router.resume()
    assert [item["id"] for item in resumed] == [task["id"]]
    assert router._task(task["id"])["status"] == "pendiente"
