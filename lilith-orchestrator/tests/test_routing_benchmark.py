from __future__ import annotations

import json
from pathlib import Path

from lilith_orchestrator.routing_benchmark import (
    DEFAULT_ROUTING_CASES,
    load_cases,
    run_routing_benchmark,
)
from lilith_orchestrator.task_router import TaskRouter
from lilith_tools.orchestration_state import OrchestrationStateStore


def test_default_benchmark_is_reproducible(tmp_path: Path) -> None:
    router = TaskRouter(
        store=OrchestrationStateStore(tmp_path / "state.sqlite3"),
        evidence_weight=0,
    )
    first = run_routing_benchmark(router, DEFAULT_ROUTING_CASES)
    second = run_routing_benchmark(router, DEFAULT_ROUTING_CASES)
    assert first == second
    assert first["total"] == 5
    assert 0 <= first["accuracy"] <= 1
    assert all("decision" in case for case in first["cases"])


def test_load_custom_cases(tmp_path: Path) -> None:
    path = tmp_path / "cases.json"
    path.write_text(json.dumps([{
        "name": "tiny",
        "expected_preset": "quick",
        "complexity": 0.1,
        "risk": 0.1,
        "clarity": 1.0,
        "volume": 0.1,
    }]), encoding="utf-8")
    cases = load_cases(path)
    assert cases[0].name == "tiny"
    assert cases[0].expected_preset == "quick"
