"""Tests for the persisted task router state machine."""

from __future__ import annotations

from pathlib import Path


def _router(tmp_path: Path, **kwargs):
    from lilith_orchestrator.task_router import TaskRouter
    from lilith_tools.orchestration_state import OrchestrationStateStore

    store = OrchestrationStateStore(tmp_path / "state.json")
    return TaskRouter(store=store, **kwargs), store


def test_router_routes_dependencies_and_completes(tmp_path: Path) -> None:
    router, store = _router(tmp_path)
    first = router.submit("Prepare inputs", task_id="prepare")
    second = router.submit(
        "Implement broad risky ambiguous migration",
        task_id="implement",
        dependencies=["prepare"],
        complexity=0.9,
        risk=0.8,
        clarity=0.2,
        volume=0.9,
    )

    assert first["status"] == "pendiente"
    assert router.route("implement")["status"] == "bloqueada"

    router.route("prepare")
    router.dispatch("prepare")
    router.report_success("prepare", result="ready", usage={"total_tokens": 4})

    routed = router.tick()[0]
    assert routed["id"] == "implement"
    assert routed["status"] == "delegada"
    assert routed["preset"] == "deep"
    assert store.get()["tasks"][1]["status"] == "delegada"


def test_router_escalates_high_risk_or_exhausted_retries(tmp_path: Path) -> None:
    router, _ = _router(tmp_path, max_retries=1)
    router.submit("Delete production", task_id="risk", risk=0.95)
    escalated = router.route("risk")
    assert escalated["status"] == "en_revision"
    assert escalated["escalation"]["target"] == "usuario"

    router.submit("Flaky task", task_id="flaky", risk=0.1)
    router.route("flaky")
    router.report_failure("flaky", "first failure")
    retry = router.tick()[0]
    assert retry["status"] == "delegada"
    failed = router.report_failure("flaky", "second failure")
    assert failed["status"] == "fallida"
    assert failed["escalation"]["reason"] == "reintentos agotados"


def test_router_uses_generalist_fallback_for_unknown_preset(tmp_path: Path) -> None:
    router, _ = _router(
        tmp_path,
        routing_presets={
            "generalista": {"max_score": 4.0},
            "quick": {"max_score": 0.5},
        },
    )
    router.submit("Normal task", task_id="normal", preferred_preset="missing")
    assert router.route("normal")["preset"] == "generalista"


def test_status_summary_counts_persisted_states(tmp_path: Path) -> None:
    router, _ = _router(tmp_path)
    router.submit("One", task_id="one")
    router.submit("Two", task_id="two")
    router.route("one")
    summary = router.status_summary()
    assert summary["total"] == 2
    assert summary["por_estado"] == {"delegada": 1, "pendiente": 1}
