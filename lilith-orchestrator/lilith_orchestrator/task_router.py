"""Persistent task routing over the orchestration-state source of truth."""

from __future__ import annotations

from typing import Any, Callable

DEFAULT_ROUTING_PRESETS: dict[str, dict[str, float]] = {
    "quick": {"max_score": 0.8},
    "generalista": {"max_score": 2.2},
    "deep": {"max_score": 4.0},
}


class TaskRouter:
    def __init__(
        self, *, store: Any = None, dispatcher: Any = None,
        policy_engine: Any = None,
        routing_presets: dict[str, dict[str, float]] | None = None,
        max_retries: int = 2, high_risk_threshold: float = 0.9,
        executor: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        if store is None:
            from lilith_tools.orchestration_state import OrchestrationStateStore
            store = OrchestrationStateStore()
        self.store = store
        self.dispatcher = dispatcher
        self.policy_engine = policy_engine
        self.routing_presets = routing_presets or DEFAULT_ROUTING_PRESETS
        self.max_retries = max_retries
        self.high_risk_threshold = high_risk_threshold
        self.executor = executor

    def _task(self, task_id: str) -> dict[str, Any]:
        task = next((t for t in self.store.get()["tasks"] if t.get("id") == task_id), None)
        if task is None:
            raise ValueError(f"task no encontrada: {task_id}")
        return task

    def submit(
        self, title: str, description: str = "", *, task_id: str | None = None,
        dependencies: list[str] | None = None, preferred_preset: str | None = None,
        complexity: float = 0.5, risk: float = 0.5, clarity: float = 0.5,
        volume: float = 0.5, max_retries: int | None = None,
    ) -> dict[str, Any]:
        routing = {
            "complexity": float(complexity), "risk": float(risk),
            "clarity": float(clarity), "volume": float(volume),
            "preferred_preset": preferred_preset,
        }
        return self.store.add_task(
            title, description, task_id=task_id, dependencies=dependencies or [],
            max_retries=self.max_retries if max_retries is None else max_retries,
            routing=routing,
        )

    def _dependencies_ready(self, task: dict[str, Any]) -> bool:
        tasks = {item["id"]: item for item in self.store.get()["tasks"]}
        return all(tasks.get(dep, {}).get("status") == "completada" for dep in task.get("dependencies", []))

    def _select_preset(self, task: dict[str, Any]) -> str:
        routing = task.get("routing") or {}
        preferred = routing.get("preferred_preset")
        if preferred in self.routing_presets:
            return str(preferred)
        score = sum((
            float(routing.get("complexity", 0.5)),
            float(routing.get("risk", 0.5)),
            1.0 - float(routing.get("clarity", 0.5)),
            float(routing.get("volume", 0.5)),
        ))
        choices = sorted(
            ((float(cfg.get("max_score", 4.0)), name) for name, cfg in self.routing_presets.items()),
            key=lambda item: item[0],
        )
        return next((name for limit, name in choices if score <= limit), "generalista")

    def _escalate(self, task_id: str, reason: str, *, terminal: bool = False) -> dict[str, Any]:
        return self.store.update_task(
            task_id, status="fallida" if terminal else "en_revision",
            escalation={"target": "usuario", "reason": reason},
        )

    def route(self, task_id: str) -> dict[str, Any]:
        task = self._task(task_id)
        if not self._dependencies_ready(task):
            if task["status"] == "pendiente":
                return self.store.update_task(task_id, status="bloqueada")
            return task
        risk = float((task.get("routing") or {}).get("risk", 0.5))
        if risk >= self.high_risk_threshold:
            return self._escalate(task_id, "riesgo alto")
        return self.store.update_task(task_id, status="delegada", preset=self._select_preset(task))

    def dispatch(self, task_id: str) -> dict[str, Any]:
        task = self._task(task_id)
        if task["status"] != "delegada":
            task = self.route(task_id)
        if task["status"] != "delegada" or self.executor is None:
            return task
        try:
            result = self.executor(task)
        except Exception as exc:
            return self.report_failure(task_id, str(exc))
        return self.report_success(task_id, result=str(result))

    def tick(self) -> list[dict[str, Any]]:
        changed = []
        for task in self.store.get()["tasks"]:
            if task["status"] in {"pendiente", "bloqueada"} and self._dependencies_ready(task):
                changed.append(self.route(task["id"]))
        return changed

    def report_success(
        self, task_id: str, *, result: str = "", usage: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        task = self.store.update_task(task_id, status="completada", result=result, usage=usage)
        self._post_mortem(task, True, "")
        return self._task(task_id)

    def report_failure(self, task_id: str, cause: str, *, usage: dict[str, Any] | None = None) -> dict[str, Any]:
        task = self._task(task_id)
        attempts = int(task.get("attempts", 0)) + 1
        max_retries = int(task.get("max_retries", self.max_retries))
        if attempts > max_retries:
            failed = self.store.update_task(
                task_id, status="fallida", result=cause, usage=usage, attempts=attempts,
                escalation={"target": "usuario", "reason": "reintentos agotados"},
            )
            self._post_mortem(failed, False, cause)
            return self._task(task_id)
        return self.store.update_task(
            task_id, status="bloqueada", result=cause, usage=usage, attempts=attempts,
        )

    def _post_mortem(self, task: dict[str, Any], success: bool, cause: str) -> None:
        entry = {
            "task_id": task["id"], "preset": task.get("preset"),
            "turns": task.get("turns", 0), "usage": task.get("usage", {}),
            "success": success, "cause": cause,
        }
        self.store.append_post_mortem(entry)
        self.store.update_task(task["id"], post_mortem=entry)

    def status_summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        tasks = self.store.get()["tasks"]
        for task in tasks:
            counts[task["status"]] = counts.get(task["status"], 0) + 1
        return {"total": len(tasks), "por_estado": dict(sorted(counts.items()))}
