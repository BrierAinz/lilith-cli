"""Persistent task routing over the orchestration-state source of truth."""

from __future__ import annotations

import os
import time
import uuid
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
        evidence_weight: float = 0.25,
        min_evidence_samples: int = 3,
        worker_id: str | None = None,
        telemetry: Any = None,
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
        self.evidence_weight = max(0.0, min(0.75, float(evidence_weight)))
        self.min_evidence_samples = max(1, int(min_evidence_samples))
        self.worker_id = worker_id or f"router-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        if telemetry is None:
            try:
                from lilith_telemetry import get_collector

                telemetry = get_collector()
            except ImportError:
                telemetry = None
        self.telemetry = telemetry

    def _emit(self, event_type: str, task: dict[str, Any], **data: Any) -> None:
        if self.telemetry is None:
            return
        try:
            self.telemetry.emit(
                event_type,
                task_id=task.get("id"),
                correlation_id=task.get("correlation_id"),
                agent="lilith-router",
                status=task.get("status"),
                preset=task.get("preset"),
                **data,
            )
        except Exception:
            # Observability must never become a control-plane dependency.
            return

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
        success_criteria: list[str] | None = None,
        budget: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        routing = {
            "complexity": float(complexity), "risk": float(risk),
            "clarity": float(clarity), "volume": float(volume),
            "preferred_preset": preferred_preset,
        }
        task = self.store.add_task(
            title, description, task_id=task_id, dependencies=dependencies or [],
            max_retries=self.max_retries if max_retries is None else max_retries,
            routing=routing,
            success_criteria=success_criteria or [],
            budget=budget or {},
            idempotency_key=idempotency_key,
        )
        self._emit("task.submitted", task)
        return task

    def _dependencies_ready(self, task: dict[str, Any]) -> bool:
        tasks = {item["id"]: item for item in self.store.get()["tasks"]}
        return all(tasks.get(dep, {}).get("status") == "completada" for dep in task.get("dependencies", []))

    def _evidence(self) -> dict[str, dict[str, float]]:
        """Aggregate empirical performance from durable post-mortems."""
        buckets: dict[str, dict[str, float]] = {}
        for entry in self.store.get().get("post_mortems", []):
            preset = str(entry.get("preset") or "").strip()
            if not preset:
                continue
            bucket = buckets.setdefault(
                preset,
                {"samples": 0.0, "successes": 0.0, "quality": 0.0,
                 "tokens": 0.0, "latency_ms": 0.0},
            )
            success = bool(entry.get("success"))
            usage = entry.get("usage") or {}
            bucket["samples"] += 1
            bucket["successes"] += 1 if success else 0
            bucket["quality"] += float(entry.get("quality", 1.0 if success else 0.0))
            bucket["tokens"] += float(usage.get("total_tokens", 0) or 0)
            bucket["latency_ms"] += float(entry.get("latency_ms", 0) or 0)
        for bucket in buckets.values():
            samples = max(1.0, bucket["samples"])
            bucket["success_rate"] = (bucket["successes"] + 1.0) / (samples + 2.0)
            bucket["avg_quality"] = bucket["quality"] / samples
            bucket["avg_tokens"] = bucket["tokens"] / samples
            bucket["avg_latency_ms"] = bucket["latency_ms"] / samples
        return buckets

    def _routing_decision(self, task: dict[str, Any]) -> dict[str, Any]:
        routing = task.get("routing") or {}
        preferred = routing.get("preferred_preset")
        if preferred in self.routing_presets:
            return {
                "preset": str(preferred),
                "reason": "preferencia explicita",
                "base_score": None,
                "evidence": self._evidence().get(str(preferred), {}),
            }
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
        base = next((name for limit, name in choices if score <= limit), "generalista")
        evidence = self._evidence()
        eligible = {
            name: data for name, data in evidence.items()
            if name in self.routing_presets
            and data.get("samples", 0) >= self.min_evidence_samples
        }
        if not eligible:
            return {
                "preset": base,
                "reason": "heuristica; evidencia insuficiente",
                "base_score": round(score, 4),
                "evidence": evidence.get(base, {}),
            }

        # Preserve task-shape fit while allowing repeated real outcomes to
        # move the choice.  Candidate fit decays with distance from the
        # task's complexity score; empirical score blends success + quality.
        ranked: list[tuple[float, str, dict[str, float]]] = []
        max_score = max(4.0, max(float(v.get("max_score", 4.0)) for v in self.routing_presets.values()))
        for name, cfg in self.routing_presets.items():
            shape_fit = max(0.0, 1.0 - abs(score - float(cfg.get("max_score", 4.0))) / max_score)
            data = evidence.get(name, {})
            empirical = (
                0.65 * float(data.get("success_rate", 0.5))
                + 0.35 * float(data.get("avg_quality", 0.5))
            )
            weight = self.evidence_weight if data.get("samples", 0) >= self.min_evidence_samples else 0.0
            combined = (1.0 - weight) * shape_fit + weight * empirical
            ranked.append((combined, name, data))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        combined, chosen, data = ranked[0]
        return {
            "preset": chosen,
            "reason": "heuristica + resultados historicos",
            "base_preset": base,
            "base_score": round(score, 4),
            "combined_score": round(combined, 4),
            "evidence": data,
        }

    def _select_preset(self, task: dict[str, Any]) -> str:
        return str(self._routing_decision(task)["preset"])

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
        decision = self._routing_decision(task)
        merged_routing = {**(task.get("routing") or {}), "decision": decision}
        routed = self.store.update_task(
            task_id,
            status="delegada",
            preset=decision["preset"],
            routing=merged_routing,
        )
        self._emit("task.routed", routed, decision=decision)
        return routed

    @staticmethod
    def _budget_exceeded(
        task: dict[str, Any], *, elapsed: float = 0.0,
        usage: dict[str, Any] | None = None,
    ) -> str | None:
        budget = task.get("budget") or {}
        effective_usage = usage if usage is not None else (task.get("usage") or {})
        if budget.get("max_tokens") is not None:
            used = float(effective_usage.get("total_tokens", 0) or 0)
            if used >= float(budget["max_tokens"]):
                return f"presupuesto de tokens agotado ({used:g}/{budget['max_tokens']})"
        if budget.get("max_seconds") is not None and elapsed >= float(budget["max_seconds"]):
            return f"presupuesto de tiempo agotado ({elapsed:.2f}s/{budget['max_seconds']}s)"
        if budget.get("max_attempts") is not None:
            attempts = int(task.get("attempts", 0))
            if attempts >= int(budget["max_attempts"]):
                return f"presupuesto de intentos agotado ({attempts}/{budget['max_attempts']})"
        return None

    def dispatch(self, task_id: str) -> dict[str, Any]:
        self.resume()
        task = self._task(task_id)
        if task["status"] != "delegada":
            task = self.route(task_id)
        if task["status"] != "delegada" or self.executor is None:
            return task
        exceeded = self._budget_exceeded(task)
        if exceeded:
            return self._escalate(task_id, exceeded, terminal=True)
        task = self.store.claim_task(task_id, self.worker_id, lease_seconds=300)
        self.store.checkpoint_task(
            task_id,
            "before_execution",
            {"attempt": int(task.get("attempts", 0)) + 1, "preset": task.get("preset")},
        )
        started = time.perf_counter()
        try:
            result = self.executor(task)
        except Exception as exc:
            failed = self.report_failure(task_id, str(exc))
            if failed["status"] not in {"fallida", "cancelada"}:
                failed = self.store.release_task(
                    task_id, self.worker_id, status=failed["status"]
                )
            return failed
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if isinstance(result, dict):
            result_text = str(result.get("result") or result.get("summary") or "")
            usage = dict(result.get("usage") or {})
            verification = dict(result.get("verification") or {})
            quality = float(result.get("quality", 1.0 if verification.get("verified") else 0.5))
        else:
            result_text = str(result)
            usage = {}
            verification = {}
            quality = 0.5
        exceeded = self._budget_exceeded(
            task, elapsed=elapsed_ms / 1000, usage=usage
        )
        if exceeded:
            failed = self.report_failure(task_id, exceeded, usage=usage)
            if failed["status"] not in {"fallida", "cancelada"}:
                failed = self.store.release_task(
                    task_id, self.worker_id, status=failed["status"]
                )
            return failed
        reported = self.report_success(
            task_id,
            result=result_text,
            usage=usage,
            verification=verification,
            quality=quality,
            latency_ms=elapsed_ms,
        )
        if reported["status"] not in {"completada", "fallida", "cancelada"}:
            reported = self.store.release_task(
                task_id, self.worker_id, status=reported["status"]
            )
        return reported

    def tick(self) -> list[dict[str, Any]]:
        changed = []
        for task in self.store.get()["tasks"]:
            if task["status"] in {"pendiente", "bloqueada"} and self._dependencies_ready(task):
                changed.append(self.route(task["id"]))
        return changed

    def report_success(
        self, task_id: str, *, result: str = "", usage: dict[str, Any] | None = None,
        verification: dict[str, Any] | None = None,
        quality: float = 1.0,
        latency_ms: int = 0,
    ) -> dict[str, Any]:
        current = self._task(task_id)
        criteria = list(current.get("success_criteria") or [])
        verified = bool((verification or {}).get("verified"))
        target_status = "completada" if not criteria or verified else "en_revision"
        task = self.store.update_task(
            task_id,
            status=target_status,
            result=result,
            usage=usage,
            verification=verification or {
                "verified": not criteria,
                "reason": "sin criterios explicitos" if not criteria else "verificacion pendiente",
            },
        )
        if target_status == "completada":
            self._post_mortem(task, True, "", quality=quality, latency_ms=latency_ms)
        self._emit(
            "task.completed" if target_status == "completada" else "task.review_required",
            task,
            verification=task.get("verification"),
        )
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
            self._post_mortem(failed, False, cause, quality=0.0)
            self._emit("task.failed", failed, cause=cause)
            return self._task(task_id)
        blocked = self.store.update_task(
            task_id, status="bloqueada", result=cause, usage=usage, attempts=attempts,
        )
        self._emit("task.retry_scheduled", blocked, cause=cause, attempts=attempts)
        return blocked

    def verify(
        self,
        task_id: str,
        *,
        passed: bool,
        evidence: list[str] | None = None,
        summary: str = "",
        quality: float = 1.0,
    ) -> dict[str, Any]:
        task = self._task(task_id)
        if task["status"] not in {"en_revision", "delegada"}:
            raise ValueError("task no esta lista para verificacion")
        verification = {
            "verified": bool(passed),
            "evidence": list(evidence or []),
            "summary": summary,
            "verified_at": time.time(),
        }
        if not passed:
            failed = self.store.update_task(
                task_id, status="bloqueada", verification=verification,
                result=summary or "verificacion fallida",
            )
            self._emit("task.verification_failed", failed, evidence=evidence or [])
            return failed
        done = self.store.update_task(
            task_id, status="completada", verification=verification
        )
        self._post_mortem(done, True, "", quality=quality)
        self._emit("task.verified", done, evidence=evidence or [])
        return self._task(task_id)

    def checkpoint(
        self, task_id: str, label: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return self.store.checkpoint_task(task_id, label, payload or {})

    def cancel(self, task_id: str, reason: str = "") -> dict[str, Any]:
        task = self.store.cancel_task(task_id, reason)
        self._emit("task.cancelled", task, reason=reason)
        return task

    def resume(self) -> list[dict[str, Any]]:
        return self.store.resume_expired()

    def _post_mortem(
        self,
        task: dict[str, Any],
        success: bool,
        cause: str,
        *,
        quality: float = 0.0,
        latency_ms: int = 0,
    ) -> None:
        entry = {
            "task_id": task["id"], "preset": task.get("preset"),
            "turns": task.get("turns", 0), "usage": task.get("usage", {}),
            "success": success, "cause": cause,
            "quality": max(0.0, min(1.0, float(quality))),
            "latency_ms": max(0, int(latency_ms)),
            "correlation_id": task.get("correlation_id"),
            "routing": (task.get("routing") or {}).get("decision", {}),
        }
        self.store.append_post_mortem(entry)
        self.store.update_task(task["id"], post_mortem=entry)

    def status_summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        tasks = self.store.get()["tasks"]
        for task in tasks:
            counts[task["status"]] = counts.get(task["status"], 0) + 1
        state = self.store.get()
        return {
            "total": len(tasks),
            "por_estado": dict(sorted(counts.items())),
            "backend": state.get("backend", "json"),
            "revision": state.get("revision"),
            "event_count": state.get("event_count", 0),
            "active_leases": len(state.get("active_leases", [])),
        }
