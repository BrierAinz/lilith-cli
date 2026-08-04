"""Deterministic benchmark harness for the evidence-aware task router."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import Iterable


@dataclass(frozen=True)
class RoutingBenchmarkCase:
    name: str
    expected_preset: str
    complexity: float = 0.5
    risk: float = 0.5
    clarity: float = 0.5
    volume: float = 0.5
    description: str = ""
    tags: list[str] = field(default_factory=list)

    def as_task(self) -> dict[str, Any]:
        return {
            "id": f"benchmark:{self.name}",
            "title": self.name,
            "description": self.description,
            "routing": {
                "complexity": self.complexity,
                "risk": self.risk,
                "clarity": self.clarity,
                "volume": self.volume,
                "benchmark_tags": list(self.tags),
            },
        }


DEFAULT_ROUTING_CASES = (
    RoutingBenchmarkCase("respuesta breve", "quick", 0.1, 0.1, 0.9, 0.1),
    RoutingBenchmarkCase("cambio localizado", "generalista", 0.5, 0.4, 0.7, 0.5),
    RoutingBenchmarkCase("refactor multi paquete", "deep", 0.9, 0.7, 0.3, 0.9),
    RoutingBenchmarkCase("investigacion incierta", "deep", 0.8, 0.6, 0.1, 0.7),
    RoutingBenchmarkCase("validacion rutinaria", "quick", 0.2, 0.1, 0.9, 0.2),
)


def load_cases(path: str | Path) -> list[RoutingBenchmarkCase]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise TypeError("el benchmark debe ser una lista JSON")
    return [RoutingBenchmarkCase(**item) for item in raw]


def run_routing_benchmark(router: Any, cases: Iterable[RoutingBenchmarkCase]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for case in cases:
        decision = router._routing_decision(case.as_task())
        actual = str(decision["preset"])
        rows.append({
            **asdict(case),
            "actual_preset": actual,
            "passed": actual == case.expected_preset,
            "decision": decision,
        })
    passed = sum(1 for row in rows if row["passed"])
    total = len(rows)
    return {
        "total": total,
        "passed": passed,
        "accuracy": (passed / total) if total else 0.0,
        "cases": rows,
    }


__all__ = [
    "DEFAULT_ROUTING_CASES",
    "RoutingBenchmarkCase",
    "load_cases",
    "run_routing_benchmark",
]
