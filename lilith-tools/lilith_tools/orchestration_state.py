"""Persistent orchestration plan state.

Production uses a transactional SQLite backend.  Explicit ``*.json`` paths are
kept as a compatibility backend for old integrations and fixtures; the first
production SQLite open imports the former JSON snapshot without deleting it.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .base import BaseTool, ToolResult
from .registry import ToolRegistry

VALID_STATUSES = {
    "pendiente",
    "delegada",
    "bloqueada",
    "en_revision",
    "completada",
    "fallida",
    "cancelada",
}
TERMINAL_STATUSES = {"completada", "fallida", "cancelada"}
VALID_TRANSITIONS = {
    "pendiente": {"delegada", "bloqueada", "en_revision", "completada", "fallida", "cancelada"},
    "delegada": {"pendiente", "bloqueada", "en_revision", "completada", "fallida", "cancelada"},
    "bloqueada": {"pendiente", "delegada", "completada", "fallida", "cancelada"},
    "en_revision": {"pendiente", "delegada", "bloqueada", "completada", "fallida", "cancelada"},
    "completada": set(),
    "fallida": {"pendiente", "delegada"},
    "cancelada": {"pendiente"},
}
_LOCK = threading.RLock()


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def default_state_path() -> Path:
    override = os.environ.get("YGGDRASIL_ORCHESTRATION_STATE")
    return Path(override).expanduser() if override else Path.home() / ".yggdrasil" / "orchestration_state.sqlite3"


def legacy_state_path() -> Path:
    return Path.home() / ".yggdrasil" / "orchestration_state.json"


def _empty_state() -> dict[str, Any]:
    return {
        "version": 2,
        "plan": None,
        "tasks": [],
        "costs": {"historical": {"presets": {}, "providers": {}, "total": {}}},
        "post_mortems": [],
    }


class OrchestrationStateStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path).expanduser() if path else default_state_path()
        self._sqlite = None
        if self.path.suffix.lower() != ".json":
            from .orchestration_sqlite import SQLiteOrchestrationBackend

            self._sqlite = SQLiteOrchestrationBackend(
                self.path,
                legacy_json=legacy_state_path() if path is None else None,
            )

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return _empty_state()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"estado de orquestación inválido: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("tasks", []), list):
            raise ValueError("estado de orquestación inválido")
        data.setdefault("version", 1)
        data.setdefault("plan", None)
        data.setdefault("tasks", [])
        data.setdefault("costs", {"historical": {"presets": {}, "providers": {}, "total": {}}})
        data.setdefault("post_mortems", [])
        return data

    def _write(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, self.path)

    def get(self) -> dict[str, Any]:
        if self._sqlite is not None:
            return self._sqlite.get()
        with _LOCK:
            return self._read()

    def set_plan(self, name: str, description: str = "") -> dict[str, Any]:
        if self._sqlite is not None:
            return self._sqlite.set_plan(name, description)
        name = str(name).strip()
        if not name:
            raise ValueError("name es requerido")
        with _LOCK:
            state = self._read()
            stamp = now_iso()
            state["plan"] = {
                "name": name,
                "description": str(description).strip(),
                "created_at": stamp,
                "updated_at": stamp,
            }
            self._write(state)
            return state

    def add_task(
        self,
        title: str,
        description: str = "",
        *,
        task_id: str | None = None,
        status: str = "pendiente",
        preset: str | None = None,
        **metadata: Any,
    ) -> dict[str, Any]:
        if self._sqlite is not None:
            return self._sqlite.add_task(
                title,
                description,
                task_id=task_id,
                status=status,
                preset=preset,
                **metadata,
            )
        title = str(title).strip()
        if not title:
            raise ValueError("title es requerido")
        if status not in VALID_STATUSES:
            raise ValueError(f"estado inválido: {status}")
        with _LOCK:
            state = self._read()
            ident = str(task_id or uuid.uuid4().hex[:12]).strip()
            idempotency_key = str(metadata.get("idempotency_key") or "").strip() or None
            if idempotency_key:
                existing = next(
                    (
                        item for item in state["tasks"]
                        if item.get("idempotency_key") == idempotency_key
                    ),
                    None,
                )
                if existing is not None:
                    return existing
            if any(task.get("id") == ident for task in state["tasks"]):
                raise ValueError(f"task id duplicado: {ident}")
            stamp = now_iso()
            task = {
                "id": ident,
                "title": title,
                "description": str(description).strip(),
                "status": status,
                "preset": str(preset).strip() if preset else None,
                "result": None,
                "created_at": stamp,
                "updated_at": stamp,
                "started_at": stamp if status == "delegada" else None,
                "completed_at": stamp if status in TERMINAL_STATUSES else None,
                "usage": {},
                "dependencies": list(metadata.get("dependencies") or []),
                "attempts": int(metadata.get("attempts", 0)),
                "max_retries": int(metadata.get("max_retries", 2)),
                "routing": dict(metadata.get("routing") or {}),
                "escalation": metadata.get("escalation"),
                "turns": int(metadata.get("turns", 0)),
                "success_criteria": list(metadata.get("success_criteria") or []),
                "budget": dict(metadata.get("budget") or {}),
                "verification": dict(metadata.get("verification") or {}),
                "correlation_id": str(metadata.get("correlation_id") or uuid.uuid4().hex),
                "trace_id": metadata.get("trace_id"),
                "idempotency_key": idempotency_key,
                "checkpoints": [],
            }
            state["tasks"].append(task)
            if state.get("plan"):
                state["plan"]["updated_at"] = stamp
            self._write(state)
            return task

    def update_task(
        self,
        task_id: str,
        *,
        status: str | None = None,
        preset: str | None = None,
        result: str | None = None,
        usage: dict[str, Any] | None = None,
        **metadata: Any,
    ) -> dict[str, Any]:
        if self._sqlite is not None:
            return self._sqlite.update_task(
                task_id,
                status=status,
                preset=preset,
                result=result,
                usage=usage,
                **metadata,
            )
        with _LOCK:
            state = self._read()
            task = next((item for item in state["tasks"] if item.get("id") == task_id), None)
            if task is None:
                raise ValueError(f"task no encontrada: {task_id}")
            if status is not None:
                if status not in VALID_STATUSES:
                    raise ValueError(f"estado inválido: {status}")
                old_status = task["status"]
                if status != old_status and status not in VALID_TRANSITIONS[old_status]:
                    raise ValueError(f"transición inválida: {old_status} -> {status}")
                task["status"] = status
                if status == "delegada" and not task.get("started_at"):
                    task["started_at"] = now_iso()
                if status in TERMINAL_STATUSES:
                    task["completed_at"] = now_iso()
            if preset is not None:
                task["preset"] = str(preset).strip() or None
            if result is not None:
                task["result"] = str(result).strip()
            if usage:
                accumulated = dict(task.get("usage") or {})
                for key, value in usage.items():
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        accumulated[key] = accumulated.get(key, 0) + value
                task["usage"] = accumulated
            for key in (
                "dependencies", "attempts", "max_retries", "routing",
                "escalation", "turns", "provider", "post_mortem",
                "success_criteria", "budget", "verification", "trace_id",
                "correlation_id", "lease_owner", "lease_expires_at",
                "last_checkpoint", "idempotency_key",
            ):
                if key in metadata:
                    task[key] = metadata[key]
            task["updated_at"] = now_iso()
            if state.get("plan"):
                state["plan"]["updated_at"] = task["updated_at"]
            self._write(state)
            return task

    def append_post_mortem(self, entry: dict[str, Any]) -> dict[str, Any]:
        if self._sqlite is not None:
            return self._sqlite.append_post_mortem(entry)
        with _LOCK:
            state = self._read()
            record = {"created_at": now_iso(), **entry}
            state["post_mortems"].append(record)
            self._write(state)
            return record

    @staticmethod
    def _add_usage(bucket: dict[str, Any], usage: dict[str, Any]) -> None:
        for key in ("prompt_tokens", "completion_tokens"):
            value = usage.get(key, 0)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                bucket[key] = bucket.get(key, 0) + value
        bucket["calls"] = bucket.get("calls", 0) + 1

    def record_cost(
        self, preset: str, provider: str, usage: dict[str, Any], *, session_id: str,
    ) -> dict[str, Any]:
        if self._sqlite is not None:
            return self._sqlite.record_cost(
                preset, provider, usage, session_id=session_id
            )
        with _LOCK:
            state = self._read()
            costs = state.setdefault("costs", {})
            historical = costs.setdefault("historical", {"presets": {}, "providers": {}, "total": {}})
            sessions = costs.setdefault("sessions", {})
            session = sessions.setdefault(session_id or "default", {"presets": {}, "providers": {}, "total": {}})
            for scope, key in (("presets", preset or "unknown"), ("providers", provider or "unknown")):
                self._add_usage(historical[scope].setdefault(key, {}), usage)
                self._add_usage(session[scope].setdefault(key, {}), usage)
            self._add_usage(historical.setdefault("total", {}), usage)
            self._add_usage(session.setdefault("total", {}), usage)
            self._write(state)
            return self.cost_summary(session_id, state=state)

    def cost_summary(self, session_id: str = "", *, state: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._sqlite is not None:
            return self._sqlite.cost_summary(session_id)
        with _LOCK:
            current = state or self._read()
            costs = current.get("costs") or {}
            return {
                "session": (costs.get("sessions") or {}).get(session_id or "default", {"presets": {}, "providers": {}, "total": {}}),
                "historical": costs.get("historical") or {"presets": {}, "providers": {}, "total": {}},
            }

    def reset_costs(self) -> None:
        if self._sqlite is not None:
            self._sqlite.reset_costs()
            return
        with _LOCK:
            state = self._read()
            state["costs"] = {"historical": {"presets": {}, "providers": {}, "total": {}}, "sessions": {}}
            self._write(state)

    def clear(self) -> None:
        if self._sqlite is not None:
            self._sqlite.clear()
            return
        with _LOCK:
            self._write(_empty_state())

    def claim_task(
        self, task_id: str, owner: str, *, lease_seconds: float = 300
    ) -> dict[str, Any]:
        if self._sqlite is None:
            return self.update_task(
                task_id,
                status="delegada",
                lease_owner=owner,
                lease_expires_at=datetime.now(UTC).timestamp() + lease_seconds,
            )
        return self._sqlite.claim_task(task_id, owner, lease_seconds=lease_seconds)

    def renew_lease(
        self, task_id: str, owner: str, *, lease_seconds: float = 300
    ) -> dict[str, Any]:
        if self._sqlite is None:
            return self.update_task(
                task_id,
                lease_owner=owner,
                lease_expires_at=datetime.now(UTC).timestamp() + lease_seconds,
            )
        return self._sqlite.renew_lease(task_id, owner, lease_seconds=lease_seconds)

    def release_task(
        self, task_id: str, owner: str, *, status: str = "pendiente"
    ) -> dict[str, Any]:
        if self._sqlite is None:
            return self.update_task(
                task_id, status=status, lease_owner=None, lease_expires_at=None
            )
        return self._sqlite.release_task(task_id, owner, status=status)

    def checkpoint_task(
        self, task_id: str, label: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if self._sqlite is None:
            checkpoint = {
                "id": uuid.uuid4().hex,
                "task_id": task_id,
                "label": label,
                "payload": dict(payload or {}),
                "created_at": now_iso(),
            }
            self.update_task(task_id, last_checkpoint=checkpoint)
            return checkpoint
        return self._sqlite.checkpoint_task(task_id, label, payload)

    def checkpoints(self, task_id: str) -> list[dict[str, Any]]:
        if self._sqlite is None:
            task = next(
                (item for item in self.get()["tasks"] if item.get("id") == task_id),
                None,
            )
            return [task["last_checkpoint"]] if task and task.get("last_checkpoint") else []
        return self._sqlite.checkpoints(task_id)

    def cancel_task(self, task_id: str, reason: str = "") -> dict[str, Any]:
        if self._sqlite is None:
            return self.update_task(
                task_id,
                status="cancelada",
                result=reason or "cancelada por operador",
                verification={"verified": False, "reason": "cancelled"},
            )
        return self._sqlite.cancel_task(task_id, reason)

    def resume_expired(self) -> list[dict[str, Any]]:
        if self._sqlite is None:
            return []
        return self._sqlite.resume_expired()

    def events(
        self, *, limit: int = 100, task_id: str | None = None
    ) -> list[dict[str, Any]]:
        if self._sqlite is None:
            return []
        return self._sqlite.events(limit=limit, task_id=task_id)


@ToolRegistry.register
class OrchestrationStateTool(BaseTool):
    name = "orchestration_state"
    description = "Consultar o actualizar el plan persistente y sus tareas delegadas."
    parameters = {
        "action": {"type": "string", "required": True, "enum": [
            "get", "post_mortems", "events", "add_task", "update_task",
            "set_plan", "claim", "renew", "release", "checkpoint",
            "checkpoints", "resume_expired", "cancel", "clear",
        ]},
        "name": {"type": "string", "required": False},
        "description": {"type": "string", "required": False},
        "title": {"type": "string", "required": False},
        "task_id": {"type": "string", "required": False},
        "status": {"type": "string", "required": False, "enum": sorted(VALID_STATUSES)},
        "preset": {"type": "string", "required": False},
        "result": {"type": "string", "required": False},
        "usage": {"type": "object", "required": False},
        "owner": {"type": "string", "required": False},
        "lease_seconds": {"type": "number", "required": False},
        "label": {"type": "string", "required": False},
        "payload": {"type": "object", "required": False},
        "limit": {"type": "integer", "required": False},
        "reason": {"type": "string", "required": False},
        "success_criteria": {"type": "array", "required": False},
        "budget": {"type": "object", "required": False},
        "idempotency_key": {"type": "string", "required": False},
        "verification": {"type": "object", "required": False},
        "correlation_id": {"type": "string", "required": False},
        "trace_id": {"type": "string", "required": False},
        "state_path": {"type": "string", "required": False},
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        try:
            store = OrchestrationStateStore(kwargs.get("state_path"))
            action = str(kwargs.get("action", "")).strip().lower()
            if action == "get":
                return ToolResult(True, store.get())
            if action == "post_mortems":
                return ToolResult(True, {"post_mortems": store.get().get("post_mortems", [])})
            if action == "events":
                return ToolResult(True, {"events": store.events(
                    limit=int(kwargs.get("limit", 100)),
                    task_id=kwargs.get("task_id"),
                )})
            if action == "set_plan":
                return ToolResult(True, store.set_plan(kwargs.get("name", ""), kwargs.get("description", "")))
            if action == "add_task":
                task = store.add_task(
                    kwargs.get("title", ""), kwargs.get("description", ""),
                    task_id=kwargs.get("task_id"), status=kwargs.get("status", "pendiente"),
                    preset=kwargs.get("preset"),
                    success_criteria=kwargs.get("success_criteria") or [],
                    budget=kwargs.get("budget") or {},
                    idempotency_key=kwargs.get("idempotency_key"),
                    correlation_id=kwargs.get("correlation_id"),
                    trace_id=kwargs.get("trace_id"),
                )
                return ToolResult(True, {"task": task})
            if action == "claim":
                return ToolResult(True, {"task": store.claim_task(
                    str(kwargs.get("task_id", "")),
                    str(kwargs.get("owner", "")),
                    lease_seconds=float(kwargs.get("lease_seconds", 300)),
                )})
            if action == "renew":
                return ToolResult(True, {"task": store.renew_lease(
                    str(kwargs.get("task_id", "")),
                    str(kwargs.get("owner", "")),
                    lease_seconds=float(kwargs.get("lease_seconds", 300)),
                )})
            if action == "release":
                return ToolResult(True, {"task": store.release_task(
                    str(kwargs.get("task_id", "")),
                    str(kwargs.get("owner", "")),
                    status=str(kwargs.get("status", "pendiente")),
                )})
            if action == "checkpoint":
                return ToolResult(True, {"checkpoint": store.checkpoint_task(
                    str(kwargs.get("task_id", "")),
                    str(kwargs.get("label", "")),
                    kwargs.get("payload") or {},
                )})
            if action == "checkpoints":
                return ToolResult(True, {"checkpoints": store.checkpoints(
                    str(kwargs.get("task_id", ""))
                )})
            if action == "resume_expired":
                return ToolResult(True, {"resumed": store.resume_expired()})
            if action == "cancel":
                return ToolResult(True, {"task": store.cancel_task(
                    str(kwargs.get("task_id", "")),
                    str(kwargs.get("reason", "")),
                )})
            if action == "update_task":
                update_metadata = {
                    key: kwargs[key]
                    for key in ("success_criteria", "budget", "verification")
                    if kwargs.get(key) is not None
                }
                task = store.update_task(
                    str(kwargs.get("task_id", "")), status=kwargs.get("status"),
                    preset=kwargs.get("preset"), result=kwargs.get("result"), usage=kwargs.get("usage"),
                    **update_metadata,
                )
                return ToolResult(True, {"task": task})
            if action == "clear":
                store.clear()
                return ToolResult(True, {"cleared": True})
            return ToolResult(False, None, "action inválida")
        except (OSError, TypeError, ValueError) as exc:
            return ToolResult(False, None, str(exc))
