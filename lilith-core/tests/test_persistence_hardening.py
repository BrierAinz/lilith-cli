from __future__ import annotations

import json
import multiprocessing
from pathlib import Path

import pytest
from lilith_core.audit_trail import AuditEntry, PolicyAuditTrail
from lilith_core.goal_state import (
    GoalPersistenceError,
    GoalStateManager,
)
from lilith_core.goal_state import (
    RevisionConflictError as CoreRevisionConflict,
)
from lilith_skills.cross_context import (
    GoalsStore,
    HandoffsStore,
)
from lilith_skills.cross_context import (
    RevisionConflictError as CrossRevisionConflict,
)
from lilith_skills.handoff_pack import HandoffPackManager

HOSTILE_IDS = ("../escaped", "..", "/absolute", r"C:\\absolute", "bad/name")


@pytest.mark.parametrize("storage_id", HOSTILE_IDS)
def test_cross_context_ids_are_confined(tmp_path: Path, storage_id: str) -> None:
    goals = GoalsStore(tmp_path / ".ygg")
    handoffs = HandoffsStore(tmp_path / ".ygg")
    with pytest.raises(ValueError):
        goals.create("hostile", goal_id=storage_id)
    with pytest.raises(ValueError):
        goals.get(storage_id)
    with pytest.raises(ValueError):
        goals.delete(storage_id)
    with pytest.raises(ValueError):
        handoffs.get(storage_id)
    with pytest.raises(ValueError):
        handoffs.delete(storage_id)
    assert not (tmp_path / "escaped.json").exists()


@pytest.mark.parametrize("storage_id", HOSTILE_IDS)
def test_core_and_session_handoff_ids_are_confined(
    tmp_path: Path, storage_id: str
) -> None:
    goals_dir = tmp_path / "goals"
    manager = GoalStateManager(goals_dir)
    handoffs = HandoffPackManager(tmp_path / "handoffs")
    with pytest.raises(ValueError):
        manager._goal_path(storage_id)
    with pytest.raises(ValueError):
        handoffs.get(storage_id)
    with pytest.raises(ValueError):
        handoffs.delete(storage_id)


def test_cross_context_rejects_stale_revision(tmp_path: Path) -> None:
    store = GoalsStore(tmp_path / ".ygg")
    created = store.create("goal", goal_id="stable")
    left = store.get(created.id)
    right = store.get(created.id)
    assert left is not None and right is not None
    left.add_turn("left", "write")
    store.save(left)
    right.add_turn("right", "stale")
    with pytest.raises(CrossRevisionConflict):
        store.save(right)
    persisted = store.get(created.id)
    assert persisted is not None
    assert [turn.agent for turn in persisted.turns] == ["left"]


def test_core_managers_reject_lost_update(tmp_path: Path) -> None:
    storage = tmp_path / "goals"
    left = GoalStateManager(storage)
    goal = left.create_goal("goal")
    right = GoalStateManager(storage)
    left.record_turn(goal.id, "left", "write")
    with pytest.raises(CoreRevisionConflict):
        right.record_turn(goal.id, "right", "stale")
    recovered = right.get_goal(goal.id)
    assert recovered is not None
    assert [turn.agent for turn in recovered.turns] == ["left"]
    reopened = GoalStateManager(storage).get_goal(goal.id)
    assert reopened is not None
    assert [turn.agent for turn in reopened.turns] == ["left"]


@pytest.mark.parametrize(
    "make_store",
    (
        lambda root: GoalsStore(root / ".ygg"),
        lambda root: GoalStateManager(root / "goals"),
    ),
)
def test_failed_atomic_replace_preserves_previous_json_and_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_store
) -> None:
    store = make_store(tmp_path)
    goal = (
        store.create("goal", goal_id="stable")
        if isinstance(store, GoalsStore)
        else store.create_goal("goal")
    )
    path = (
        store.goals_dir / f"{goal.id}.json"
        if isinstance(store, GoalsStore)
        else store._goal_path(goal.id)
    )
    before = path.read_bytes()
    before_revision = goal.revision

    def fail_replace(*args, **kwargs):
        raise OSError("injected replace failure")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="injected"):
        if isinstance(store, GoalsStore):
            goal.add_turn("test", "fail")
            store.save(goal)
        else:
            store.record_turn(goal.id, "test", "fail")
    assert path.read_bytes() == before
    assert goal.revision == before_revision
    if isinstance(store, GoalStateManager):
        recovered = store.get_goal(goal.id)
        assert recovered is not None
        assert recovered.revision == before_revision
        assert recovered.turns == []
    assert not list(path.parent.glob(f".{path.name}.*.tmp"))


def test_cross_context_corruption_is_explicit(tmp_path: Path) -> None:
    store = GoalsStore(tmp_path / ".ygg")
    store.goals_dir.mkdir(parents=True)
    (store.goals_dir / "broken.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError, match="corrupt or unreadable"):
        store.get("broken")
    with pytest.raises(ValueError, match="corrupt or unreadable"):
        store.list()


def test_core_rejects_foreign_goal_and_handoff_schemas(tmp_path: Path) -> None:
    storage = tmp_path / "goals"
    storage.mkdir()
    (storage / "foreign.json").write_text(
        json.dumps({"schema_id": "foreign-goal", "id": "foreign"}),
        encoding="utf-8",
    )
    manager = GoalStateManager(storage)
    assert "foreign" in manager._load_errors
    with pytest.raises(GoalPersistenceError):
        manager.import_handoff(
            {"schema_id": "foreign-handoff", "schema_version": 1}
        )


def test_audit_write_failure_does_not_count_or_callback(tmp_path: Path) -> None:
    unwritable = tmp_path / "audit.jsonl"
    unwritable.mkdir()
    callbacks: list[AuditEntry] = []
    trail = PolicyAuditTrail(unwritable, on_record=callbacks.append)
    with pytest.raises(OSError):
        trail.record(AuditEntry(policy="p", agent="test", session="s", action="deny"))
    assert trail.stats()["total_recorded"] == 0
    assert callbacks == []


def test_audit_rotation_and_restart_preserve_every_event(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    trail = PolicyAuditTrail(path, max_entries=5)
    for index in range(12):
        trail.record(
            AuditEntry(policy=f"p{index}", agent="test", session="s", action="allow")
        )
    assert [entry.policy for entry in trail.iter_file()] == [f"p{i}" for i in range(12)]
    assert len(list(tmp_path.glob("audit.jsonl.*.rotated"))) == 2

    reopened = PolicyAuditTrail(path, max_entries=5)
    assert reopened.stats()["total_recorded"] == 12
    assert [entry.policy for entry in reopened.tail(3)] == ["p9", "p10", "p11"]
    assert trail.stats()["buffered"] <= 5


def _competing_writer(root, kind, goal_id, barrier, results):
    import importlib
    import time

    module = importlib.import_module(
        "lilith_core.goal_state" if kind == "core" else "lilith_skills.cross_context"
    )
    original_write = module._atomic_json_write

    def delayed_write(path, payload):
        # Enlarge the read/replace window so an unprotected writer loses updates.
        time.sleep(0.2)
        original_write(path, payload)

    module._atomic_json_write = delayed_write
    if kind == "core":
        store = GoalStateManager(root)
        goal = store.get_goal(goal_id)
    else:
        store = GoalsStore(root)
        goal = store.get(goal_id)
    barrier.wait(timeout=15)
    try:
        if kind == "core":
            store.record_turn(goal_id, "worker", "write")
        else:
            goal.add_turn("worker", "write")
            store.save(goal)
    except (CoreRevisionConflict, CrossRevisionConflict):
        results.put("conflict")
    else:
        results.put("saved")


@pytest.mark.parametrize("kind", ["core", "cross"])
def test_concurrent_processes_cannot_lose_updates(tmp_path, kind, monkeypatch):
    # pytest importlib names this module under the hyphenated package directory.
    # Spawned interpreters need the stack root to import that namespace too.
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    root = tmp_path / kind
    store = GoalStateManager(root) if kind == "core" else GoalsStore(root)
    goal = store.create_goal("shared") if kind == "core" else store.create("shared")
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(2)
    results = ctx.Queue()
    workers = [ctx.Process(target=_competing_writer,
                          args=(root, kind, goal.id, barrier, results)) for _ in range(2)]
    try:
        for worker in workers:
            worker.start()
        assert sorted(results.get(timeout=30) for _ in workers) == ["conflict", "saved"]
        for worker in workers:
            worker.join(timeout=10)
            assert worker.exitcode == 0
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join()
        results.close()
    reopened = GoalStateManager(root).get_goal(goal.id) if kind == "core" else store.get(goal.id)
    assert len(reopened.turns) == 1


@pytest.mark.parametrize("kind", ["core", "cross"])
@pytest.mark.parametrize("mutation", [{"schema_version": 99}, {"id": "other"}])
def test_goal_schema_and_identity_rejected(tmp_path, kind, mutation):
    store = GoalStateManager(tmp_path) if kind == "core" else GoalsStore(tmp_path)
    goal = store.create_goal("test") if kind == "core" else store.create("test")
    directory = tmp_path if kind == "core" else store.goals_dir
    path = directory / f"{goal.id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(mutation)
    path.write_text(json.dumps(data), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises((GoalPersistenceError, ValueError)):
        if kind == "core":
            store.record_turn(goal.id, "worker", "write")
        else:
            store.save(goal)
    assert path.read_bytes() == before


def test_core_legacy_raw_handoff_remains_supported(tmp_path):
    source = GoalStateManager(tmp_path / "source")
    goal = source.create_goal("legacy")
    payload = goal.to_dict()
    for key in ("schema_id", "schema_version", "revision", "writer"):
        payload.pop(key)
    target = GoalStateManager(tmp_path / "target")
    assert target.import_handoff(payload).id == goal.id


def test_core_handoff_rejects_foreign_full_state(tmp_path):
    manager = GoalStateManager(tmp_path)
    goal = manager.create_goal("test")
    handoff = manager.export_handoff(goal.id)
    handoff["full_state"]["schema_id"] = "lilith-cross-goal"
    before = manager._goal_path(goal.id).read_bytes()
    with pytest.raises(GoalPersistenceError, match="full_state schema"):
        manager.import_handoff(handoff)
    assert manager._goal_path(goal.id).read_bytes() == before
