"""Persistent, process-safe orchestration plan state.

Decision: this is a dedicated JSON document under ``~/.yggdrasil`` rather
than a row in ``lilith-memory``. The recon found no canonical shared memory.db:
MemoryStore is an append-only conversation table with free-form metadata and
cannot atomically replace a typed plan/task state. A compact atomically-replaced
JSON document provides the required cross-session semantics without mixing
orchestration records into chat history.
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
}
TERMINAL_STATUSES = {"completada", "fallida"}
VALID_TRANSITIONS = {
    "pendiente": {"delegada", "bloqueada", "en_revision", "completada", "fallida"},
    "delegada": {"bloqueada", "en_revision", "completada", "fallida"},
    "bloqueada": {"pendiente", "delegada", "completada", "fallida"},
    "en_revision": {"delegada", "bloqueada", "completada", "fallida"},
    "completada": set(),
    "fallida": {"pendiente", "delegada"},
}
_LOCK = threading.RLock()


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def default_state_path() -> Path:
    override = os.environ.get("YGGDRASIL_ORCHESTRATION_STATE")
    return Path(override).expanduser() if override else Path.home() / ".yggdrasil" / "orchestration_state.json"


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
        with _LOCK:
            return self._read()

    def set_plan(self, name: str, description: str = "") -> dict[str, Any]:
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
        title = str(title).strip()
        if not title:
            raise ValueError("title es requerido")
        if status not in VALID_STATUSES:
            raise ValueError(f"estado inválido: {status}")
        with _LOCK:
            state = self._read()
            ident = str(task_id or uuid.uuid4().hex[:12]).strip()
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
            ):
                if key in metadata:
                    task[key] = metadata[key]
            task["updated_at"] = now_iso()
            if state.get("plan"):
                state["plan"]["updated_at"] = task["updated_at"]
            self._write(state)
            return task

    def append_post_mortem(self, entry: dict[str, Any]) -> dict[str, Any]:
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
        with _LOCK:
            current = state or self._read()
            costs = current.get("costs") or {}
            return {
                "session": (costs.get("sessions") or {}).get(session_id or "default", {"presets": {}, "providers": {}, "total": {}}),
                "historical": costs.get("historical") or {"presets": {}, "providers": {}, "total": {}},
            }

    def reset_costs(self) -> None:
        with _LOCK:
            state = self._read()
            state["costs"] = {"historical": {"presets": {}, "providers": {}, "total": {}}, "sessions": {}}
            self._write(state)

    def clear(self) -> None:
        with _LOCK:
            self._write(_empty_state())


@ToolRegistry.register
class OrchestrationStateTool(BaseTool):
    name = "orchestration_state"
    description = "Consultar o actualizar el plan persistente y sus tareas delegadas."
    parameters = {
        "action": {"type": "string", "required": True, "enum": ["get", "post_mortems", "add_task", "update_task", "set_plan", "clear"]},
        "name": {"type": "string", "required": False},
        "description": {"type": "string", "required": False},
        "title": {"type": "string", "required": False},
        "task_id": {"type": "string", "required": False},
        "status": {"type": "string", "required": False, "enum": sorted(VALID_STATUSES)},
        "preset": {"type": "string", "required": False},
        "result": {"type": "string", "required": False},
        "usage": {"type": "object", "required": False},
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        try:
            store = OrchestrationStateStore(kwargs.get("state_path"))
            action = str(kwargs.get("action", "")).strip().lower()
            if action == "get":
                return ToolResult(True, store.get())
            if action == "post_mortems":
                return ToolResult(True, {"post_mortems": store.get().get("post_mortems", [])})
            if action == "set_plan":
                return ToolResult(True, store.set_plan(kwargs.get("name", ""), kwargs.get("description", "")))
            if action == "add_task":
                task = store.add_task(
                    kwargs.get("title", ""), kwargs.get("description", ""),
                    task_id=kwargs.get("task_id"), status=kwargs.get("status", "pendiente"),
                    preset=kwargs.get("preset"),
                )
                return ToolResult(True, {"task": task})
            if action == "update_task":
                task = store.update_task(
                    str(kwargs.get("task_id", "")), status=kwargs.get("status"),
                    preset=kwargs.get("preset"), result=kwargs.get("result"), usage=kwargs.get("usage"),
                )
                return ToolResult(True, {"task": task})
            if action == "clear":
                store.clear()
                return ToolResult(True, {"cleared": True})
            return ToolResult(False, None, "action inválida")
        except (OSError, TypeError, ValueError) as exc:
            return ToolResult(False, None, str(exc))
