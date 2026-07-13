"""GoalState — loop engineering for long-running AI agents.

Inspired by LoopX (huangruiteng/loopx) and Moryn (Richardyu114).
Provides a control plane for persisting dynamic goal state across agent turns:
    - Goal registry with active/inactive states
    - Run history and evidence tracking
    - Human gate checkpoints (wait-for-human markers)
    - Handoff state for cross-session resumption
    - Quota tracking (compute budget per goal)
    - Todo lists scoped to each goal

Usage::

    from lilith_core.goal_state import GoalStateManager, Goal, GoalStatus

    manager = GoalStateManager()

    # Create a new goal
    goal = manager.create_goal(
        name="Refactor authentication",
        description="Migrate from JWT to session-based auth",
        project="Asgard/lilith-api",
        quota_max_calls=100,
    )

    # Record an agent turn
    manager.record_turn(goal.id, agent="Skadi", action="analyze", evidence="Found 12 JWT usages")

    # Add a human gate
    manager.add_gate(goal.id, "Review migration plan before proceeding")

    # Check if goal can proceed (gates cleared + quota available)
    if manager.can_proceed(goal.id):
        manager.record_turn(goal.id, agent="Skadi", action="execute", evidence="Refactored 3 files")

    # Mark complete
    manager.update_status(goal.id, GoalStatus.COMPLETED)

    # Handoff: serialize goal state for another agent/session
    handoff = manager.export_handoff(goal.id)
    # ... later, in another session ...
    manager.import_handoff(handoff)
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger("lilith.goal_state")


# ── Enums ────────────────────────────────────────────────────────────────────


class GoalStatus(Enum):
    """Lifecycle states for a goal."""

    ACTIVE = "active"
    PAUSED = "paused"       # Waiting for human input or external signal
    BLOCKED = "blocked"     # Hit a gate or quota limit
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class GateStatus(Enum):
    """States for a human gate checkpoint."""

    PENDING = "pending"     # Waiting for human review
    APPROVED = "approved"   # Human approved, can proceed
    REJECTED = "rejected"   # Human rejected, abort or retry
    SKIPPED = "skipped"     # Auto-passed (e.g., test-only execution)


# ── Data classes ─────────────────────────────────────────────────────────────


@dataclass
class TurnRecord:
    """A single agent turn within a goal."""

    agent: str
    action: str
    evidence: str = ""
    timestamp: float = field(default_factory=time.time)
    tokens_used: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "action": self.action,
            "evidence": self.evidence,
            "timestamp": self.timestamp,
            "tokens_used": self.tokens_used,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TurnRecord:
        return cls(
            agent=data["agent"],
            action=data["action"],
            evidence=data.get("evidence", ""),
            timestamp=data.get("timestamp", time.time()),
            tokens_used=data.get("tokens_used", 0),
            metadata=data.get("metadata", {}),
        )


@dataclass
class Gate:
    """A human checkpoint within a goal."""

    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    description: str = ""
    status: GateStatus = GateStatus.PENDING
    created_at: float = field(default_factory=time.time)
    resolved_at: float | None = None
    resolved_by: str | None = None
    resolution_note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "status": self.status.value,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "resolved_by": self.resolved_by,
            "resolution_note": self.resolution_note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Gate:
        return cls(
            id=data.get("id", str(uuid.uuid4())[:8]),
            description=data.get("description", ""),
            status=GateStatus(data.get("status", "pending")),
            created_at=data.get("created_at", time.time()),
            resolved_at=data.get("resolved_at"),
            resolved_by=data.get("resolved_by"),
            resolution_note=data.get("resolution_note", ""),
        )


@dataclass
class TodoItem:
    """A todo item scoped to a goal."""

    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    description: str = ""
    done: bool = False
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    assigned_agent: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "done": self.done,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "assigned_agent": self.assigned_agent,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TodoItem:
        return cls(
            id=data.get("id", str(uuid.uuid4())[:8]),
            description=data.get("description", ""),
            done=data.get("done", False),
            created_at=data.get("created_at", time.time()),
            completed_at=data.get("completed_at"),
            assigned_agent=data.get("assigned_agent"),
        )


@dataclass
class Goal:
    """A long-running goal with full state tracking.

    Attributes:
        id: Unique goal identifier.
        name: Short human-readable name.
        description: Detailed description of the goal.
        project: Associated project/realm (e.g., "Asgard/lilith-api").
        status: Current lifecycle state.
        created_at: Timestamp when the goal was created.
        updated_at: Timestamp of last modification.
        turns: Chronological list of agent turns.
        gates: List of human checkpoints.
        todos: List of todo items.
        quota_max_calls: Maximum allowed agent calls.
        quota_used_calls: Number of calls consumed.
        quota_max_tokens: Maximum allowed tokens.
        quota_used_tokens: Number of tokens consumed.
        metadata: Free-form key-value storage.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = ""
    description: str = ""
    project: str = ""
    status: GoalStatus = GoalStatus.ACTIVE
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    turns: list[TurnRecord] = field(default_factory=list)
    gates: list[Gate] = field(default_factory=list)
    todos: list[TodoItem] = field(default_factory=list)
    quota_max_calls: int = 0  # 0 = unlimited
    quota_used_calls: int = 0
    quota_max_tokens: int = 0  # 0 = unlimited
    quota_used_tokens: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "project": self.project,
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "turns": [t.to_dict() for t in self.turns],
            "gates": [g.to_dict() for g in self.gates],
            "todos": [t.to_dict() for t in self.todos],
            "quota_max_calls": self.quota_max_calls,
            "quota_used_calls": self.quota_used_calls,
            "quota_max_tokens": self.quota_max_tokens,
            "quota_used_tokens": self.quota_used_tokens,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Goal:
        return cls(
            id=data.get("id", str(uuid.uuid4())[:8]),
            name=data.get("name", ""),
            description=data.get("description", ""),
            project=data.get("project", ""),
            status=GoalStatus(data.get("status", "active")),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            turns=[TurnRecord.from_dict(t) for t in data.get("turns", [])],
            gates=[Gate.from_dict(g) for g in data.get("gates", [])],
            todos=[TodoItem.from_dict(t) for t in data.get("todos", [])],
            quota_max_calls=data.get("quota_max_calls", 0),
            quota_used_calls=data.get("quota_used_calls", 0),
            quota_max_tokens=data.get("quota_max_tokens", 0),
            quota_used_tokens=data.get("quota_used_tokens", 0),
            metadata=data.get("metadata", {}),
        )

    @property
    def is_blocked(self) -> bool:
        """True if any gate is pending or quota is exhausted."""
        if any(g.status == GateStatus.PENDING for g in self.gates):
            return True
        if self.quota_max_calls > 0 and self.quota_used_calls >= self.quota_max_calls:
            return True
        if self.quota_max_tokens > 0 and self.quota_used_tokens >= self.quota_max_tokens:
            return True
        return False

    @property
    def completion_pct(self) -> float:
        """Percentage of todos completed (0.0–1.0)."""
        if not self.todos:
            return 0.0
        done = sum(1 for t in self.todos if t.done)
        return done / len(self.todos)

    @property
    def last_turn(self) -> TurnRecord | None:
        """Most recent turn, or None if no turns recorded."""
        return self.turns[-1] if self.turns else None


# ── GoalStateManager ─────────────────────────────────────────────────────────


class GoalStateManager:
    """Central registry and persistence for long-running agent goals.

    Provides CRUD for goals, turn recording, gate management, todo tracking,
    quota enforcement, and handoff serialization.

    Args:
        storage_dir: Directory for JSON persistence. If None, uses
            ``$YGGDRASIL_ROOT/.ygg/goals`` or ``./.ygg/goals``.
    """

    def __init__(self, storage_dir: str | Path | None = None) -> None:
        self._goals: dict[str, Goal] = {}
        self._lock = threading.RLock()

        if storage_dir is None:
            root = os.environ.get("YGGDRASIL_ROOT", ".")
            storage_dir = Path(root) / ".ygg" / "goals"
        self._storage_dir = Path(storage_dir)
        self._storage_dir.mkdir(parents=True, exist_ok=True)

        self._load_all()

    # ── Persistence ──────────────────────────────────────────────────────────

    def _goal_path(self, goal_id: str) -> Path:
        return self._storage_dir / f"{goal_id}.json"

    def _save(self, goal: Goal) -> None:
        path = self._goal_path(goal.id)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(goal.to_dict(), f, indent=2, ensure_ascii=False)

    def _load(self, goal_id: str) -> Goal | None:
        path = self._goal_path(goal_id)
        if not path.exists():
            return None
        try:
            with open(path, encoding="utf-8") as f:
                return Goal.from_dict(json.load(f))
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.warning("Failed to load goal %s: %s", goal_id, exc)
            return None

    def _load_all(self) -> None:
        if not self._storage_dir.exists():
            return
        for path in self._storage_dir.glob("*.json"):
            goal_id = path.stem
            goal = self._load(goal_id)
            if goal is not None:
                self._goals[goal_id] = goal

    # ── CRUD ─────────────────────────────────────────────────────────────────

    def create_goal(
        self,
        name: str,
        description: str = "",
        project: str = "",
        quota_max_calls: int = 0,
        quota_max_tokens: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> Goal:
        """Create a new goal and persist it."""
        with self._lock:
            goal = Goal(
                name=name,
                description=description,
                project=project,
                quota_max_calls=quota_max_calls,
                quota_max_tokens=quota_max_tokens,
                metadata=metadata or {},
            )
            self._goals[goal.id] = goal
            self._save(goal)
            logger.info("Created goal %s: %s", goal.id, name)
            return goal

    def get_goal(self, goal_id: str) -> Goal | None:
        """Retrieve a goal by ID."""
        with self._lock:
            return self._goals.get(goal_id)

    def list_goals(
        self,
        status: GoalStatus | None = None,
        project: str | None = None,
    ) -> list[Goal]:
        """List goals, optionally filtered by status and/or project."""
        with self._lock:
            goals = list(self._goals.values())
        if status is not None:
            goals = [g for g in goals if g.status == status]
        if project is not None:
            goals = [g for g in goals if g.project == project]
        return goals

    def update_status(self, goal_id: str, status: GoalStatus) -> Goal | None:
        """Update the lifecycle status of a goal."""
        with self._lock:
            goal = self._goals.get(goal_id)
            if goal is None:
                return None
            goal.status = status
            goal.updated_at = time.time()
            self._save(goal)
            logger.info("Goal %s status → %s", goal_id, status.value)
            return goal

    def delete_goal(self, goal_id: str) -> bool:
        """Delete a goal and its persisted file."""
        with self._lock:
            goal = self._goals.pop(goal_id, None)
            if goal is None:
                return False
            path = self._goal_path(goal_id)
            if path.exists():
                path.unlink()
            logger.info("Deleted goal %s", goal_id)
            return True

    # ── Turn recording ───────────────────────────────────────────────────────

    def record_turn(
        self,
        goal_id: str,
        agent: str,
        action: str,
        evidence: str = "",
        tokens_used: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> Goal | None:
        """Record an agent turn against a goal."""
        with self._lock:
            goal = self._goals.get(goal_id)
            if goal is None:
                return None

            turn = TurnRecord(
                agent=agent,
                action=action,
                evidence=evidence,
                tokens_used=tokens_used,
                metadata=metadata or {},
            )
            goal.turns.append(turn)
            goal.quota_used_calls += 1
            goal.quota_used_tokens += tokens_used
            goal.updated_at = time.time()
            self._save(goal)
            logger.debug("Goal %s turn: %s/%s", goal_id, agent, action)
            return goal

    # ── Gate management ──────────────────────────────────────────────────────

    def add_gate(self, goal_id: str, description: str) -> Gate | None:
        """Add a human checkpoint to a goal."""
        with self._lock:
            goal = self._goals.get(goal_id)
            if goal is None:
                return None
            gate = Gate(description=description)
            goal.gates.append(gate)
            goal.updated_at = time.time()
            self._save(goal)
            logger.info("Goal %s gate added: %s", goal_id, description)
            return gate

    def resolve_gate(
        self,
        goal_id: str,
        gate_id: str,
        status: GateStatus,
        resolved_by: str = "",
        note: str = "",
    ) -> Gate | None:
        """Resolve a human gate."""
        with self._lock:
            goal = self._goals.get(goal_id)
            if goal is None:
                return None
            for gate in goal.gates:
                if gate.id == gate_id:
                    gate.status = status
                    gate.resolved_at = time.time()
                    gate.resolved_by = resolved_by
                    gate.resolution_note = note
                    goal.updated_at = time.time()
                    self._save(goal)
                    logger.info("Goal %s gate %s → %s", goal_id, gate_id, status.value)
                    return gate
            return None

    # ── Todo management ──────────────────────────────────────────────────────

    def add_todo(
        self,
        goal_id: str,
        description: str,
        assigned_agent: str | None = None,
    ) -> TodoItem | None:
        """Add a todo item to a goal."""
        with self._lock:
            goal = self._goals.get(goal_id)
            if goal is None:
                return None
            todo = TodoItem(description=description, assigned_agent=assigned_agent)
            goal.todos.append(todo)
            goal.updated_at = time.time()
            self._save(goal)
            return todo

    def complete_todo(self, goal_id: str, todo_id: str) -> TodoItem | None:
        """Mark a todo as done."""
        with self._lock:
            goal = self._goals.get(goal_id)
            if goal is None:
                return None
            for todo in goal.todos:
                if todo.id == todo_id:
                    todo.done = True
                    todo.completed_at = time.time()
                    goal.updated_at = time.time()
                    self._save(goal)
                    return todo
            return None

    def remove_todo(self, goal_id: str, todo_id: str) -> bool:
        """Remove a todo from a goal."""
        with self._lock:
            goal = self._goals.get(goal_id)
            if goal is None:
                return False
            original_len = len(goal.todos)
            goal.todos = [t for t in goal.todos if t.id != todo_id]
            if len(goal.todos) < original_len:
                goal.updated_at = time.time()
                self._save(goal)
                return True
            return False

    # ── Quota / flow control ───────────────────────────────────────────────

    def can_proceed(self, goal_id: str) -> bool:
        """Check if a goal is allowed to execute another turn.

        Returns False if:
            - Goal does not exist
            - Goal status is not ACTIVE
            - Any gate is PENDING
            - Quota exhausted (calls or tokens)
        """
        with self._lock:
            goal = self._goals.get(goal_id)
            if goal is None:
                return False
            if goal.status != GoalStatus.ACTIVE:
                return False
            return not goal.is_blocked

    def quota_remaining(self, goal_id: str) -> dict[str, int]:
        """Return remaining quota for a goal.

        Returns ``{"calls": N, "tokens": M}``.  ``-1`` means unlimited.
        """
        with self._lock:
            goal = self._goals.get(goal_id)
            if goal is None:
                return {"calls": 0, "tokens": 0}
            calls = (
                -1
                if goal.quota_max_calls == 0
                else max(0, goal.quota_max_calls - goal.quota_used_calls)
            )
            tokens = (
                -1
                if goal.quota_max_tokens == 0
                else max(0, goal.quota_max_tokens - goal.quota_used_tokens)
            )
            return {"calls": calls, "tokens": tokens}

    # ── Handoff (cross-session) ────────────────────────────────────────────

    def export_handoff(self, goal_id: str) -> dict[str, Any]:
        """Export a goal as a handoff pack for cross-session resumption.

        Includes summary, next safe actions, open gates, incomplete todos,
        and full goal state.
        """
        with self._lock:
            goal = self._goals.get(goal_id)
            if goal is None:
                return {}

            pending_gates = [g.to_dict() for g in goal.gates if g.status == GateStatus.PENDING]
            open_todos = [t.to_dict() for t in goal.todos if not t.done]
            last = goal.last_turn

            return {
                "handoff_version": "1.0",
                "goal_id": goal.id,
                "name": goal.name,
                "description": goal.description,
                "project": goal.project,
                "status": goal.status.value,
                "summary": {
                    "total_turns": len(goal.turns),
                    "last_agent": last.agent if last else None,
                    "last_action": last.action if last else None,
                    "last_evidence": last.evidence if last else None,
                    "completion_pct": goal.completion_pct,
                },
                "pending_gates": pending_gates,
                "open_todos": open_todos,
                "quota_remaining": self.quota_remaining(goal_id),
                "full_state": goal.to_dict(),
            }

    def import_handoff(self, data: dict[str, Any]) -> Goal:
        """Import a handoff pack, creating or updating the goal."""
        with self._lock:
            full_state = data.get("full_state", data)
            goal = Goal.from_dict(full_state)
            goal.updated_at = time.time()
            self._goals[goal.id] = goal
            self._save(goal)
            logger.info("Imported handoff for goal %s", goal.id)
            return goal

    # ── Stats ──────────────────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        """Return aggregate statistics across all goals."""
        with self._lock:
            goals = list(self._goals.values())
        total = len(goals)
        by_status: dict[str, int] = {}
        for g in goals:
            by_status[g.status.value] = by_status.get(g.status.value, 0) + 1
        return {
            "total_goals": total,
            "by_status": by_status,
            "total_turns": sum(len(g.turns) for g in goals),
            "total_gates": sum(len(g.gates) for g in goals),
            "total_todos": sum(len(g.todos) for g in goals),
            "storage_dir": str(self._storage_dir),
        }
