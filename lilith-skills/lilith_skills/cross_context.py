"""Cross-cutting .ygg context — typed access to the agent ecosystem's shared state.

While :class:`lilith_skills.project_context.ProjectContext` owns the
*intra-project* working set (CURRENT.md, TASKS.md, LOG.md, ...), the
agent ecosystem — the one Skadi, Lilith and the Vanaheim personas all
share — keeps a second tier of *cross-cutting* state inside the same
``.ygg/`` directory:

    .ygg/
        goals/<id>.json     — Goals tracked across the ecosystem
        handoffs/<id>.json  — Compact handoff packs for suspended goals
        audit.jsonl         — Append-only policy / tool audit trail
        policies.yaml       — Default policy rule set
        workflows/*.yaml    — Named workflow definitions (steps + gates)

These were previously read as raw JSON from the cron jobs and ygg.py
helpers. This module unifies them behind a single typed facade so the
rest of the ecosystem (CLI, agents, tests) can:

    - iterate goals/handoffs/workflows with a stable API,
    - query the audit log with filters (agent/policy/action/time),
    - eval a policy rule against a tool-call context,
    - render a workflow's steps as a planning checklist.

Inspired by Eter-Agents' .eter and Aether-Agents' .aether context
conventions, but extended with the cross-cutting working set that
Yggdrasil evolved organically during overnight cron runs.

Usage::

    from pathlib import Path
    from lilith_skills.cross_context import CrossContext

    cx = CrossContext(Path.home() / "Yggdrasil")

    # Goals
    for g in cx.goals.list():
        print(g.name, g.status)
    g = cx.goals.create(name="ship-it", project="Asgard/lilith-core")
    g.add_turn("Skadi", "analyze", "scanned coverage")
    cx.handoffs.write_for(g)   # snapshot the goal as a handoff pack

    # Audit log
    cx.audit.append(
        policy="deny-dangerous-shell",
        agent="Odin",
        hook_type="pre_tool_call",
        action="deny",
        note="shell blocked",
        data={"tool_name": "shell_exec"},
    )
    for ev in cx.audit.filter(action="deny", since="2026-07-01"):
        print(ev.policy, ev.agent)

    # Policies
    pol = cx.policies
    for rule in pol.matching("pre_tool_call", {"tool_name": "shell_exec"}):
        print(rule.name, rule.action)

    # Workflows
    wf = cx.workflows.get("bug-fix")
    for step in wf.steps:
        print(step.name, step.intent)
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Iterator

# ── Optional YAML ─────────────────────────────────────────────────────────────
try:  # pragma: no cover — import guard
    import yaml as _yaml  # type: ignore[import-untyped]

    _HAVE_YAML = True
except ImportError:  # pragma: no cover — fallback when PyYAML is absent
    _yaml = None  # type: ignore[assignment]
    _HAVE_YAML = False


# ── File conventions ──────────────────────────────────────────────────────────

GOALS_DIR = "goals"
HANDOFFS_DIR = "handoffs"
WORKFLOWS_DIR = "workflows"
AUDIT_FILE = "audit.jsonl"
POLICIES_FILE = "policies.yaml"
CONTEXT_FILE = "CONTEXT.json"  # shared with ProjectContext

# ── Helpers ───────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now().isoformat()


def _parse_ts(ts: str | float | int | None) -> datetime | None:
    """Best-effort parse of an ISO timestamp, epoch seconds, or epoch millis.

    Returns ``None`` for unparseable inputs so callers can skip them.
    """
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        # Heuristic: > 10^12 → milliseconds, else seconds
        if ts > 1_000_000_000_000:
            ts = ts / 1000.0
        try:
            return datetime.fromtimestamp(float(ts))
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(ts, str):
        s = ts.strip()
        if not s:
            return None
        # Strip trailing Z so fromisoformat handles it on older Pythons.
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(s)
        except ValueError:
            pass
        # Last resort: epoch-as-string
        try:
            return datetime.fromtimestamp(float(s))
        except (OverflowError, OSError, ValueError):
            return None
    return None


def _json_load(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _yaml_load(text: str) -> Any:
    if _HAVE_YAML:
        return _yaml.safe_load(text) or {}
    # Ultra-minimal fallback: parse one level of `key: value` mappings.
    # Used only when PyYAML is unavailable (should not happen in production).
    return _mini_yaml_load(text)


def _mini_yaml_load(text: str) -> Any:
    """Tiny fallback YAML loader for shallow ``key: value`` mappings.

    Supports the subset we need for ``policies.yaml`` / ``workflows/*.yaml``:
        - top-level ``key: value``
        - nested mappings via indents
        - list items via ``- value`` (or ``- key: value``)
        - inline ``[a, b, c]`` lists

    The parser tracks (indent, container) pairs on a stack. When a list
    item is opened with ``-``, a synthetic key/indent is pushed for the
    item's body so subsequent indented lines are appended to it.
    """
    root: dict[str, Any] = {}

    def _coerce(v: str) -> Any:
        s = v.strip()
        if s.startswith('"') and s.endswith('"'):
            return s[1:-1]
        if s.startswith("'") and s.endswith("'"):
            return s[1:-1]
        if s.lower() in ("true", "yes"):
            return True
        if s.lower() in ("false", "no"):
            return False
        if s.lower() in ("null", "~", ""):
            return None
        if s.startswith("[") and s.endswith("]"):
            inner = s[1:-1].strip()
            if not inner:
                return []
            return [_coerce(p) for p in inner.split(",")]
        try:
            if "." in s:
                return float(s)
            return int(s)
        except ValueError:
            return s

    lines = text.splitlines()
    # Pre-compute the index of the next non-blank, non-comment line for
    # each line (used to peek what kind of container follows a bare key).
    next_meaningful: list[int] = [-1] * len(lines)
    nxt = -1
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip() and not lines[i].lstrip().startswith("#"):
            next_meaningful[i] = nxt
            nxt = i
        else:
            next_meaningful[i] = nxt

    stack: list[tuple[int, Any]] = [(-1, root)]

    for i, raw in enumerate(lines):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        # Pop the stack until we find a strictly shallower parent.
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if not stack:
            stack = [(-1, root)]
        _, parent = stack[-1]
        stripped = raw.lstrip(" ")

        if stripped.startswith("- "):
            if not isinstance(parent, list):
                # Stray list item; ignore
                continue
            item_text = stripped[2:].strip()
            if ":" in item_text and not item_text.startswith('"'):
                key, _, val = item_text.partition(":")
                key = key.strip()
                val = val.strip()
                if not val:
                    # Open a new dict item with no value
                    item: dict[str, Any] = {}
                    parent.append(item)
                    # Push the item at the dash's own indent so subsequent
                    # deeper-indented keys attach to it.
                    stack.append((indent, item))
                else:
                    item = {key: _coerce(val)}
                    parent.append(item)
                    # Same: push at dash indent for sibling keys.
                    stack.append((indent, item))
            else:
                parent.append(_coerce(item_text))
            continue

        if ":" in stripped:
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip()
            if not val:
                # Bare key — decide whether it opens a list or a dict by
                # peeking the next meaningful line.
                container: Any = {}
                nxt = next_meaningful[i]
                if 0 <= nxt < len(lines):
                    peek = lines[nxt]
                    if peek.lstrip().startswith("- "):
                        container = []
                if isinstance(parent, dict):
                    parent[key] = container
                stack.append((indent, container))
            else:
                if isinstance(parent, dict):
                    parent[key] = _coerce(val)
        # else: ignore malformed line

    return root


# ═════════════════════════════════════════════════════════════════════════════
# Goals
# ═════════════════════════════════════════════════════════════════════════════


@dataclass
class GoalTurn:
    """A single turn inside a goal's execution."""

    agent: str
    action: str
    evidence: str = ""
    timestamp: str = field(default_factory=_now_iso)
    tokens_used: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "action": self.action,
            "evidence": self.evidence,
            "timestamp": self.timestamp,
            "tokens_used": self.tokens_used,
            "metadata": dict(self.metadata),
        }


@dataclass
class GoalGate:
    """A quality-gate / decision-point inside a goal."""

    id: str
    description: str
    status: str = "pending"  # pending | approved | rejected | skipped
    created_at: str = field(default_factory=_now_iso)
    resolved_at: str | None = None
    resolved_by: str | None = None
    resolution_note: str = ""

    def resolve(self, status: str, *, by: str = "skadi", note: str = "") -> None:
        """Mark this gate as resolved (approved/rejected/skipped)."""
        if status not in ("approved", "rejected", "skipped"):
            raise ValueError(f"invalid gate status: {status!r}")
        self.status = status
        self.resolved_at = _now_iso()
        self.resolved_by = by
        self.resolution_note = note

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "status": self.status,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "resolved_by": self.resolved_by,
            "resolution_note": self.resolution_note,
        }


@dataclass
class Goal:
    """A cross-cutting goal tracked by the agent ecosystem.

    A goal is the unit of long-lived intent: it owns an ordered list of
    turns (what agents did), pending quality-gates (decisions still
    awaiting human / cross-agent review), todos, and a quota. Goals are
    persisted as ``.ygg/goals/<id>.json`` and have a corresponding
    handoff in ``.ygg/handoffs/<id>.json`` that summarises them for
    fast loading.
    """

    id: str
    name: str
    description: str = ""
    project: str = ""
    status: str = "active"  # active | paused | done | failed
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    turns: list[GoalTurn] = field(default_factory=list)
    gates: list[GoalGate] = field(default_factory=list)
    todos: list[dict[str, Any]] = field(default_factory=list)
    quota_max_calls: int = 10
    quota_used_calls: int = 0
    quota_max_tokens: int = 0
    quota_used_tokens: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    # ── Convenience ─────────────────────────────────────────────────────

    def add_turn(
        self,
        agent: str,
        action: str,
        evidence: str = "",
        tokens: int = 0,
        **metadata: Any,
    ) -> GoalTurn:
        turn = GoalTurn(
            agent=agent,
            action=action,
            evidence=evidence,
            tokens_used=tokens,
            metadata=dict(metadata),
        )
        self.turns.append(turn)
        self.quota_used_calls += 1
        self.quota_used_tokens += tokens
        self.updated_at = time.time()
        return turn

    def add_gate(self, description: str) -> GoalGate:
        gate = GoalGate(id=uuid.uuid4().hex[:8], description=description)
        self.gates.append(gate)
        self.updated_at = time.time()
        return gate

    def pending_gates(self) -> list[GoalGate]:
        return [g for g in self.gates if g.status == "pending"]

    def add_todo(self, text: str) -> dict[str, Any]:
        todo = {"id": uuid.uuid4().hex[:8], "text": text, "done": False}
        self.todos.append(todo)
        self.updated_at = time.time()
        return todo

    def complete_todo(self, todo_id: str) -> bool:
        for t in self.todos:
            if t.get("id") == todo_id:
                t["done"] = True
                self.updated_at = time.time()
                return True
        return False

    @property
    def completion_pct(self) -> float:
        total = len(self.todos) + len(self.gates)
        if total == 0:
            # Use turn activity as a weak proxy
            return 1.0 if self.quota_used_calls >= self.quota_max_calls else 0.0
        done = sum(1 for t in self.todos if t.get("done"))
        done += sum(1 for g in self.gates if g.status in ("approved", "skipped"))
        return round(done / total, 4)

    def quota_remaining(self) -> dict[str, int]:
        return {
            "calls": max(0, self.quota_max_calls - self.quota_used_calls),
            "tokens": (
                max(0, self.quota_max_tokens - self.quota_used_tokens)
                if self.quota_max_tokens > 0
                else -1
            ),
        }

    # ── (De)serialisation ───────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "project": self.project,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "turns": [t.to_dict() for t in self.turns],
            "gates": [g.to_dict() for g in self.gates],
            "todos": list(self.todos),
            "quota_max_calls": self.quota_max_calls,
            "quota_used_calls": self.quota_used_calls,
            "quota_max_tokens": self.quota_max_tokens,
            "quota_used_tokens": self.quota_used_tokens,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Goal:
        if not isinstance(data, dict):
            return cls(id="unknown", name="unknown")
        turns = [GoalTurn(**t) for t in data.get("turns", []) if isinstance(t, dict)]
        gates = [GoalGate(**g) for g in data.get("gates", []) if isinstance(g, dict)]
        return cls(
            id=str(data.get("id") or uuid.uuid4().hex[:8]),
            name=str(data.get("name") or "unnamed"),
            description=str(data.get("description") or ""),
            project=str(data.get("project") or ""),
            status=str(data.get("status") or "active"),
            created_at=float(data.get("created_at") or time.time()),
            updated_at=float(data.get("updated_at") or time.time()),
            turns=turns,
            gates=gates,
            todos=list(data.get("todos") or []),
            quota_max_calls=int(data.get("quota_max_calls") or 10),
            quota_used_calls=int(data.get("quota_used_calls") or 0),
            quota_max_tokens=int(data.get("quota_max_tokens") or 0),
            quota_used_tokens=int(data.get("quota_used_tokens") or 0),
            metadata=dict(data.get("metadata") or {}),
        )


class GoalsStore:
    """Filesystem-backed store of :class:`Goal` objects.

    Each goal lives in its own file under ``.ygg/goals/<id>.json`` and is
    loaded / saved atomically. The store is intentionally simple — the
    filesystem IS the index, and a fresh ``list()`` walks the dir.
    """

    def __init__(self, ygg_dir: Path | str) -> None:
        self.ygg_dir = Path(ygg_dir)
        self.goals_dir = self.ygg_dir / GOALS_DIR

    @property
    def exists(self) -> bool:
        return self.goals_dir.exists()

    def _ensure(self) -> None:
        self.goals_dir.mkdir(parents=True, exist_ok=True)

    def list(self) -> list[Goal]:
        if not self.exists:
            return []
        out: list[Goal] = []
        for p in sorted(self.goals_dir.glob("*.json")):
            data = _json_load(p)
            if data:
                out.append(Goal.from_dict(data))
        return out

    def get(self, goal_id: str) -> Goal | None:
        path = self.goals_dir / f"{goal_id}.json"
        if not path.exists():
            return None
        data = _json_load(path)
        return Goal.from_dict(data) if data else None

    def create(
        self,
        name: str,
        *,
        project: str = "",
        description: str = "",
        quota_max_calls: int = 10,
        quota_max_tokens: int = 0,
        goal_id: str | None = None,
    ) -> Goal:
        self._ensure()
        gid = goal_id or uuid.uuid4().hex[:8]
        goal = Goal(
            id=gid,
            name=name,
            description=description,
            project=project,
            quota_max_calls=quota_max_calls,
            quota_max_tokens=quota_max_tokens,
        )
        self.save(goal)
        return goal

    def save(self, goal: Goal) -> None:
        self._ensure()
        goal.updated_at = time.time()
        path = self.goals_dir / f"{goal.id}.json"
        path.write_text(
            json.dumps(goal.to_dict(), indent=2),
            encoding="utf-8",
        )

    def delete(self, goal_id: str) -> bool:
        path = self.goals_dir / f"{goal_id}.json"
        if path.exists():
            path.unlink()
            return True
        return False

    def active(self) -> list[Goal]:
        return [g for g in self.list() if g.status == "active"]


# ═════════════════════════════════════════════════════════════════════════════
# Handoffs
# ═════════════════════════════════════════════════════════════════════════════


@dataclass
class HandoffPack:
    """Compact summary of a :class:`Goal` for fast cross-agent reload.

    Handoffs are derived from goals but live in their own file so an
    agent can load a small summary (a few KB) instead of the full
    history (which grows with every turn).
    """

    goal_id: str
    name: str
    description: str = ""
    project: str = ""
    status: str = "active"
    total_turns: int = 0
    last_agent: str = ""
    last_action: str = ""
    last_evidence: str = ""
    completion_pct: float = 0.0
    pending_gates: list[dict[str, Any]] = field(default_factory=list)
    open_todos: list[dict[str, Any]] = field(default_factory=list)
    quota_remaining: dict[str, int] = field(default_factory=dict)
    summary: str = ""
    handoff_version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "handoff_version": self.handoff_version,
            "goal_id": self.goal_id,
            "name": self.name,
            "description": self.description,
            "project": self.project,
            "status": self.status,
            "summary": {
                "total_turns": self.total_turns,
                "last_agent": self.last_agent,
                "last_action": self.last_action,
                "last_evidence": self.last_evidence,
                "completion_pct": self.completion_pct,
            },
            "pending_gates": list(self.pending_gates),
            "open_todos": list(self.open_todos),
            "quota_remaining": dict(self.quota_remaining),
        }

    @classmethod
    def from_goal(cls, goal: Goal) -> HandoffPack:
        last_turn = goal.turns[-1] if goal.turns else None
        return cls(
            goal_id=goal.id,
            name=goal.name,
            description=goal.description,
            project=goal.project,
            status=goal.status,
            total_turns=len(goal.turns),
            last_agent=last_turn.agent if last_turn else "",
            last_action=last_turn.action if last_turn else "",
            last_evidence=last_turn.evidence if last_turn else "",
            completion_pct=goal.completion_pct,
            pending_gates=[g.to_dict() for g in goal.pending_gates()],
            open_todos=[t for t in goal.todos if not t.get("done")],
            quota_remaining=goal.quota_remaining(),
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HandoffPack:
        if not isinstance(data, dict):
            return cls(goal_id="unknown", name="unknown")
        summary = data.get("summary") or {}
        return cls(
            handoff_version=str(data.get("handoff_version") or "1.0"),
            goal_id=str(data.get("goal_id") or "unknown"),
            name=str(data.get("name") or "unknown"),
            description=str(data.get("description") or ""),
            project=str(data.get("project") or ""),
            status=str(data.get("status") or "active"),
            total_turns=int(summary.get("total_turns") or 0),
            last_agent=str(summary.get("last_agent") or ""),
            last_action=str(summary.get("last_action") or ""),
            last_evidence=str(summary.get("last_evidence") or ""),
            completion_pct=float(summary.get("completion_pct") or 0.0),
            pending_gates=list(data.get("pending_gates") or []),
            open_todos=list(data.get("open_todos") or []),
            quota_remaining=dict(data.get("quota_remaining") or {}),
            summary="",  # derived; not persisted
        )


class HandoffsStore:
    """Filesystem-backed store of :class:`HandoffPack` summaries."""

    def __init__(self, ygg_dir: Path | str) -> None:
        self.ygg_dir = Path(ygg_dir)
        self.handoffs_dir = self.ygg_dir / HANDOFFS_DIR

    @property
    def exists(self) -> bool:
        return self.handoffs_dir.exists()

    def _ensure(self) -> None:
        self.handoffs_dir.mkdir(parents=True, exist_ok=True)

    def list(self) -> list[HandoffPack]:
        if not self.exists:
            return []
        out: list[HandoffPack] = []
        for p in sorted(self.handoffs_dir.glob("*.json")):
            data = _json_load(p)
            if data:
                out.append(HandoffPack.from_dict(data))
        return out

    def get(self, goal_id: str) -> HandoffPack | None:
        path = self.handoffs_dir / f"{goal_id}.json"
        if not path.exists():
            return None
        data = _json_load(path)
        return HandoffPack.from_dict(data) if data else None

    def write_for(self, goal: Goal) -> HandoffPack:
        self._ensure()
        pack = HandoffPack.from_goal(goal)
        path = self.handoffs_dir / f"{goal.id}.json"
        path.write_text(
            json.dumps(pack.to_dict(), indent=2),
            encoding="utf-8",
        )
        return pack

    def delete(self, goal_id: str) -> bool:
        path = self.handoffs_dir / f"{goal_id}.json"
        if path.exists():
            path.unlink()
            return True
        return False


# ═════════════════════════════════════════════════════════════════════════════
# Audit log
# ═════════════════════════════════════════════════════════════════════════════


@dataclass
class AuditEvent:
    """A single audit-log entry."""

    ts: str
    policy: str
    agent: str
    session: str = ""
    tool: str = ""
    message: str = ""
    hook_type: str = ""
    action: str = ""
    note: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def timestamp(self) -> datetime | None:
        return _parse_ts(self.ts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "policy": self.policy,
            "agent": self.agent,
            "session": self.session,
            "tool": self.tool,
            "message": self.message,
            "hook_type": self.hook_type,
            "action": self.action,
            "note": self.note,
            "data": dict(self.data),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AuditEvent:
        if not isinstance(data, dict):
            return cls(ts=_now_iso(), policy="", agent="")
        return cls(
            ts=str(data.get("ts") or _now_iso()),
            policy=str(data.get("policy") or ""),
            agent=str(data.get("agent") or ""),
            session=str(data.get("session") or ""),
            tool=str(data.get("tool") or ""),
            message=str(data.get("message") or ""),
            hook_type=str(data.get("hook_type") or ""),
            action=str(data.get("action") or ""),
            note=str(data.get("note") or ""),
            data=dict(data.get("data") or {}),
        )


class AuditLog:
    """Append/query the ``.ygg/audit.jsonl`` audit trail.

    The file is line-delimited JSON: each line is one :class:`AuditEvent`.
    Reads are streaming-friendly (we walk the file lazily) and writes
    are atomic (we append a single line, with a trailing newline).
    """

    def __init__(self, ygg_dir: Path | str) -> None:
        self.ygg_dir = Path(ygg_dir)
        self.path = self.ygg_dir / AUDIT_FILE

    @property
    def exists(self) -> bool:
        return self.path.exists()

    def _ensure(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.touch()

    def append(
        self,
        *,
        policy: str,
        agent: str,
        hook_type: str = "",
        action: str = "log",
        tool: str = "",
        session: str = "",
        message: str = "",
        note: str = "",
        data: dict[str, Any] | None = None,
        ts: str | None = None,
    ) -> AuditEvent:
        self._ensure()
        ev = AuditEvent(
            ts=ts or _now_iso(),
            policy=policy,
            agent=agent,
            session=session,
            tool=tool,
            message=message,
            hook_type=hook_type,
            action=action,
            note=note,
            data=dict(data or {}),
        )
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ev.to_dict(), ensure_ascii=False) + "\n")
        return ev

    def iter_all(self) -> Iterator[AuditEvent]:
        if not self.exists:
            return
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                yield AuditEvent.from_dict(data)

    def all(self) -> list[AuditEvent]:
        return list(self.iter_all())

    def filter(
        self,
        *,
        agent: str | None = None,
        policy: str | None = None,
        action: str | None = None,
        hook_type: str | None = None,
        since: str | int | float | datetime | None = None,
        until: str | int | float | datetime | None = None,
        limit: int | None = None,
    ) -> list[AuditEvent]:
        since_dt = _parse_ts(since) if since is not None else None
        until_dt = _parse_ts(until) if until is not None else None
        out: list[AuditEvent] = []
        for ev in self.iter_all():
            if agent is not None and ev.agent != agent:
                continue
            if policy is not None and ev.policy != policy:
                continue
            if action is not None and ev.action != action:
                continue
            if hook_type is not None and ev.hook_type != hook_type:
                continue
            ts_dt = ev.timestamp
            if since_dt is not None:
                if ts_dt is None or ts_dt < since_dt:
                    continue
            if until_dt is not None:
                if ts_dt is None or ts_dt > until_dt:
                    continue
            out.append(ev)
            if limit is not None and len(out) >= limit:
                break
        return out

    def count(self) -> int:
        if not self.exists:
            return 0
        n = 0
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    n += 1
        return n

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()
        self._ensure()


# ═════════════════════════════════════════════════════════════════════════════
# Policies
# ═════════════════════════════════════════════════════════════════════════════


VALID_RULE_ACTIONS = {"allow", "deny", "log", "flag"}
VALID_RULE_TYPES = {
    "tool_allowlist",
    "tool_denylist",
    "rate_limit",
    "token_budget",
    "regex",
    "always",
}


@dataclass
class PolicyRule:
    """A single policy rule loaded from ``policies.yaml``.

    Rules are intentionally permissive on the input side (we accept any
    extra keys) and strict on the output: ``matches(context)`` returns
    a bool, callers map action→effect.
    """

    name: str
    description: str = ""
    priority: int = 100
    action: str = "log"
    scope: str = "all"
    type: str = "always"
    enabled: bool = True
    raw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.action not in VALID_RULE_ACTIONS:
            # Don't crash — surface the bad value via enabled=False
            self.enabled = False
        if self.type not in VALID_RULE_TYPES:
            self.enabled = False

    def matches(self, context: dict[str, Any] | None = None) -> bool:
        """Return whether this rule fires against the given context.

        Supported rule types:
            - always: always matches
            - tool_allowlist: matches when ``tool_name`` IS in `tools`
            - tool_denylist: matches when ``tool_name`` IS in `tools`
            - rate_limit: matches when calls in window >= max_calls
            - token_budget: matches when total tokens >= max_tokens
            - regex: matches when ``field_name`` value matches ``pattern``
        """
        if not self.enabled:
            return False
        ctx = context or {}
        if self.type == "always":
            return True
        if self.type == "tool_allowlist":
            tools = self.raw.get("tools") or []
            return ctx.get("tool_name") in tools
        if self.type == "tool_denylist":
            tools = self.raw.get("tools") or []
            return ctx.get("tool_name") in tools
        if self.type == "rate_limit":
            max_calls = int(self.raw.get("max_calls") or 0)
            window = int(self.raw.get("window_seconds") or 0)
            actual = int(ctx.get("calls_in_window") or 0)
            return actual >= max_calls and max_calls > 0 and window > 0
        if self.type == "token_budget":
            max_tokens = int(self.raw.get("max_tokens") or 0)
            actual = int(ctx.get("tokens_used") or 0)
            return actual >= max_tokens and max_tokens > 0
        if self.type == "regex":
            field = self.raw.get("field_name") or "tool_name"
            pattern = self.raw.get("pattern")
            if not pattern:
                return False
            value = str(ctx.get(field) or "")
            try:
                return bool(re.search(pattern, value))
            except re.error:
                return False
        return False

    def to_dict(self) -> dict[str, Any]:
        return dict(self.raw) | {
            "name": self.name,
            "description": self.description,
            "priority": self.priority,
            "action": self.action,
            "scope": self.scope,
            "type": self.type,
            "enabled": self.enabled,
        }


@dataclass
class PolicySet:
    """A named set of :class:`PolicyRule` objects loaded from one YAML file."""

    name: str = "default"
    rules: list[PolicyRule] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    source: str = ""

    def matching(
        self, context: dict[str, Any] | None = None
    ) -> list[PolicyRule]:
        return sorted(
            (r for r in self.rules if r.matches(context)),
            key=lambda r: r.priority,
        )

    def by_action(self, action: str) -> list[PolicyRule]:
        return [r for r in self.rules if r.action == action]

    def by_name(self, name: str) -> PolicyRule | None:
        for r in self.rules:
            if r.name == name:
                return r
        return None

    def enabled(self) -> list[PolicyRule]:
        return [r for r in self.rules if r.enabled]


class PoliciesStore:
    """Loader for the ``.ygg/policies.yaml`` default policy set."""

    def __init__(self, ygg_dir: Path | str) -> None:
        self.ygg_dir = Path(ygg_dir)
        self.path = self.ygg_dir / POLICIES_FILE

    @property
    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> PolicySet:
        if not self.exists:
            return PolicySet(source=str(self.path))
        try:
            data = _yaml_load(self.path.read_text(encoding="utf-8")) or {}
        except OSError:
            return PolicySet(source=str(self.path))
        ps = PolicySet(
            name=str(data.get("name") or "default"),
            raw=data if isinstance(data, dict) else {},
            source=str(self.path),
        )
        for r in (data.get("policies") or []) if isinstance(data, dict) else []:
            if not isinstance(r, dict):
                continue
            ps.rules.append(
                PolicyRule(
                    name=str(r.get("name") or "unnamed"),
                    description=str(r.get("description") or ""),
                    priority=int(r.get("priority") or 100),
                    action=str(r.get("action") or "log"),
                    scope=str(r.get("scope") or "all"),
                    type=str(r.get("type") or "always"),
                    enabled=bool(r.get("enabled", True)),
                    raw=r,
                )
            )
        return ps

    def evaluate(
        self, context: dict[str, Any] | None = None
    ) -> tuple[str, PolicyRule | None]:
        """Evaluate rules and return ``(action, rule)`` for the highest-priority match.

        Returns ``("log", None)`` when no rule matches.
        """
        ps = self.load()
        matches = ps.matching(context)
        if not matches:
            return ("log", None)
        winner = matches[0]
        return (winner.action, winner)


# ═════════════════════════════════════════════════════════════════════════════
# Workflows
# ═════════════════════════════════════════════════════════════════════════════


@dataclass
class WorkflowStep:
    """One step inside a workflow definition."""

    name: str
    intent: str = "code"
    description: str = ""
    tools: list[str] = field(default_factory=list)
    gate: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "intent": self.intent,
            "description": self.description,
            "tools": list(self.tools),
            "gate": dict(self.gate),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowStep:
        if not isinstance(data, dict):
            return cls(name="unnamed")
        tools_val = data.get("tools") or []
        if isinstance(tools_val, str):
            tools_val = [t.strip() for t in tools_val.split(",") if t.strip()]
        return cls(
            name=str(data.get("name") or "unnamed"),
            intent=str(data.get("intent") or "code"),
            description=str(data.get("description") or ""),
            tools=list(tools_val),
            gate=dict(data.get("gate") or {}),
        )


@dataclass
class Workflow:
    """A named workflow definition loaded from ``.ygg/workflows/*.yaml``."""

    name: str
    description: str = ""
    version: str = "1.0"
    steps: list[WorkflowStep] = field(default_factory=list)
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "steps": [s.to_dict() for s in self.steps],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, source: str = "") -> Workflow:
        if not isinstance(data, dict):
            return cls(name="unnamed", source=source)
        steps = [
            WorkflowStep.from_dict(s)
            for s in (data.get("steps") or [])
            if isinstance(s, dict)
        ]
        return cls(
            name=str(data.get("name") or "unnamed"),
            description=str(data.get("description") or ""),
            version=str(data.get("version") or "1.0"),
            steps=steps,
            source=source,
        )


class WorkflowsStore:
    """Loader for ``.ygg/workflows/*.yaml``."""

    def __init__(self, ygg_dir: Path | str) -> None:
        self.ygg_dir = Path(ygg_dir)
        self.workflows_dir = self.ygg_dir / WORKFLOWS_DIR

    @property
    def exists(self) -> bool:
        return self.workflows_dir.exists()

    def list(self) -> list[Workflow]:
        if not self.exists:
            return []
        out: list[Workflow] = []
        for p in sorted(self.workflows_dir.glob("*.yaml")):
            wf = self.get(p.stem)
            if wf is not None:
                out.append(wf)
        return out

    def get(self, name: str) -> Workflow | None:
        path = self.workflows_dir / f"{name}.yaml"
        if not path.exists():
            return None
        try:
            data = _yaml_load(path.read_text(encoding="utf-8")) or {}
        except OSError:
            return None
        return Workflow.from_dict(data, source=str(path))

    def names(self) -> list[str]:
        return [w.name for w in self.list()]


# ═════════════════════════════════════════════════════════════════════════════
# CrossContext facade
# ═════════════════════════════════════════════════════════════════════════════


@dataclass
class CrossContext:
    """Unified facade over the cross-cutting .ygg working set.

    Aggregates the five sub-stores (goals, handoffs, audit, policies,
    workflows) so callers can do ``cx.goals.list()`` instead of wiring
    five separate constructors. The facade also exposes a couple of
    derived convenience methods (e.g. ``snapshot()``) that an agent can
    serialize into a single prompt-injection blob.
    """

    ygg_dir: Path

    def __init__(self, ygg_dir: Path | str) -> None:  # type: ignore[no-redef]
        self.ygg_dir = Path(ygg_dir)
        self.goals = GoalsStore(self.ygg_dir)
        self.handoffs = HandoffsStore(self.ygg_dir)
        self.audit = AuditLog(self.ygg_dir)
        self.policies = PoliciesStore(self.ygg_dir)
        self.workflows = WorkflowsStore(self.ygg_dir)

    @property
    def exists(self) -> bool:
        return self.ygg_dir.exists()

    def snapshot(self) -> dict[str, Any]:
        """Return a serialisable snapshot of all five sub-stores.

        Useful for tests, ``ygg context`` display, and for an agent
        that needs to "see" the full cross-cutting state in one shot.
        """
        return {
            "ygg_dir": str(self.ygg_dir),
            "goals": {
                "count": len(self.goals.list()),
                "active": [g.id for g in self.goals.active()],
            },
            "handoffs": {
                "count": len(self.handoffs.list()),
                "ids": [h.goal_id for h in self.handoffs.list()],
            },
            "audit": {
                "count": self.audit.count(),
            },
            "policies": {
                "path": str(self.policies.path),
                "exists": self.policies.exists,
                "rule_count": len(self.policies.load().rules),
            },
            "workflows": {
                "names": self.workflows.names(),
                "count": len(self.workflows.list()),
            },
        }

    def render_summary(self) -> str:
        """Return a one-screen, human-readable summary of cross-cutting state.

        Used by the ``ygg context`` CLI's ``summary`` action.
        """
        snap = self.snapshot()
        lines: list[str] = []
        lines.append(f"Ygg dir: {snap['ygg_dir']}")
        lines.append("")
        lines.append(
            f"Goals:    {snap['goals']['count']} total, "
            f"{len(snap['goals']['active'])} active "
            f"({', '.join(snap['goals']['active']) or '—'})"
        )
        lines.append(
            f"Handoffs: {snap['handoffs']['count']} total "
            f"({', '.join(snap['handoffs']['ids']) or '—'})"
        )
        lines.append(f"Audit:    {snap['audit']['count']} events")
        if snap["policies"]["exists"]:
            lines.append(
                f"Policies: {snap['policies']['rule_count']} rules "
                f"loaded from {snap['policies']['path']}"
            )
        else:
            lines.append("Policies: <none>")
        if snap["workflows"]["count"]:
            lines.append(
                f"Workflows: {snap['workflows']['count']} "
                f"({', '.join(snap['workflows']['names'])})"
            )
        else:
            lines.append("Workflows: <none>")
        return "\n".join(lines)


__all__ = [
    # helpers
    "_now_iso",
    "_parse_ts",
    "_yaml_load",
    # goals
    "Goal",
    "GoalTurn",
    "GoalGate",
    "GoalsStore",
    # handoffs
    "HandoffPack",
    "HandoffsStore",
    # audit
    "AuditEvent",
    "AuditLog",
    # policies
    "PolicyRule",
    "PolicySet",
    "PoliciesStore",
    "VALID_RULE_ACTIONS",
    "VALID_RULE_TYPES",
    # workflows
    "Workflow",
    "WorkflowStep",
    "WorkflowsStore",
    # facade
    "CrossContext",
    # constants
    "GOALS_DIR",
    "HANDOFFS_DIR",
    "WORKFLOWS_DIR",
    "AUDIT_FILE",
    "POLICIES_FILE",
    "CONTEXT_FILE",
]
