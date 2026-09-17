from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lilith_tools.orchestration_state import OrchestrationStateStore

_REPORT_STATUSES = {"completada", "fallida", "bloqueada", "cancelada"}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class MissionReport:
    task_id: str
    mission_id: str
    objective: str
    status: str
    updated_at: str
    acknowledged: bool
    debrief: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class MissionInbox:
    """Durable mission reports derived from the canonical orchestration state."""

    def __init__(self, state_path: str | Path | None = None) -> None:
        self.state_path = Path(state_path).expanduser() if state_path else None
    def _store(self) -> OrchestrationStateStore:
        return OrchestrationStateStore(self.state_path)

    @staticmethod
    def _is_reportable(task: dict[str, Any]) -> bool:
        return (
            task.get("preset") == "lilith-mission"
            and str(task.get("status") or "") in _REPORT_STATUSES
        )

    @staticmethod
    def _acked(task: dict[str, Any]) -> bool:
        routing = task.get("routing") or {}
        inbox = routing.get("inbox") or {}
        return bool(inbox.get("acked_at"))

    @staticmethod
    def _children(state: dict[str, Any], task_id: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for task in state.get("tasks", []):
            spec = ((task.get("routing") or {}).get("mission_spec") or {})
            meta = spec.get("metadata") or {}
            if str(meta.get("parent_task_id") or "") == task_id:
                rows.append(task)
        rows.sort(key=lambda row: str(row.get("updated_at") or ""), reverse=True)
        return rows

    @staticmethod
    def _post_mortems(state: dict[str, Any], task_id: str) -> list[dict[str, Any]]:
        return [
            row for row in state.get("post_mortems", [])
            if str(row.get("task_id") or "") == task_id
        ]
    @classmethod
    def _debrief(cls, state: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
        routing = task.get("routing") or {}
        team = routing.get("team") or []
        compute = routing.get("compute") or {}
        verification = task.get("verification") or {}
        post_mortems = cls._post_mortems(state, str(task.get("id") or ""))
        lessons: list[str] = []
        for row in post_mortems:
            for lesson in row.get("lessons") or []:
                text = str(lesson).strip()
                if text and text not in lessons:
                    lessons.append(text)
        children = cls._children(state, str(task.get("id") or ""))
        return {
            "result": str(task.get("result") or ""),
            "verification": verification,
            "usage": dict(task.get("usage") or {}),
            "court": [
                str(row.get("display_name") or row.get("agent_id") or "")
                for row in team if isinstance(row, dict)
            ],
            "compute": {
                str(agent): str(route.get("resource_id") or "")
                for agent, route in compute.items() if isinstance(route, dict)
            },
            "journal_compaction": dict(routing.get("journal_compaction") or {}),
            "lessons": lessons,
            "children": [
                {
                    "task_id": child.get("id"),
                    "objective": child.get("title"),
                    "status": child.get("status"),
                    "reason": (
                        (((child.get("routing") or {}).get("mission_spec") or {})
                         .get("metadata") or {}).get("reason")
                    ),
                }
                for child in children
            ],
            "post_mortem_count": len(post_mortems),
        }
    def list(self, *, unread_only: bool = False, limit: int = 20) -> list[MissionReport]:
        state = self._store().get()
        rows = [
            row for row in state.get("tasks", [])
            if self._is_reportable(row) and (not unread_only or not self._acked(row))
        ]
        rows.sort(key=lambda row: str(row.get("updated_at") or ""), reverse=True)
        reports: list[MissionReport] = []
        for task in rows[: max(1, min(100, int(limit)))]:
            reports.append(MissionReport(
                task_id=str(task.get("id") or ""),
                mission_id=str(task.get("correlation_id") or ""),
                objective=str(task.get("title") or ""),
                status=str(task.get("status") or ""),
                updated_at=str(task.get("updated_at") or ""),
                acknowledged=self._acked(task),
                debrief=self._debrief(state, task),
            ))
        return reports

    def unread_count(self) -> int:
        return len(self.list(unread_only=True, limit=100))

    def acknowledge(self, task_id: str, *, actor: str = "overlord") -> MissionReport:
        store = self._store()
        state = store.get()
        task = next(
            (row for row in state.get("tasks", []) if row.get("id") == task_id),
            None,
        )
        if task is None or not self._is_reportable(task):
            raise ValueError(f"mission report not found: {task_id}")
        routing = dict(task.get("routing") or {})
        inbox = dict(routing.get("inbox") or {})
        inbox.update({"acked_at": _now_iso(), "acked_by": str(actor or "overlord")})
        routing["inbox"] = inbox
        updated = store.update_task(task_id, routing=routing)
        fresh = store.get()
        return MissionReport(
            task_id=str(updated.get("id") or ""),
            mission_id=str(updated.get("correlation_id") or ""),
            objective=str(updated.get("title") or ""),
            status=str(updated.get("status") or ""),
            updated_at=str(updated.get("updated_at") or ""),
            acknowledged=True,
            debrief=self._debrief(fresh, updated),
        )
