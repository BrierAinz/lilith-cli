from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from lilith_tools.orchestration_sqlite import SQLiteOrchestrationBackend


def test_json_snapshot_is_imported_without_deleting_source(tmp_path: Path) -> None:
    legacy = tmp_path / "orchestration_state.json"
    legacy.write_text(
        json.dumps({
            "plan": {"name": "v8", "description": "migration"},
            "tasks": [{
                "id": "old-1", "title": "legacy", "description": "",
                "status": "pendiente", "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            }],
            "costs": {"historical": {"presets": {}, "providers": {}, "total": {}}},
            "post_mortems": [],
        }),
        encoding="utf-8",
    )
    store = SQLiteOrchestrationBackend(tmp_path / "state.sqlite3", legacy_json=legacy)
    state = store.get()
    assert state["backend"] == "sqlite"
    assert state["plan"]["name"] == "v8"
    assert state["tasks"][0]["id"] == "old-1"
    assert legacy.exists()
    assert store.events()[0]["event_type"] == "migration.import_json"


def test_concurrent_initializers_import_legacy_exactly_once(tmp_path: Path) -> None:
    legacy = tmp_path / "orchestration_state.json"
    legacy.write_text(
        json.dumps({
            "tasks": [{"id": "legacy", "title": "kept", "status": "pendiente"}]
        }),
        encoding="utf-8",
    )
    target = tmp_path / "orchestration_state.sqlite3"

    def open_store(_: int) -> None:
        SQLiteOrchestrationBackend(target, legacy_json=legacy)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(open_store, range(16)))

    backend = SQLiteOrchestrationBackend(target, legacy_json=legacy)
    assert [task["id"] for task in backend.get()["tasks"]] == ["legacy"]
    assert [event["event_type"] for event in backend.events()] == [
        "migration.import_json"
    ]


def test_concurrent_writers_do_not_lose_tasks(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"

    def add(index: int) -> str:
        store = SQLiteOrchestrationBackend(path)
        return store.add_task(f"task {index}", task_id=f"task-{index}")["id"]

    with ThreadPoolExecutor(max_workers=10) as pool:
        ids = list(pool.map(add, range(40)))

    state = SQLiteOrchestrationBackend(path).get()
    assert len(ids) == 40
    assert len(state["tasks"]) == 40
    assert {task["id"] for task in state["tasks"]} == set(ids)
    assert state["event_count"] == 40


def test_idempotency_key_returns_existing_task(tmp_path: Path) -> None:
    store = SQLiteOrchestrationBackend(tmp_path / "state.sqlite3")
    first = store.add_task("one", idempotency_key="request-1")
    second = store.add_task("duplicate", idempotency_key="request-1")
    assert first["id"] == second["id"]
    assert len(store.get()["tasks"]) == 1


def test_lease_claim_checkpoint_and_crash_resume(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    store = SQLiteOrchestrationBackend(path)
    task = store.add_task("resumable", success_criteria=["tests pass"])
    claimed = store.claim_task(task["id"], "worker-a", lease_seconds=30)
    assert claimed["status"] == "delegada"
    assert claimed["lease_owner"] == "worker-a"
    with pytest.raises(ValueError, match="reclamada"):
        store.claim_task(task["id"], "worker-b", lease_seconds=30)

    checkpoint = store.checkpoint_task(task["id"], "tests", {"passed": 3})
    assert store.checkpoints(task["id"])[0]["id"] == checkpoint["id"]

    # Simulate a worker crash by expiring the persisted lease.
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE task_leases SET expires_at = 0 WHERE task_id = ?", (task["id"],))
    resumed = store.resume_expired()
    assert [item["id"] for item in resumed] == [task["id"]]
    restored = next(item for item in store.get()["tasks"] if item["id"] == task["id"])
    assert restored["status"] == "pendiente"
    assert restored["last_checkpoint"] == checkpoint["id"]


def test_terminal_task_releases_lease_and_has_audit_events(tmp_path: Path) -> None:
    store = SQLiteOrchestrationBackend(tmp_path / "state.sqlite3")
    task = store.add_task("finish")
    store.claim_task(task["id"], "worker")
    done = store.update_task(
        task["id"], status="completada", verification={"verified": True}
    )
    assert done["status"] == "completada"
    assert store.get()["active_leases"] == []
    event_types = {event["event_type"] for event in store.events(task_id=task["id"])}
    assert {"task.added", "task.claimed", "task.updated"} <= event_types
