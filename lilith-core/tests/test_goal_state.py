"""Comprehensive tests for lilith_core.goal_state.

Covers GoalStateManager — the long-running agent loop engineering surface
modelled after LoopX and Moryn. Targets: enums + dataclasses (to/from
dict round-trips), CRUD, turn recording, gate resolution, todo tracking,
quota enforcement (can_proceed + quota_remaining), and cross-session
handoff serialization (export/import).

Tests use a per-test tmp storage directory to keep the manager's JSON
persistence isolated and to avoid touching the real .ygg/goals tree.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from lilith_core.goal_state import (
    Gate,
    GateStatus,
    Goal,
    GoalStateManager,
    GoalStatus,
    TodoItem,
    TurnRecord,
)


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def storage(tmp_path: Path) -> Path:
    """Per-test storage directory. Cleaned up automatically by pytest."""
    d = tmp_path / "goals"
    d.mkdir()
    return d


@pytest.fixture
def manager(storage: Path) -> GoalStateManager:
    """Fresh GoalStateManager bound to the per-test storage dir."""
    return GoalStateManager(storage_dir=storage)


# ── Enum coverage ───────────────────────────────────────────────────────────


class TestEnums:
    def test_goal_status_values(self) -> None:
        assert GoalStatus.ACTIVE.value == "active"
        assert GoalStatus.PAUSED.value == "paused"
        assert GoalStatus.BLOCKED.value == "blocked"
        assert GoalStatus.COMPLETED.value == "completed"
        assert GoalStatus.ABANDONED.value == "abandoned"

    def test_gate_status_values(self) -> None:
        assert GateStatus.PENDING.value == "pending"
        assert GateStatus.APPROVED.value == "approved"
        assert GateStatus.REJECTED.value == "rejected"
        assert GateStatus.SKIPPED.value == "skipped"

    def test_goal_status_round_trip(self) -> None:
        for s in GoalStatus:
            assert GoalStatus(s.value) == s


# ── Dataclass round-trips ───────────────────────────────────────────────────


class TestTurnRecord:
    def test_round_trip(self) -> None:
        t = TurnRecord(agent="Skadi", action="analyze", evidence="found 12", tokens_used=300)
        d = t.to_dict()
        t2 = TurnRecord.from_dict(d)
        assert t2.agent == "Skadi"
        assert t2.action == "analyze"
        assert t2.evidence == "found 12"
        assert t2.tokens_used == 300
        assert t.timestamp == pytest.approx(t2.timestamp, abs=0.01)

    def test_round_trip_with_metadata(self) -> None:
        t = TurnRecord(
            agent="Skadi",
            action="code",
            evidence="patched",
            metadata={"files": ["a.py", "b.py"], "ok": True},
        )
        d = t.to_dict()
        assert d["metadata"] == {"files": ["a.py", "b.py"], "ok": True}
        t2 = TurnRecord.from_dict(d)
        assert t2.metadata == {"files": ["a.py", "b.py"], "ok": True}

    def test_default_evidence(self) -> None:
        t = TurnRecord(agent="x", action="y")
        assert t.evidence == ""
        assert t.tokens_used == 0
        assert t.metadata == {}


class TestGate:
    def test_round_trip_pending(self) -> None:
        g = Gate(description="review the diff")
        d = g.to_dict()
        g2 = Gate.from_dict(d)
        assert g2.description == "review the diff"
        assert g2.status == GateStatus.PENDING
        assert g2.resolved_at is None
        assert g2.resolved_by is None

    def test_round_trip_resolved(self) -> None:
        g = Gate(
            description="ship?",
            status=GateStatus.APPROVED,
            resolved_at=123.0,
            resolved_by="Odin",
            resolution_note="lgtm",
        )
        d = g.to_dict()
        g2 = Gate.from_dict(d)
        assert g2.status == GateStatus.APPROVED
        assert g2.resolved_by == "Odin"
        assert g2.resolution_note == "lgtm"

    def test_id_unique_by_default(self) -> None:
        g1 = Gate(description="a")
        g2 = Gate(description="b")
        assert g1.id != g2.id
        assert len(g1.id) == 8


class TestTodoItem:
    def test_round_trip(self) -> None:
        ti = TodoItem(description="refactor", assigned_agent="Adan")
        d = ti.to_dict()
        ti2 = TodoItem.from_dict(d)
        assert ti2.description == "refactor"
        assert ti2.assigned_agent == "Adan"
        assert ti2.done is False

    def test_round_trip_done(self) -> None:
        ti = TodoItem(description="x", done=True, completed_at=42.0)
        d = ti.to_dict()
        ti2 = TodoItem.from_dict(d)
        assert ti2.done is True
        assert ti2.completed_at == 42.0


class TestGoal:
    def test_default_status_is_active(self) -> None:
        g = Goal(name="x")
        assert g.status == GoalStatus.ACTIVE
        assert g.turns == []
        assert g.gates == []
        assert g.todos == []

    def test_is_blocked_no_gates_no_quota(self) -> None:
        g = Goal()
        assert g.is_blocked is False

    def test_is_blocked_pending_gate(self) -> None:
        g = Goal()
        g.gates.append(Gate(description="review", status=GateStatus.PENDING))
        assert g.is_blocked is True

    def test_is_blocked_approved_gate_passes(self) -> None:
        g = Goal()
        g.gates.append(Gate(description="r", status=GateStatus.APPROVED))
        assert g.is_blocked is False

    def test_is_blocked_call_quota_exhausted(self) -> None:
        g = Goal(quota_max_calls=2, quota_used_calls=2)
        assert g.is_blocked is True

    def test_is_blocked_token_quota_exhausted(self) -> None:
        g = Goal(quota_max_tokens=100, quota_used_tokens=100)
        assert g.is_blocked is True

    def test_is_blocked_call_quota_just_under(self) -> None:
        g = Goal(quota_max_calls=2, quota_used_calls=1)
        assert g.is_blocked is False

    def test_is_blocked_unlimited_quota(self) -> None:
        g = Goal(quota_max_calls=0, quota_used_calls=999_999)
        assert g.is_blocked is False

    def test_completion_pct_no_todos(self) -> None:
        g = Goal()
        assert g.completion_pct == 0.0

    def test_completion_pct_mixed(self) -> None:
        g = Goal()
        g.todos.append(TodoItem(description="a", done=True))
        g.todos.append(TodoItem(description="b", done=False))
        g.todos.append(TodoItem(description="c", done=True))
        g.todos.append(TodoItem(description="d", done=False))
        assert g.completion_pct == 0.5

    def test_completion_pct_all_done(self) -> None:
        g = Goal()
        g.todos.extend(
            TodoItem(description=str(i), done=True) for i in range(3)
        )
        assert g.completion_pct == pytest.approx(1.0)

    def test_last_turn_none(self) -> None:
        g = Goal()
        assert g.last_turn is None

    def test_last_turn_returns_most_recent(self) -> None:
        g = Goal()
        g.turns.append(TurnRecord(agent="A", action="first"))
        g.turns.append(TurnRecord(agent="B", action="second"))
        g.turns.append(TurnRecord(agent="C", action="third"))
        assert g.last_turn is not None
        assert g.last_turn.agent == "C"

    def test_goal_to_from_dict_full(self) -> None:
        g = Goal(
            name="refactor",
            description="do the thing",
            project="Asgard/lilith-api",
            quota_max_calls=50,
            quota_max_tokens=10_000,
            metadata={"phase": "research"},
        )
        g.turns.append(TurnRecord(agent="Skadi", action="analyze", evidence="ok"))
        g.gates.append(Gate(description="ship?"))
        g.todos.append(TodoItem(description="a todo"))
        d = g.to_dict()
        # json-roundtrip exposes any non-serialisable surprises
        json.dumps(d)
        g2 = Goal.from_dict(d)
        assert g2.id == g.id
        assert g2.name == "refactor"
        assert g2.project == "Asgard/lilith-api"
        assert g2.quota_max_calls == 50
        assert g2.metadata == {"phase": "research"}
        assert len(g2.turns) == 1
        assert len(g2.gates) == 1
        assert len(g2.todos) == 1

    def test_goal_from_dict_defaults(self) -> None:
        g = Goal.from_dict({})
        assert g.id != ""  # random uuid prefix
        assert g.status == GoalStatus.ACTIVE
        assert g.turns == []


# ── GoalStateManager: CRUD ──────────────────────────────────────────────────


class TestGoalCRUD:
    def test_create_goal_persists_to_disk(self, manager: GoalStateManager, storage: Path) -> None:
        g = manager.create_goal(name="ship it", description="now")
        assert g.id
        assert g.name == "ship it"
        assert (storage / f"{g.id}.json").exists()

    def test_create_goal_with_quota_and_metadata(
        self, manager: GoalStateManager
    ) -> None:
        g = manager.create_goal(
            name="x",
            quota_max_calls=10,
            quota_max_tokens=2000,
            metadata={"owner": "Skadi"},
        )
        assert g.quota_max_calls == 10
        assert g.quota_max_tokens == 2000
        assert g.metadata == {"owner": "Skadi"}

    def test_create_goal_default_metadata_is_empty(
        self, manager: GoalStateManager
    ) -> None:
        g = manager.create_goal(name="x", metadata=None)
        assert g.metadata == {}

    def test_get_goal_returns_created(self, manager: GoalStateManager) -> None:
        g = manager.create_goal(name="x")
        fetched = manager.get_goal(g.id)
        assert fetched is not None
        assert fetched.id == g.id

    def test_get_goal_unknown(self, manager: GoalStateManager) -> None:
        assert manager.get_goal("missing") is None

    def test_list_goals_empty(self, manager: GoalStateManager) -> None:
        assert manager.list_goals() == []

    def test_list_goals_returns_all(self, manager: GoalStateManager) -> None:
        manager.create_goal(name="a")
        manager.create_goal(name="b")
        manager.create_goal(name="c")
        assert len(manager.list_goals()) == 3

    def test_list_goals_filter_by_status(self, manager: GoalStateManager) -> None:
        a = manager.create_goal(name="active")
        b = manager.create_goal(name="complete")
        manager.update_status(b.id, GoalStatus.COMPLETED)
        actives = manager.list_goals(status=GoalStatus.ACTIVE)
        assert {g.id for g in actives} == {a.id}
        completed = manager.list_goals(status=GoalStatus.COMPLETED)
        assert {g.id for g in completed} == {b.id}

    def test_list_goals_filter_by_project(self, manager: GoalStateManager) -> None:
        manager.create_goal(name="x", project="Asgard")
        manager.create_goal(name="y", project="Vanaheim")
        manager.create_goal(name="z", project="Asgard")
        asgard = manager.list_goals(project="Asgard")
        assert len(asgard) == 2
        assert all(g.project == "Asgard" for g in asgard)

    def test_list_goals_combined_filter(self, manager: GoalStateManager) -> None:
        a = manager.create_goal(name="a", project="Asgard")
        manager.create_goal(name="b", project="Asgard")
        manager.update_status(a.id, GoalStatus.COMPLETED)
        result = manager.list_goals(project="Asgard", status=GoalStatus.COMPLETED)
        assert {g.id for g in result} == {a.id}

    def test_update_status(self, manager: GoalStateManager) -> None:
        g = manager.create_goal(name="x")
        updated = manager.update_status(g.id, GoalStatus.PAUSED)
        assert updated is not None
        assert updated.status == GoalStatus.PAUSED
        assert manager.get_goal(g.id).status == GoalStatus.PAUSED

    def test_update_status_unknown(self, manager: GoalStateManager) -> None:
        assert manager.update_status("nope", GoalStatus.COMPLETED) is None

    def test_update_status_persists_to_disk(
        self, manager: GoalStateManager, storage: Path
    ) -> None:
        g = manager.create_goal(name="x")
        manager.update_status(g.id, GoalStatus.COMPLETED)
        on_disk = json.loads((storage / f"{g.id}.json").read_text())
        assert on_disk["status"] == "completed"

    def test_delete_goal(self, manager: GoalStateManager, storage: Path) -> None:
        g = manager.create_goal(name="x")
        assert manager.delete_goal(g.id) is True
        assert manager.get_goal(g.id) is None
        assert not (storage / f"{g.id}.json").exists()

    def test_delete_goal_unknown(self, manager: GoalStateManager) -> None:
        assert manager.delete_goal("nope") is False

    def test_id_uniqueness(self, manager: GoalStateManager) -> None:
        ids = {manager.create_goal(name=str(i)).id for i in range(20)}
        assert len(ids) == 20


# ── Turn recording ──────────────────────────────────────────────────────────


class TestTurnRecording:
    def test_record_turn_appends(self, manager: GoalStateManager) -> None:
        g = manager.create_goal(name="x")
        manager.record_turn(g.id, agent="Skadi", action="analyze", evidence="ok")
        goal = manager.get_goal(g.id)
        assert goal is not None
        assert len(goal.turns) == 1
        assert goal.turns[0].agent == "Skadi"

    def test_record_turn_increments_quota(self, manager: GoalStateManager) -> None:
        g = manager.create_goal(name="x")
        manager.record_turn(g.id, agent="Skadi", action="a", tokens_used=100)
        manager.record_turn(g.id, agent="Skadi", action="b", tokens_used=50)
        goal = manager.get_goal(g.id)
        assert goal is not None
        assert goal.quota_used_calls == 2
        assert goal.quota_used_tokens == 150

    def test_record_turn_unknown_goal(self, manager: GoalStateManager) -> None:
        assert manager.record_turn("missing", agent="x", action="y") is None

    def test_record_turn_with_metadata(self, manager: GoalStateManager) -> None:
        g = manager.create_goal(name="x")
        manager.record_turn(
            g.id, agent="x", action="y", evidence="z", metadata={"k": "v"}
        )
        t = manager.get_goal(g.id).turns[0]
        assert t.metadata == {"k": "v"}


# ── Gates ───────────────────────────────────────────────────────────────────


class TestGates:
    def test_add_gate(self, manager: GoalStateManager) -> None:
        g = manager.create_goal(name="x")
        gate = manager.add_gate(g.id, "review the diff")
        assert gate is not None
        assert gate.status == GateStatus.PENDING
        assert manager.get_goal(g.id).gates[0].id == gate.id

    def test_add_gate_unknown_goal(self, manager: GoalStateManager) -> None:
        assert manager.add_gate("nope", "x") is None

    def test_resolve_gate_approved(self, manager: GoalStateManager) -> None:
        g = manager.create_goal(name="x")
        gate = manager.add_gate(g.id, "ship?")
        resolved = manager.resolve_gate(g.id, gate.id, GateStatus.APPROVED, resolved_by="Odin", note="lgtm")
        assert resolved is not None
        assert resolved.status == GateStatus.APPROVED
        assert resolved.resolved_by == "Odin"
        assert resolved.resolution_note == "lgtm"
        assert resolved.resolved_at is not None

    def test_resolve_gate_rejected(self, manager: GoalStateManager) -> None:
        g = manager.create_goal(name="x")
        gate = manager.add_gate(g.id, "ship?")
        resolved = manager.resolve_gate(g.id, gate.id, GateStatus.REJECTED)
        assert resolved is not None
        assert resolved.status == GateStatus.REJECTED

    def test_resolve_gate_unknown_goal(self, manager: GoalStateManager) -> None:
        assert manager.resolve_gate("nope", "id", GateStatus.APPROVED) is None

    def test_resolve_gate_unknown_gate(self, manager: GoalStateManager) -> None:
        g = manager.create_goal(name="x")
        assert manager.resolve_gate(g.id, "fake", GateStatus.APPROVED) is None

    def test_gate_resolution_persists(self, manager: GoalStateManager, storage: Path) -> None:
        g = manager.create_goal(name="x")
        gate = manager.add_gate(g.id, "ship?")
        manager.resolve_gate(g.id, gate.id, GateStatus.APPROVED)
        on_disk = json.loads((storage / f"{g.id}.json").read_text())
        assert on_disk["gates"][0]["status"] == "approved"


# ── Todos ───────────────────────────────────────────────────────────────────


class TestTodos:
    def test_add_todo(self, manager: GoalStateManager) -> None:
        g = manager.create_goal(name="x")
        todo = manager.add_todo(g.id, "fix bug", assigned_agent="Adan")
        assert todo is not None
        assert todo.description == "fix bug"
        assert todo.assigned_agent == "Adan"
        assert todo.done is False

    def test_add_todo_unknown_goal(self, manager: GoalStateManager) -> None:
        assert manager.add_todo("nope", "x") is None

    def test_complete_todo(self, manager: GoalStateManager) -> None:
        g = manager.create_goal(name="x")
        todo = manager.add_todo(g.id, "fix")
        completed = manager.complete_todo(g.id, todo.id)
        assert completed is not None
        assert completed.done is True
        assert completed.completed_at is not None
        assert manager.get_goal(g.id).todos[0].done is True

    def test_complete_todo_unknown_goal(self, manager: GoalStateManager) -> None:
        assert manager.complete_todo("nope", "id") is None

    def test_complete_todo_unknown_todo(self, manager: GoalStateManager) -> None:
        g = manager.create_goal(name="x")
        assert manager.complete_todo(g.id, "fake") is None

    def test_remove_todo(self, manager: GoalStateManager) -> None:
        g = manager.create_goal(name="x")
        t1 = manager.add_todo(g.id, "a")
        t2 = manager.add_todo(g.id, "b")
        assert manager.remove_todo(g.id, t1.id) is True
        remaining = manager.get_goal(g.id).todos
        assert len(remaining) == 1
        assert remaining[0].id == t2.id

    def test_remove_todo_unknown(self, manager: GoalStateManager) -> None:
        g = manager.create_goal(name="x")
        assert manager.remove_todo(g.id, "fake") is False

    def test_remove_todo_unknown_goal(self, manager: GoalStateManager) -> None:
        assert manager.remove_todo("nope", "id") is False


# ── Quota / can_proceed ─────────────────────────────────────────────────────


class TestQuota:
    def test_can_proceed_fresh(self, manager: GoalStateManager) -> None:
        g = manager.create_goal(name="x")
        assert manager.can_proceed(g.id) is True

    def test_can_proceed_unknown_goal(self, manager: GoalStateManager) -> None:
        assert manager.can_proceed("nope") is False

    def test_can_proceed_paused(self, manager: GoalStateManager) -> None:
        g = manager.create_goal(name="x")
        manager.update_status(g.id, GoalStatus.PAUSED)
        assert manager.can_proceed(g.id) is False

    def test_can_proceed_blocked_by_gate(self, manager: GoalStateManager) -> None:
        g = manager.create_goal(name="x")
        manager.add_gate(g.id, "review")
        assert manager.can_proceed(g.id) is False

    def test_can_proceed_unblocked_after_resolve(self, manager: GoalStateManager) -> None:
        g = manager.create_goal(name="x")
        gate = manager.add_gate(g.id, "review")
        manager.resolve_gate(g.id, gate.id, GateStatus.APPROVED)
        assert manager.can_proceed(g.id) is True

    def test_can_proceed_blocked_by_call_quota(self, manager: GoalStateManager) -> None:
        g = manager.create_goal(name="x", quota_max_calls=2)
        manager.record_turn(g.id, agent="x", action="a")
        assert manager.can_proceed(g.id) is True
        manager.record_turn(g.id, agent="x", action="b")
        assert manager.can_proceed(g.id) is False

    def test_can_proceed_blocked_by_token_quota(self, manager: GoalStateManager) -> None:
        g = manager.create_goal(name="x", quota_max_tokens=100)
        manager.record_turn(g.id, agent="x", action="a", tokens_used=100)
        assert manager.can_proceed(g.id) is False

    def test_quota_remaining_unlimited(self, manager: GoalStateManager) -> None:
        g = manager.create_goal(name="x")
        assert manager.quota_remaining(g.id) == {"calls": -1, "tokens": -1}

    def test_quota_remaining_unknown_goal(self, manager: GoalStateManager) -> None:
        assert manager.quota_remaining("nope") == {"calls": 0, "tokens": 0}

    def test_quota_remaining_consumes_correctly(self, manager: GoalStateManager) -> None:
        g = manager.create_goal(name="x", quota_max_calls=5, quota_max_tokens=100)
        manager.record_turn(g.id, agent="x", action="a", tokens_used=30)
        manager.record_turn(g.id, agent="x", action="b", tokens_used=20)
        rem = manager.quota_remaining(g.id)
        assert rem == {"calls": 3, "tokens": 50}

    def test_quota_remaining_clamps_to_zero(self, manager: GoalStateManager) -> None:
        g = manager.create_goal(name="x", quota_max_calls=2)
        for _ in range(5):
            manager.record_turn(g.id, agent="x", action="a")
        rem = manager.quota_remaining(g.id)
        assert rem["calls"] == 0
        assert rem["tokens"] == -1


# ── Handoff (cross-session) ────────────────────────────────────────────────


class TestHandoff:
    def test_export_handoff_shape(self, manager: GoalStateManager) -> None:
        g = manager.create_goal(name="ship", project="Asgard")
        manager.record_turn(g.id, agent="Skadi", action="analyze", evidence="ok")
        manager.add_todo(g.id, "write tests")
        manager.add_gate(g.id, "ship?")

        handoff = manager.export_handoff(g.id)
        assert handoff["handoff_version"] == "1.0"
        assert handoff["goal_id"] == g.id
        assert handoff["name"] == "ship"
        assert handoff["project"] == "Asgard"
        assert handoff["status"] == "active"
        assert handoff["summary"]["total_turns"] == 1
        assert handoff["summary"]["last_agent"] == "Skadi"
        assert handoff["summary"]["last_action"] == "analyze"
        assert handoff["summary"]["last_evidence"] == "ok"
        assert handoff["summary"]["completion_pct"] == 0.0
        assert len(handoff["pending_gates"]) == 1
        assert len(handoff["open_todos"]) == 1
        assert handoff["full_state"]["id"] == g.id

    def test_export_handoff_excludes_completed_todos(self, manager: GoalStateManager) -> None:
        g = manager.create_goal(name="x")
        open_t = manager.add_todo(g.id, "open")
        closed_t = manager.add_todo(g.id, "closed")
        manager.complete_todo(g.id, closed_t.id)
        handoff = manager.export_handoff(g.id)
        assert len(handoff["open_todos"]) == 1
        assert handoff["open_todos"][0]["id"] == open_t.id

    def test_export_handoff_excludes_resolved_gates(self, manager: GoalStateManager) -> None:
        g = manager.create_goal(name="x")
        pending_g = manager.add_gate(g.id, "still pending")
        resolved_g = manager.add_gate(g.id, "resolved")
        manager.resolve_gate(g.id, resolved_g.id, GateStatus.APPROVED)
        handoff = manager.export_handoff(g.id)
        pending_ids = {g["id"] for g in handoff["pending_gates"]}
        assert pending_g.id in pending_ids
        assert resolved_g.id not in pending_ids

    def test_export_handoff_unknown_goal(self, manager: GoalStateManager) -> None:
        assert manager.export_handoff("nope") == {}

    def test_export_handoff_empty_turns(self, manager: GoalStateManager) -> None:
        g = manager.create_goal(name="x")
        handoff = manager.export_handoff(g.id)
        assert handoff["summary"]["total_turns"] == 0
        assert handoff["summary"]["last_agent"] is None
        assert handoff["summary"]["last_action"] is None

    def test_import_handoff_round_trip(self, manager: GoalStateManager) -> None:
        g = manager.create_goal(name="x", project="Asgard", quota_max_calls=5)
        manager.record_turn(g.id, agent="Skadi", action="analyze", tokens_used=10)
        manager.add_todo(g.id, "follow-up")
        gate = manager.add_gate(g.id, "review")
        manager.resolve_gate(g.id, gate.id, GateStatus.APPROVED)

        handoff = manager.export_handoff(g.id)

        # Import into a fresh manager (simulates new session)
        new_manager = GoalStateManager(storage_dir=g.id and "x" or "anything")
        # Simulate with a fresh tmp dir to keep test isolation
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            fresh = GoalStateManager(storage_dir=Path(tmp))
            imported = fresh.import_handoff(handoff)
            assert imported.id == g.id
            assert imported.name == "x"
            assert imported.project == "Asgard"
            assert imported.quota_max_calls == 5
            assert len(imported.turns) == 1
            assert len(imported.gates) == 1
            assert len(imported.todos) == 1

    def test_import_handoff_creates_persistence(
        self, manager: GoalStateManager, storage: Path
    ) -> None:
        g = manager.create_goal(name="x")
        handoff = manager.export_handoff(g.id)
        # Import onto a fresh manager in the same dir (overwrites)
        manager.import_handoff(handoff)
        assert (storage / f"{g.id}.json").exists()


# ── Stats / Aggregation ────────────────────────────────────────────────────


class TestStats:
    def test_stats_empty(self, manager: GoalStateManager) -> None:
        s = manager.stats()
        assert s["total_goals"] == 0
        assert s["by_status"] == {}
        assert s["total_turns"] == 0
        assert s["total_gates"] == 0
        assert s["total_todos"] == 0
        assert "storage_dir" in s

    def test_stats_with_content(self, manager: GoalStateManager) -> None:
        a = manager.create_goal(name="a")
        manager.create_goal(name="b")
        manager.update_status(a.id, GoalStatus.COMPLETED)
        manager.record_turn(a.id, agent="x", action="y")
        manager.add_gate(a.id, "g")
        manager.add_todo(a.id, "t")

        s = manager.stats()
        assert s["total_goals"] == 2
        assert s["by_status"] == {"completed": 1, "active": 1}
        assert s["total_turns"] == 1
        assert s["total_gates"] == 1
        assert s["total_todos"] == 1


# ── Persistence integration ─────────────────────────────────────────────────


class TestPersistence:
    def test_reload_after_create(self, manager: GoalStateManager, storage: Path) -> None:
        g = manager.create_goal(name="x", description="y", project="z")
        # New manager over same dir → should see the existing goal on disk
        other = GoalStateManager(storage_dir=storage)
        loaded = other.get_goal(g.id)
        assert loaded is not None
        assert loaded.name == "x"
        assert loaded.description == "y"
        assert loaded.project == "z"

    def test_corrupt_file_is_skipped(self, manager: GoalStateManager, storage: Path) -> None:
        # Write garbage that Goal.from_dict cannot parse
        (storage / "corrupt.json").write_text("{not valid json")
        # Reload — should not crash, should not load the corrupt file
        other = GoalStateManager(storage_dir=storage)
        assert other.get_goal("corrupt") is None

    def test_default_storage_dir_uses_ygg_root(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("YGGDRASIL_ROOT", str(tmp_path))
        m = GoalStateManager()  # no explicit dir
        assert str(tmp_path) in str(m._storage_dir)
        assert m._storage_dir == tmp_path / ".ygg" / "goals"


# ── Concurrency ─────────────────────────────────────────────────────────────


class TestConcurrency:
    def test_concurrent_writes_dont_corrupt(
        self, manager: GoalStateManager
    ) -> None:
        g = manager.create_goal(name="x")

        def worker(start: int) -> None:
            for i in range(start, start + 10):
                manager.record_turn(g.id, agent=f"a{i}", action="x", tokens_used=1)

        threads = [threading.Thread(target=worker, args=(i * 100,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        goal = manager.get_goal(g.id)
        assert goal is not None
        # No crashes, all 40 turns recorded (lock serialised writes)
        assert len(goal.turns) == 40
        assert goal.quota_used_calls == 40
        assert goal.quota_used_tokens == 40

    def test_concurrent_reads_safe(self, manager: GoalStateManager) -> None:
        g = manager.create_goal(name="x")
        errors: list[BaseException] = []

        def reader() -> None:
            try:
                for _ in range(50):
                    manager.get_goal(g.id)
                    manager.list_goals()
                    manager.stats()
                    manager.quota_remaining(g.id)
            except BaseException as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=reader) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
