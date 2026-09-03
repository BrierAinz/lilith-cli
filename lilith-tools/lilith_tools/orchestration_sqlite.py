"""Transactional SQLite backend for Lilith orchestration state.

The public compatibility facade remains ``OrchestrationStateStore``.  This
backend normalises independently-mutated records (tasks, leases, events and
checkpoints) while keeping plan/cost payloads as JSON documents.  Every
mutation uses ``BEGIN IMMEDIATE`` so concurrent Lilith, Telegram and worker
processes cannot silently overwrite each other.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


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


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def empty_costs() -> dict[str, Any]:
    return {
        "historical": {"presets": {}, "providers": {}, "total": {}},
        "sessions": {},
    }


class SQLiteOrchestrationBackend:
    """Process-safe backend used by production orchestration state."""

    def __init__(self, path: Path, *, legacy_json: Path | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        if legacy_json and legacy_json.exists():
            self._import_legacy_once(legacy_json)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=15000")
        for attempt in range(100):
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 99:
                    conn.close()
                    raise
                time.sleep(0.02)
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS state_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    idempotency_key TEXT UNIQUE,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS post_mortems (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS orchestration_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    task_id TEXT,
                    correlation_id TEXT,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_task
                    ON orchestration_events(task_id, seq DESC);
                CREATE TABLE IF NOT EXISTS task_leases (
                    task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
                    owner TEXT NOT NULL,
                    acquired_at REAL NOT NULL,
                    heartbeat_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS task_checkpoints (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    label TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_checkpoints_task
                    ON task_checkpoints(task_id, created_at DESC);
                """
            )
            self._set_meta(conn, "version", 3)
            if self._get_meta(conn, "costs", None) is None:
                self._set_meta(conn, "costs", empty_costs())
            if self._get_meta(conn, "revision", None) is None:
                self._set_meta(conn, "revision", 0)

    @staticmethod
    def _get_meta(conn: sqlite3.Connection, key: str, default: Any) -> Any:
        row = conn.execute("SELECT value FROM state_meta WHERE key = ?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            return default

    @staticmethod
    def _set_meta(conn: sqlite3.Connection, key: str, value: Any) -> None:
        conn.execute(
            "INSERT INTO state_meta(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )

    def _bump_revision(self, conn: sqlite3.Connection) -> int:
        revision = int(self._get_meta(conn, "revision", 0)) + 1
        self._set_meta(conn, "revision", revision)
        return revision

    @staticmethod
    def _event(
        conn: sqlite3.Connection,
        event_type: str,
        payload: dict[str, Any],
        *,
        task_id: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        conn.execute(
            "INSERT INTO orchestration_events "
            "(event_type, task_id, correlation_id, payload, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                event_type,
                task_id,
                correlation_id,
                json.dumps(payload, ensure_ascii=False, default=str),
                now_iso(),
            ),
        )

    @staticmethod
    def _task_from_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return json.loads(row["payload"])

    def _load_task(self, conn: sqlite3.Connection, task_id: str) -> dict[str, Any]:
        row = conn.execute("SELECT payload FROM tasks WHERE id = ?", (task_id,)).fetchone()
        task = self._task_from_row(row)
        if task is None:
            raise ValueError(f"task no encontrada: {task_id}")
        return task

    @staticmethod
    def _save_task(conn: sqlite3.Connection, task: dict[str, Any]) -> None:
        conn.execute(
            "UPDATE tasks SET payload = ?, updated_at = ? WHERE id = ?",
            (json.dumps(task, ensure_ascii=False), task["updated_at"], task["id"]),
        )

    def get(self) -> dict[str, Any]:
        with self._connect() as conn:
            tasks = [
                json.loads(row[0])
                for row in conn.execute("SELECT payload FROM tasks ORDER BY created_at, id")
            ]
            post_mortems = [
                json.loads(row[0])
                for row in conn.execute("SELECT payload FROM post_mortems ORDER BY seq")
            ]
            active_leases = [
                dict(row)
                for row in conn.execute(
                    "SELECT task_id, owner, acquired_at, heartbeat_at, expires_at "
                    "FROM task_leases WHERE expires_at > ? ORDER BY task_id",
                    (time.time(),),
                )
            ]
            event_count = int(
                conn.execute("SELECT COUNT(*) FROM orchestration_events").fetchone()[0]
            )
            return {
                "version": 3,
                "backend": "sqlite",
                "revision": int(self._get_meta(conn, "revision", 0)),
                "plan": self._get_meta(conn, "plan", None),
                "tasks": tasks,
                "costs": self._get_meta(conn, "costs", empty_costs()),
                "post_mortems": post_mortems,
                "active_leases": active_leases,
                "event_count": event_count,
            }

    def _replace_state_locked(
        self, conn: sqlite3.Connection, state: dict[str, Any], event_type: str
    ) -> None:
        conn.execute("DELETE FROM task_leases")
        conn.execute("DELETE FROM task_checkpoints")
        conn.execute("DELETE FROM tasks")
        conn.execute("DELETE FROM post_mortems")
        self._set_meta(conn, "plan", state.get("plan"))
        self._set_meta(conn, "costs", state.get("costs") or empty_costs())
        for raw in state.get("tasks") or []:
            if not isinstance(raw, dict) or not raw.get("id"):
                continue
            task = dict(raw)
            stamp = str(task.get("created_at") or now_iso())
            task.setdefault("updated_at", stamp)
            task.setdefault("correlation_id", uuid.uuid4().hex)
            conn.execute(
                "INSERT INTO tasks(id, idempotency_key, payload, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    task["id"], task.get("idempotency_key"),
                    json.dumps(task, ensure_ascii=False), stamp, task["updated_at"],
                ),
            )
        for raw in state.get("post_mortems") or []:
            if not isinstance(raw, dict):
                continue
            record = dict(raw)
            created = str(record.get("created_at") or now_iso())
            record.setdefault("created_at", created)
            conn.execute(
                "INSERT INTO post_mortems(payload, created_at) VALUES (?, ?)",
                (json.dumps(record, ensure_ascii=False), created),
            )
        self._event(conn, event_type, {"tasks": len(state.get("tasks") or [])})
        self._bump_revision(conn)

    def _import_legacy_once(self, legacy_json: Path) -> None:
        try:
            payload = json.loads(legacy_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return
        if not isinstance(payload, dict):
            return
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if self._get_meta(conn, "legacy_migration", None) is not None:
                conn.rollback()
                return
            occupied = int(conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])
            events = int(
                conn.execute("SELECT COUNT(*) FROM orchestration_events").fetchone()[0]
            )
            if occupied or events:
                self._set_meta(conn, "legacy_migration", "skipped_nonempty")
                conn.commit()
                return
            self._replace_state_locked(conn, payload, "migration.import_json")
            self._set_meta(conn, "legacy_migration", "imported")
            conn.commit()

    def replace_state(self, state: dict[str, Any], *, event_type: str = "state.replace") -> None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._replace_state_locked(conn, state, event_type)
            conn.commit()

    def set_plan(self, name: str, description: str = "") -> dict[str, Any]:
        name = str(name).strip()
        if not name:
            raise ValueError("name es requerido")
        stamp = now_iso()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = self._get_meta(conn, "plan", None) or {}
            plan = {
                "name": name,
                "description": str(description).strip(),
                "created_at": existing.get("created_at", stamp),
                "updated_at": stamp,
            }
            self._set_meta(conn, "plan", plan)
            self._event(conn, "plan.set", plan)
            self._bump_revision(conn)
            conn.commit()
        return self.get()

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
            raise ValueError(f"estado invalido: {status}")
        ident = str(task_id or uuid.uuid4().hex[:12]).strip()
        idempotency_key = str(metadata.get("idempotency_key") or "").strip() or None
        stamp = now_iso()
        correlation_id = str(metadata.get("correlation_id") or uuid.uuid4().hex)
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
            "correlation_id": correlation_id,
            "trace_id": metadata.get("trace_id"),
            "idempotency_key": idempotency_key,
            "checkpoints": [],
        }
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if idempotency_key:
                row = conn.execute(
                    "SELECT payload FROM tasks WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                existing = self._task_from_row(row)
                if existing is not None:
                    conn.rollback()
                    return existing
            try:
                conn.execute(
                    "INSERT INTO tasks(id, idempotency_key, payload, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (ident, idempotency_key, json.dumps(task, ensure_ascii=False), stamp, stamp),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"task id duplicado: {ident}") from exc
            self._event(
                conn,
                "task.added",
                {"title": title, "status": status, "preset": preset},
                task_id=ident,
                correlation_id=correlation_id,
            )
            self._bump_revision(conn)
            conn.commit()
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
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._load_task(conn, task_id)
            old_status = task["status"]
            if status is not None:
                if status not in VALID_STATUSES:
                    raise ValueError(f"estado invalido: {status}")
                if status != old_status and status not in VALID_TRANSITIONS[old_status]:
                    raise ValueError(f"transicion invalida: {old_status} -> {status}")
                task["status"] = status
                if status == "delegada" and not task.get("started_at"):
                    task["started_at"] = now_iso()
                if status in TERMINAL_STATUSES:
                    task["completed_at"] = now_iso()
                    conn.execute("DELETE FROM task_leases WHERE task_id = ?", (task_id,))
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
            allowed = {
                "dependencies", "attempts", "max_retries", "routing",
                "escalation", "turns", "provider", "post_mortem",
                "success_criteria", "budget", "verification", "trace_id",
                "lease_owner", "lease_expires_at", "last_checkpoint",
            }
            for key in allowed:
                if key in metadata:
                    task[key] = metadata[key]
            task["updated_at"] = now_iso()
            self._save_task(conn, task)
            plan = self._get_meta(conn, "plan", None)
            if isinstance(plan, dict):
                plan["updated_at"] = task["updated_at"]
                self._set_meta(conn, "plan", plan)
            self._event(
                conn,
                "task.updated",
                {"from": old_status, "to": task["status"], "metadata": sorted(allowed & metadata.keys())},
                task_id=task_id,
                correlation_id=task.get("correlation_id"),
            )
            self._bump_revision(conn)
            conn.commit()
        return task

    def append_post_mortem(self, entry: dict[str, Any]) -> dict[str, Any]:
        record = {"created_at": now_iso(), **entry}
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO post_mortems(payload, created_at) VALUES (?, ?)",
                (json.dumps(record, ensure_ascii=False), record["created_at"]),
            )
            self._event(
                conn,
                "task.post_mortem",
                {"success": bool(record.get("success")), "preset": record.get("preset")},
                task_id=record.get("task_id"),
                correlation_id=record.get("correlation_id"),
            )
            self._bump_revision(conn)
            conn.commit()
        return record

    @staticmethod
    def _add_usage(bucket: dict[str, Any], usage: dict[str, Any]) -> None:
        for key in ("prompt_tokens", "completion_tokens", "total_tokens", "cost_usd"):
            value = usage.get(key, 0)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                bucket[key] = bucket.get(key, 0) + value
        bucket["calls"] = bucket.get("calls", 0) + 1

    def record_cost(
        self, preset: str, provider: str, usage: dict[str, Any], *, session_id: str
    ) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            costs = self._get_meta(conn, "costs", empty_costs())
            historical = costs.setdefault("historical", empty_costs()["historical"])
            sessions = costs.setdefault("sessions", {})
            session = sessions.setdefault(
                session_id or "default", {"presets": {}, "providers": {}, "total": {}}
            )
            for scope, key in (("presets", preset or "unknown"), ("providers", provider or "unknown")):
                self._add_usage(historical.setdefault(scope, {}).setdefault(key, {}), usage)
                self._add_usage(session.setdefault(scope, {}).setdefault(key, {}), usage)
            self._add_usage(historical.setdefault("total", {}), usage)
            self._add_usage(session.setdefault("total", {}), usage)
            self._set_meta(conn, "costs", costs)
            self._event(conn, "cost.recorded", {"preset": preset, "provider": provider, "usage": usage})
            self._bump_revision(conn)
            conn.commit()
        return self.cost_summary(session_id)

    def cost_summary(self, session_id: str = "") -> dict[str, Any]:
        with self._connect() as conn:
            costs = self._get_meta(conn, "costs", empty_costs())
        return {
            "session": (costs.get("sessions") or {}).get(
                session_id or "default", {"presets": {}, "providers": {}, "total": {}}
            ),
            "historical": costs.get("historical") or empty_costs()["historical"],
        }

    def reset_costs(self) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._set_meta(conn, "costs", empty_costs())
            self._event(conn, "cost.reset", {})
            self._bump_revision(conn)
            conn.commit()

    def claim_task(self, task_id: str, owner: str, *, lease_seconds: float = 300) -> dict[str, Any]:
        owner = str(owner).strip()
        if not owner:
            raise ValueError("owner es requerido")
        now = time.time()
        expires = now + max(1.0, float(lease_seconds))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._load_task(conn, task_id)
            if task["status"] in TERMINAL_STATUSES:
                raise ValueError(f"task terminal no reclamable: {task_id}")
            lease = conn.execute(
                "SELECT owner, expires_at FROM task_leases WHERE task_id = ?", (task_id,)
            ).fetchone()
            if lease and float(lease["expires_at"]) > now and lease["owner"] != owner:
                raise ValueError(f"task ya reclamada por {lease['owner']}")
            conn.execute(
                "INSERT INTO task_leases(task_id, owner, acquired_at, heartbeat_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(task_id) DO UPDATE SET "
                "owner=excluded.owner, acquired_at=excluded.acquired_at, "
                "heartbeat_at=excluded.heartbeat_at, expires_at=excluded.expires_at",
                (task_id, owner, now, now, expires),
            )
            old_status = task["status"]
            if old_status != "delegada":
                task["status"] = "delegada"
                task.setdefault("started_at", now_iso())
            task["lease_owner"] = owner
            task["lease_expires_at"] = expires
            task["updated_at"] = now_iso()
            self._save_task(conn, task)
            self._event(
                conn, "task.claimed", {"owner": owner, "expires_at": expires},
                task_id=task_id, correlation_id=task.get("correlation_id")
            )
            self._bump_revision(conn)
            conn.commit()
        return task

    def renew_lease(self, task_id: str, owner: str, *, lease_seconds: float = 300) -> dict[str, Any]:
        now = time.time()
        expires = now + max(1.0, float(lease_seconds))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            lease = conn.execute(
                "SELECT owner, expires_at FROM task_leases WHERE task_id = ?", (task_id,)
            ).fetchone()
            if lease is None or lease["owner"] != owner or float(lease["expires_at"]) <= now:
                raise ValueError("lease ausente, expirada o de otro owner")
            conn.execute(
                "UPDATE task_leases SET heartbeat_at=?, expires_at=? WHERE task_id=?",
                (now, expires, task_id),
            )
            task = self._load_task(conn, task_id)
            task["lease_expires_at"] = expires
            task["updated_at"] = now_iso()
            self._save_task(conn, task)
            self._event(
                conn, "task.lease_renewed", {"owner": owner, "expires_at": expires},
                task_id=task_id, correlation_id=task.get("correlation_id")
            )
            self._bump_revision(conn)
            conn.commit()
        return task

    def release_task(self, task_id: str, owner: str, *, status: str = "pendiente") -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            lease = conn.execute(
                "SELECT owner FROM task_leases WHERE task_id = ?", (task_id,)
            ).fetchone()
            if lease is not None and lease["owner"] != owner:
                raise ValueError(f"lease pertenece a {lease['owner']}")
            task = self._load_task(conn, task_id)
            if status not in VALID_STATUSES:
                raise ValueError(f"estado invalido: {status}")
            old_status = task["status"]
            if status != old_status and status not in VALID_TRANSITIONS[old_status]:
                raise ValueError(f"transicion invalida: {old_status} -> {status}")
            conn.execute("DELETE FROM task_leases WHERE task_id = ?", (task_id,))
            task["status"] = status
            task["lease_owner"] = None
            task["lease_expires_at"] = None
            task["updated_at"] = now_iso()
            self._save_task(conn, task)
            self._event(
                conn, "task.released", {"owner": owner, "status": status},
                task_id=task_id, correlation_id=task.get("correlation_id")
            )
            self._bump_revision(conn)
            conn.commit()
        return task

    def checkpoint_task(
        self, task_id: str, label: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        label = str(label).strip()
        if not label:
            raise ValueError("label es requerido")
        checkpoint = {
            "id": uuid.uuid4().hex,
            "task_id": task_id,
            "label": label,
            "payload": dict(payload or {}),
            "created_at": now_iso(),
        }
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._load_task(conn, task_id)
            conn.execute(
                "INSERT INTO task_checkpoints(id, task_id, label, payload, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    checkpoint["id"], task_id, label,
                    json.dumps(checkpoint["payload"], ensure_ascii=False),
                    checkpoint["created_at"],
                ),
            )
            task.setdefault("checkpoints", []).append(
                {k: checkpoint[k] for k in ("id", "label", "created_at")}
            )
            task["last_checkpoint"] = checkpoint["id"]
            task["updated_at"] = now_iso()
            self._save_task(conn, task)
            self._event(
                conn, "task.checkpointed", {"checkpoint_id": checkpoint["id"], "label": label},
                task_id=task_id, correlation_id=task.get("correlation_id")
            )
            self._bump_revision(conn)
            conn.commit()
        return checkpoint

    def checkpoints(self, task_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, task_id, label, payload, created_at FROM task_checkpoints "
                "WHERE task_id = ? ORDER BY created_at DESC", (task_id,)
            ).fetchall()
        return [
            {**dict(row), "payload": json.loads(row["payload"])} for row in rows
        ]

    def cancel_task(self, task_id: str, reason: str = "") -> dict[str, Any]:
        return self.update_task(
            task_id,
            status="cancelada",
            result=reason or "cancelada por operador",
            verification={"verified": False, "reason": "cancelled"},
        )

    def resume_expired(self) -> list[dict[str, Any]]:
        now = time.time()
        resumed: list[dict[str, Any]] = []
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT task_id, owner FROM task_leases WHERE expires_at <= ?", (now,)
            ).fetchall()
            for row in rows:
                task = self._load_task(conn, str(row["task_id"]))
                if task["status"] == "delegada":
                    task["status"] = "pendiente"
                task["lease_owner"] = None
                task["lease_expires_at"] = None
                task["updated_at"] = now_iso()
                self._save_task(conn, task)
                self._event(
                    conn, "task.resumed_after_lease", {"previous_owner": row["owner"]},
                    task_id=task["id"], correlation_id=task.get("correlation_id")
                )
                resumed.append(task)
            conn.execute("DELETE FROM task_leases WHERE expires_at <= ?", (now,))
            if resumed:
                self._bump_revision(conn)
            conn.commit()
        return resumed

    def events(self, *, limit: int = 100, task_id: str | None = None) -> list[dict[str, Any]]:
        query = (
            "SELECT * FROM orchestration_events WHERE task_id = ? ORDER BY seq DESC LIMIT ?"
            if task_id else
            "SELECT * FROM orchestration_events ORDER BY seq DESC LIMIT ?"
        )
        params: tuple[Any, ...] = (task_id, max(1, int(limit))) if task_id else (max(1, int(limit)),)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def clear(self) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM task_leases")
            conn.execute("DELETE FROM task_checkpoints")
            conn.execute("DELETE FROM tasks")
            conn.execute("DELETE FROM post_mortems")
            self._set_meta(conn, "plan", None)
            self._set_meta(conn, "costs", empty_costs())
            self._event(conn, "state.cleared", {})
            self._bump_revision(conn)
            conn.commit()


__all__ = [
    "TERMINAL_STATUSES",
    "VALID_STATUSES",
    "VALID_TRANSITIONS",
    "SQLiteOrchestrationBackend",
]
