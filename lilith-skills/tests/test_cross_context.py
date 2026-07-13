"""Tests for the cross-cutting .ygg context facade."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from lilith_skills.cross_context import (
    AUDIT_FILE,
    GOALS_DIR,
    HANDOFFS_DIR,
    POLICIES_FILE,
    VALID_RULE_ACTIONS,
    VALID_RULE_TYPES,
    WORKFLOWS_DIR,
    AuditEvent,
    AuditLog,
    CrossContext,
    Goal,
    GoalGate,
    GoalTurn,
    GoalsStore,
    HandoffPack,
    HandoffsStore,
    PoliciesStore,
    PolicyRule,
    PolicySet,
    Workflow,
    WorkflowStep,
    WorkflowsStore,
    _mini_yaml_load,
    _now_iso,
    _parse_ts,
    _yaml_load,
)


# ═════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def ygg_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".ygg"
    d.mkdir()
    return d


@pytest.fixture
def populated_ygg(ygg_dir: Path) -> Path:
    """A .ygg dir that already has a single goal + handoff + audit + policies + 1 workflow."""
    # Goals dir + one goal file
    (ygg_dir / GOALS_DIR).mkdir()
    goal = {
        "id": "abc12345",
        "name": "smoke-test",
        "description": "test goal",
        "project": "Asgard/lilith-core",
        "status": "active",
        "created_at": 1700000000.0,
        "updated_at": 1700000100.0,
        "turns": [
            {
                "agent": "Skadi",
                "action": "analyze",
                "evidence": "scanned coverage",
                "timestamp": "2024-01-01T00:00:00",
                "tokens_used": 0,
                "metadata": {},
            }
        ],
        "gates": [
            {
                "id": "g0001",
                "description": "review the diff?",
                "status": "pending",
                "created_at": "2024-01-01T00:00:00",
                "resolved_at": None,
                "resolved_by": None,
                "resolution_note": "",
            }
        ],
        "todos": [],
        "quota_max_calls": 5,
        "quota_used_calls": 1,
        "quota_max_tokens": 0,
        "quota_used_tokens": 0,
        "metadata": {},
    }
    (ygg_dir / GOALS_DIR / "abc12345.json").write_text(json.dumps(goal), encoding="utf-8")

    # Handoffs dir + one handoff
    (ygg_dir / HANDOFFS_DIR).mkdir()
    handoff = {
        "handoff_version": "1.0",
        "goal_id": "abc12345",
        "name": "smoke-test",
        "description": "test goal",
        "project": "Asgard/lilith-core",
        "status": "active",
        "summary": {
            "total_turns": 1,
            "last_agent": "Skadi",
            "last_action": "analyze",
            "last_evidence": "scanned coverage",
            "completion_pct": 0.0,
        },
        "pending_gates": [
            {
                "id": "g0001",
                "description": "review the diff?",
                "status": "pending",
                "created_at": "2024-01-01T00:00:00",
                "resolved_at": None,
                "resolved_by": None,
                "resolution_note": "",
            }
        ],
        "open_todos": [],
        "quota_remaining": {"calls": 4, "tokens": -1},
    }
    (ygg_dir / HANDOFFS_DIR / "abc12345.json").write_text(
        json.dumps(handoff), encoding="utf-8"
    )

    # Audit log
    audit_lines = [
        {
            "ts": "2024-01-01T10:00:00",
            "policy": "deny-dangerous-shell",
            "agent": "Odin",
            "session": "s1",
            "tool": "shell_exec",
            "hook_type": "pre_tool_call",
            "action": "deny",
            "note": "blocked",
            "data": {"tool_name": "shell_exec"},
        },
        {
            "ts": "2024-01-02T10:00:00",
            "policy": "rate-limit-all-agents",
            "agent": "Mimir",
            "session": "s2",
            "tool": "read_file",
            "hook_type": "pre_tool_call",
            "action": "allow",
            "note": "",
            "data": {"tool_name": "read_file"},
        },
        {
            "ts": "2024-01-03T10:00:00",
            "policy": "audit-everything",
            "agent": "Odin",
            "session": "s1",
            "tool": "read_file",
            "hook_type": "post_tool_call",
            "action": "log",
            "note": "ok",
            "data": {},
        },
    ]
    (ygg_dir / AUDIT_FILE).write_text(
        "\n".join(json.dumps(e) for e in audit_lines) + "\n", encoding="utf-8"
    )

    # Policies
    policies_yaml = """
policies:
  - name: deny-shell
    description: "block shell access"
    priority: 10
    action: deny
    scope: all
    type: tool_denylist
    tools: [shell_exec, system]

  - name: rate-limit
    description: "10 calls per minute"
    priority: 50
    action: log
    scope: all
    type: rate_limit
    max_calls: 10
    window_seconds: 60

  - name: allow-reads
    priority: 20
    action: allow
    type: tool_allowlist
    tools: [read_file]

  - name: pattern-block
    priority: 5
    action: deny
    type: regex
    field_name: tool_name
    pattern: "delete_.*"

  - name: always-flag
    priority: 99
    action: flag
    type: always
"""
    (ygg_dir / POLICIES_FILE).write_text(policies_yaml, encoding="utf-8")

    # Workflows
    (ygg_dir / WORKFLOWS_DIR).mkdir()
    (ygg_dir / WORKFLOWS_DIR / "bug-fix.yaml").write_text(
        """
name: bug-fix
description: bug investigation
version: "1.0"
steps:
  - name: reproduce
    intent: debug
    description: reproduce the bug
    tools: [terminal, read_file]
    gate:
      type: content_check
      min_length: 50
  - name: fix
    intent: code
    description: implement the fix
    tools: [write_file, patch]
""",
        encoding="utf-8",
    )

    return ygg_dir


# ═════════════════════════════════════════════════════════════════════════════
# Helper tests
# ═════════════════════════════════════════════════════════════════════════════


class TestParseTs:
    def test_none(self):
        assert _parse_ts(None) is None

    def test_empty_string(self):
        assert _parse_ts("") is None

    def test_iso_string(self):
        dt = _parse_ts("2024-01-01T10:00:00")
        assert dt == datetime(2024, 1, 1, 10, 0, 0)

    def test_iso_with_z(self):
        dt = _parse_ts("2024-01-01T10:00:00Z")
        assert dt is not None
        assert dt.year == 2024

    def test_iso_with_offset(self):
        dt = _parse_ts("2024-01-01T10:00:00+00:00")
        assert dt is not None
        assert dt.year == 2024

    def test_epoch_seconds(self):
        dt = _parse_ts(1_700_000_000)
        assert dt == datetime.fromtimestamp(1_700_000_000)

    def test_epoch_millis(self):
        dt = _parse_ts(1_700_000_000_000)
        assert dt == datetime.fromtimestamp(1_700_000_000)

    def test_garbage_returns_none(self):
        assert _parse_ts("not a date at all") is None


class TestNowIso:
    def test_returns_string(self):
        s = _now_iso()
        assert isinstance(s, str)
        # Round-trip parse should succeed
        _parse_ts(s)


class TestMiniYaml:
    def test_simple_key_value(self):
        data = _mini_yaml_load("name: foo\nage: 42\n")
        assert data["name"] == "foo"
        assert data["age"] == 42

    def test_nested_mapping(self):
        text = """
policies:
  - name: a
    priority: 1
  - name: b
    priority: 2
"""
        data = _mini_yaml_load(text)
        assert "policies" in data
        assert isinstance(data["policies"], list)
        assert data["policies"][0]["name"] == "a"
        assert data["policies"][0]["priority"] == 1

    def test_list_with_inline_dict(self):
        text = """
items:
  - foo
  - bar
  - baz
"""
        data = _mini_yaml_load(text)
        assert data["items"] == ["foo", "bar", "baz"]

    def test_comments_and_blank_lines(self):
        text = """
# comment
name: foo

# another comment
value: 1
"""
        data = _mini_yaml_load(text)
        assert data["name"] == "foo"
        assert data["value"] == 1

    def test_quoted_strings(self):
        data = _mini_yaml_load('name: "hello world"\n')
        assert data["name"] == "hello world"

    def test_booleans(self):
        data = _mini_yaml_load("yes: true\nno: false\n")
        assert data["yes"] is True
        assert data["no"] is False

    def test_empty(self):
        assert _mini_yaml_load("") == {}

    def test_tool_list(self):
        text = "tools: [shell_exec, read_file]\n"
        data = _mini_yaml_load(text)
        assert data["tools"] == ["shell_exec", "read_file"]


class TestYamlLoadDispatch:
    def test_uses_yaml_lib(self):
        # With PyYAML installed, _yaml_load uses it.
        data = _yaml_load("foo: bar\nbaz: 42\n")
        assert data == {"foo": "bar", "baz": 42}


# ═════════════════════════════════════════════════════════════════════════════
# Goal / GoalTurn / GoalGate
# ═════════════════════════════════════════════════════════════════════════════


class TestGoalTurn:
    def test_defaults(self):
        t = GoalTurn(agent="odin", action="test")
        assert t.evidence == ""
        assert t.timestamp  # auto

    def test_to_dict(self):
        t = GoalTurn(agent="mimir", action="analyze", evidence="scanned")
        d = t.to_dict()
        assert d["agent"] == "mimir"
        assert d["evidence"] == "scanned"


class TestGoalGate:
    def test_resolve_approved(self):
        g = GoalGate(id="g1", description="x")
        g.resolve("approved", by="skadi", note="looks good")
        assert g.status == "approved"
        assert g.resolved_by == "skadi"
        assert g.resolution_note == "looks good"
        assert g.resolved_at  # was set

    def test_resolve_invalid_status_raises(self):
        g = GoalGate(id="g1", description="x")
        with pytest.raises(ValueError):
            g.resolve("bogus")

    def test_to_dict(self):
        g = GoalGate(id="g1", description="x", status="approved")
        d = g.to_dict()
        assert d["id"] == "g1"
        assert d["status"] == "approved"


class TestGoal:
    def _mk(self) -> Goal:
        return Goal(id="g1", name="test", quota_max_calls=3, quota_max_tokens=100)

    def test_defaults(self):
        g = self._mk()
        assert g.status == "active"
        assert g.turns == []
        assert g.gates == []
        assert g.todos == []

    def test_add_turn(self):
        g = self._mk()
        t = g.add_turn("skadi", "analyze", "scanned", tokens=10)
        assert t.agent == "skadi"
        assert t.tokens_used == 10
        assert g.quota_used_calls == 1
        assert g.quota_used_tokens == 10
        assert g.updated_at > 0

    def test_add_gate(self):
        g = self._mk()
        gate = g.add_gate("review?")
        assert gate.id
        assert gate.status == "pending"
        assert g.pending_gates() == [gate]

    def test_pending_gates_filters(self):
        g = self._mk()
        g1 = g.add_gate("a")
        g.add_gate("b")
        g1.resolve("approved", by="skadi")
        pending = g.pending_gates()
        assert g1 not in pending
        assert len(pending) == 1

    def test_add_todo(self):
        g = self._mk()
        t = g.add_todo("write tests")
        assert t["text"] == "write tests"
        assert t["done"] is False

    def test_complete_todo(self):
        g = self._mk()
        t = g.add_todo("write tests")
        assert g.complete_todo(t["id"]) is True
        assert g.todos[0]["done"] is True

    def test_complete_todo_missing_returns_false(self):
        g = self._mk()
        assert g.complete_todo("nope") is False

    def test_completion_pct_with_todos(self):
        g = self._mk()
        g.add_todo("a")
        t2 = g.add_todo("b")
        assert g.completion_pct == 0.0
        g.complete_todo(t2["id"])
        assert g.completion_pct == 0.5

    def test_completion_pct_with_gates(self):
        g = self._mk()
        gate = g.add_gate("review?")
        g.add_todo("write code")
        assert g.completion_pct == 0.0
        gate.resolve("approved", by="skadi")
        assert g.completion_pct == 0.5

    def test_completion_pct_empty(self):
        g = self._mk()
        # No todos, no gates, quota_max=3, used=0 → 0.0
        assert g.completion_pct == 0.0

    def test_quota_remaining(self):
        g = self._mk()
        g.add_turn("skadi", "x", tokens=20)
        rem = g.quota_remaining()
        assert rem["calls"] == 2
        assert rem["tokens"] == 80

    def test_quota_remaining_unlimited_tokens(self):
        g = Goal(id="g", name="x", quota_max_tokens=0)
        rem = g.quota_remaining()
        assert rem["tokens"] == -1

    def test_round_trip(self):
        g = self._mk()
        g.add_turn("skadi", "analyze", evidence="x", tokens=5)
        g.add_gate("review?")
        g.add_todo("write tests")
        d = g.to_dict()
        g2 = Goal.from_dict(d)
        assert g2.id == g.id
        assert g2.name == g.name
        assert len(g2.turns) == 1
        assert len(g2.gates) == 1
        assert g2.quota_used_calls == 1

    def test_from_dict_handles_garbage(self):
        g = Goal.from_dict({})
        assert g.id and g.name

        g = Goal.from_dict("not a dict")  # type: ignore[arg-type]
        assert g.id == "unknown"


# ═════════════════════════════════════════════════════════════════════════════
# GoalsStore
# ═════════════════════════════════════════════════════════════════════════════


class TestGoalsStore:
    def test_list_empty(self, ygg_dir: Path):
        store = GoalsStore(ygg_dir)
        assert store.list() == []
        assert store.active() == []

    def test_create_and_list(self, ygg_dir: Path):
        store = GoalsStore(ygg_dir)
        g = store.create("smoke", project="Asgard", quota_max_calls=5)
        assert g.id
        assert (ygg_dir / GOALS_DIR / f"{g.id}.json").exists()
        listed = store.list()
        assert len(listed) == 1
        assert listed[0].name == "smoke"

    def test_get_existing(self, ygg_dir: Path):
        store = GoalsStore(ygg_dir)
        g = store.create("smoke")
        fetched = store.get(g.id)
        assert fetched is not None
        assert fetched.id == g.id

    def test_get_missing_returns_none(self, ygg_dir: Path):
        store = GoalsStore(ygg_dir)
        assert store.get("nope") is None

    def test_save_updates_file(self, ygg_dir: Path):
        store = GoalsStore(ygg_dir)
        g = store.create("smoke")
        g.add_turn("skadi", "analyze", "x")
        store.save(g)
        reloaded = store.get(g.id)
        assert reloaded is not None
        assert len(reloaded.turns) == 1

    def test_delete(self, ygg_dir: Path):
        store = GoalsStore(ygg_dir)
        g = store.create("smoke")
        assert store.delete(g.id) is True
        assert store.get(g.id) is None
        assert store.delete(g.id) is False  # already gone

    def test_active_filters(self, ygg_dir: Path):
        store = GoalsStore(ygg_dir)
        g1 = store.create("a")
        g2 = store.create("b")
        g2.status = "done"
        store.save(g2)
        active = store.active()
        assert g1 in active
        assert g2 not in active

    def test_create_with_explicit_id(self, ygg_dir: Path):
        store = GoalsStore(ygg_dir)
        g = store.create("x", goal_id="myid")
        assert g.id == "myid"
        assert store.get("myid") is not None

    def test_exists_property(self, ygg_dir: Path):
        store = GoalsStore(ygg_dir)
        assert store.exists is False
        store._ensure()
        assert store.exists is True


# ═════════════════════════════════════════════════════════════════════════════
# HandoffPack / HandoffsStore
# ═════════════════════════════════════════════════════════════════════════════


class TestHandoffPack:
    def test_from_goal(self):
        g = Goal(id="g1", name="t", project="X")
        g.add_turn("skadi", "analyze", "x", tokens=0)
        g.add_gate("review?")
        pack = HandoffPack.from_goal(g)
        assert pack.goal_id == "g1"
        assert pack.last_agent == "skadi"
        assert pack.last_action == "analyze"
        assert pack.total_turns == 1
        assert len(pack.pending_gates) == 1
        assert pack.quota_remaining["calls"] >= 0

    def test_from_goal_empty(self):
        g = Goal(id="g1", name="t")
        pack = HandoffPack.from_goal(g)
        assert pack.last_agent == ""
        assert pack.total_turns == 0

    def test_from_dict_round_trip(self):
        g = Goal(id="g1", name="t", project="X")
        g.add_turn("skadi", "analyze", "x")
        pack = HandoffPack.from_goal(g)
        d = pack.to_dict()
        pack2 = HandoffPack.from_dict(d)
        assert pack2.goal_id == pack.goal_id
        assert pack2.last_agent == "skadi"

    def test_from_dict_handles_garbage(self):
        pack = HandoffPack.from_dict({})
        assert pack.goal_id and pack.name
        pack = HandoffPack.from_dict("not a dict")  # type: ignore[arg-type]
        assert pack.goal_id == "unknown"


class TestHandoffsStore:
    def test_list_empty(self, ygg_dir: Path):
        store = HandoffsStore(ygg_dir)
        assert store.list() == []

    def test_write_for(self, ygg_dir: Path):
        gs = GoalsStore(ygg_dir)
        hs = HandoffsStore(ygg_dir)
        g = gs.create("smoke", project="Asgard")
        g.add_turn("skadi", "analyze", "x")
        pack = hs.write_for(g)
        assert pack.goal_id == g.id
        assert (ygg_dir / HANDOFFS_DIR / f"{g.id}.json").exists()
        listed = hs.list()
        assert len(listed) == 1
        assert listed[0].goal_id == g.id

    def test_get_existing(self, ygg_dir: Path):
        gs = GoalsStore(ygg_dir)
        hs = HandoffsStore(ygg_dir)
        g = gs.create("smoke")
        hs.write_for(g)
        assert hs.get(g.id) is not None

    def test_get_missing_returns_none(self, ygg_dir: Path):
        hs = HandoffsStore(ygg_dir)
        assert hs.get("nope") is None

    def test_delete(self, ygg_dir: Path):
        hs = HandoffsStore(ygg_dir)
        assert hs.delete("nope") is False
        # Need a real one
        gs = GoalsStore(ygg_dir)
        g = gs.create("smoke")
        hs.write_for(g)
        assert hs.delete(g.id) is True


# ═════════════════════════════════════════════════════════════════════════════
# AuditEvent / AuditLog
# ═════════════════════════════════════════════════════════════════════════════


class TestAuditEvent:
    def test_timestamp(self):
        ev = AuditEvent(ts="2024-01-01T10:00:00", policy="x", agent="y")
        assert ev.timestamp == datetime(2024, 1, 1, 10, 0, 0)

    def test_timestamp_garbage(self):
        ev = AuditEvent(ts="garbage", policy="x", agent="y")
        assert ev.timestamp is None

    def test_round_trip(self):
        ev = AuditEvent(
            ts="2024-01-01T10:00:00",
            policy="p",
            agent="a",
            session="s",
            tool="t",
            hook_type="pre",
            action="deny",
            note="n",
            data={"k": "v"},
        )
        d = ev.to_dict()
        ev2 = AuditEvent.from_dict(d)
        assert ev2.policy == "p"
        assert ev2.data == {"k": "v"}

    def test_from_dict_handles_garbage(self):
        ev = AuditEvent.from_dict({})
        assert ev.policy == ""
        ev = AuditEvent.from_dict("nope")  # type: ignore[arg-type]
        assert ev.policy == ""


class TestAuditLog:
    def test_empty(self, ygg_dir: Path):
        log = AuditLog(ygg_dir)
        assert log.exists is False
        assert log.count() == 0
        assert log.all() == []

    def test_append(self, ygg_dir: Path):
        log = AuditLog(ygg_dir)
        ev = log.append(
            policy="deny-shell",
            agent="odin",
            hook_type="pre_tool_call",
            action="deny",
            tool="shell_exec",
            data={"tool_name": "shell_exec"},
        )
        assert ev.policy == "deny-shell"
        assert log.count() == 1
        assert log.path.exists()

    def test_iter_all(self, populated_ygg: Path):
        log = AuditLog(populated_ygg)
        events = list(log.iter_all())
        assert len(events) == 3

    def test_filter_by_agent(self, populated_ygg: Path):
        log = AuditLog(populated_ygg)
        odin_events = log.filter(agent="Odin")
        assert len(odin_events) == 2
        assert all(e.agent == "Odin" for e in odin_events)

    def test_filter_by_policy(self, populated_ygg: Path):
        log = AuditLog(populated_ygg)
        evs = log.filter(policy="deny-dangerous-shell")
        assert len(evs) == 1
        assert evs[0].action == "deny"

    def test_filter_by_action(self, populated_ygg: Path):
        log = AuditLog(populated_ygg)
        evs = log.filter(action="allow")
        assert len(evs) == 1
        assert evs[0].agent == "Mimir"

    def test_filter_by_hook_type(self, populated_ygg: Path):
        log = AuditLog(populated_ygg)
        evs = log.filter(hook_type="pre_tool_call")
        assert len(evs) == 2

    def test_filter_by_time(self, populated_ygg: Path):
        log = AuditLog(populated_ygg)
        evs = log.filter(since="2024-01-02")
        assert len(evs) == 2
        evs = log.filter(until="2024-01-02T23:59:59")
        assert len(evs) == 2

    def test_filter_with_limit(self, populated_ygg: Path):
        log = AuditLog(populated_ygg)
        evs = log.filter(limit=2)
        assert len(evs) == 2

    def test_clear(self, populated_ygg: Path):
        log = AuditLog(populated_ygg)
        assert log.count() == 3
        log.clear()
        assert log.count() == 0
        # File should still exist (touch'd back)
        assert log.path.exists()

    def test_append_after_clear_continues(self, populated_ygg: Path):
        log = AuditLog(populated_ygg)
        log.clear()
        log.append(policy="x", agent="y", action="log")
        assert log.count() == 1


# ═════════════════════════════════════════════════════════════════════════════
# PolicyRule / PolicySet / PoliciesStore
# ═════════════════════════════════════════════════════════════════════════════


class TestPolicyRule:
    def test_always_matches(self):
        r = PolicyRule(name="r", type="always", action="log")
        assert r.matches({}) is True

    def test_disabled_never_matches(self):
        r = PolicyRule(name="r", type="always", action="log", enabled=False)
        assert r.matches({}) is False

    def test_tool_denylist(self):
        r = PolicyRule(
            name="deny-shell", type="tool_denylist", action="deny", raw={"tools": ["shell_exec"]}
        )
        assert r.matches({"tool_name": "shell_exec"}) is True
        assert r.matches({"tool_name": "read_file"}) is False

    def test_tool_allowlist(self):
        r = PolicyRule(
            name="allow-reads", type="tool_allowlist", action="allow", raw={"tools": ["read_file"]}
        )
        assert r.matches({"tool_name": "read_file"}) is True
        assert r.matches({"tool_name": "shell_exec"}) is False

    def test_rate_limit(self):
        r = PolicyRule(
            name="rl",
            type="rate_limit",
            action="log",
            raw={"max_calls": 10, "window_seconds": 60},
        )
        assert r.matches({"calls_in_window": 5}) is False
        assert r.matches({"calls_in_window": 10}) is True
        assert r.matches({"calls_in_window": 100}) is True
        # No window → never matches
        r2 = PolicyRule(name="rl2", type="rate_limit", raw={"max_calls": 10, "window_seconds": 0})
        assert r2.matches({"calls_in_window": 100}) is False

    def test_token_budget(self):
        r = PolicyRule(
            name="tb", type="token_budget", action="log", raw={"max_tokens": 1000}
        )
        assert r.matches({"tokens_used": 500}) is False
        assert r.matches({"tokens_used": 1500}) is True

    def test_regex(self):
        r = PolicyRule(
            name="rgx",
            type="regex",
            action="deny",
            raw={"field_name": "tool_name", "pattern": "delete_.*"},
        )
        assert r.matches({"tool_name": "delete_user"}) is True
        assert r.matches({"tool_name": "read_file"}) is False

    def test_regex_invalid_pattern(self):
        r = PolicyRule(
            name="rgx",
            type="regex",
            action="deny",
            raw={"field_name": "tool_name", "pattern": "[unclosed"},
        )
        # Bad regex → no match
        assert r.matches({"tool_name": "x"}) is False

    def test_invalid_action_disables(self):
        r = PolicyRule(name="r", type="always", action="bogus")
        assert r.enabled is False
        assert r.matches({}) is False

    def test_invalid_type_disables(self):
        r = PolicyRule(name="r", type="bogus", action="log")
        assert r.enabled is False

    def test_to_dict_preserves_raw(self):
        raw = {"tools": ["x"], "extra": 1}
        r = PolicyRule(
            name="r", type="tool_denylist", action="deny", priority=5, raw=raw
        )
        d = r.to_dict()
        assert d["name"] == "r"
        assert d["priority"] == 5
        assert d["tools"] == ["x"]
        assert d["extra"] == 1


class TestPolicySet:
    def test_matching_sorted_by_priority(self):
        ps = PolicySet(rules=[
            PolicyRule(name="low", type="always", action="log", priority=100),
            PolicyRule(name="high", type="always", action="deny", priority=1),
        ])
        matches = ps.matching({})
        assert matches[0].name == "high"

    def test_by_action(self):
        ps = PolicySet(rules=[
            PolicyRule(name="a", type="always", action="allow"),
            PolicyRule(name="b", type="always", action="deny"),
        ])
        denies = ps.by_action("deny")
        assert len(denies) == 1
        assert denies[0].name == "b"

    def test_by_name(self):
        ps = PolicySet(rules=[PolicyRule(name="a", type="always", action="log")])
        assert ps.by_name("a") is not None
        assert ps.by_name("z") is None

    def test_enabled(self):
        ps = PolicySet(rules=[
            PolicyRule(name="a", type="always", action="log"),
            PolicyRule(name="b", type="always", action="log", enabled=False),
        ])
        assert len(ps.enabled()) == 1


class TestPoliciesStore:
    def test_load_missing(self, ygg_dir: Path):
        store = PoliciesStore(ygg_dir)
        assert store.exists is False
        ps = store.load()
        assert ps.rules == []

    def test_load_real(self, populated_ygg: Path):
        store = PoliciesStore(populated_ygg)
        assert store.exists is True
        ps = store.load()
        assert len(ps.rules) == 5
        names = {r.name for r in ps.rules}
        assert "deny-shell" in names
        assert "rate-limit" in names
        assert "pattern-block" in names

    def test_evaluate_no_match(self, ygg_dir: Path):
        store = PoliciesStore(ygg_dir)
        action, rule = store.evaluate({"tool_name": "read_file"})
        # No policies loaded → default log
        assert action == "log"
        assert rule is None

    def test_evaluate_match_deny(self, populated_ygg: Path):
        store = PoliciesStore(populated_ygg)
        action, rule = store.evaluate({"tool_name": "shell_exec"})
        assert action == "deny"
        assert rule is not None
        # Highest-priority matching rule wins (deny-shell has priority 10)
        assert rule.name in ("deny-shell", "pattern-block")

    def test_evaluate_match_allow(self, populated_ygg: Path):
        store = PoliciesStore(populated_ygg)
        action, rule = store.evaluate({"tool_name": "read_file"})
        # allow-reads (priority 20) matches
        assert action == "allow"
        assert rule is not None
        assert rule.name == "allow-reads"

    def test_evaluate_regex_match(self, populated_ygg: Path):
        store = PoliciesStore(populated_ygg)
        action, rule = store.evaluate({"tool_name": "delete_user"})
        # pattern-block has priority 5 — highest
        assert action == "deny"
        assert rule is not None
        assert rule.name == "pattern-block"

    def test_evaluate_always_flag(self, populated_ygg: Path):
        store = PoliciesStore(populated_ygg)
        action, rule = store.evaluate({})
        # always-flag has priority 99, but deny-shell has priority 10
        # so deny-shell wins when shell_exec is in context, but no shell
        # in the default context → only always-flag matches
        # Actually, the bug: always-flag matches ANY context including empty
        # → it would win unless a higher-priority match exists.
        # pattern-block (priority 5) doesn't match. allow-reads (20) doesn't.
        # deny-shell (10) doesn't match (no tool). rate-limit (50) doesn't
        # match (no rate data). always-flag (99) matches.
        assert action == "flag"
        assert rule is not None
        assert rule.name == "always-flag"

    def test_evaluate_rate_limit_match(self, populated_ygg: Path):
        store = PoliciesStore(populated_ygg)
        action, rule = store.evaluate({"calls_in_window": 100})
        # rate-limit has priority 50 and matches
        # always-flag has priority 99 but rate-limit has higher priority
        # so rate-limit wins
        assert action == "log"
        assert rule is not None
        assert rule.name == "rate-limit"


# ═════════════════════════════════════════════════════════════════════════════
# Workflow / WorkflowStep / WorkflowsStore
# ═════════════════════════════════════════════════════════════════════════════


class TestWorkflowStep:
    def test_from_dict(self):
        s = WorkflowStep.from_dict({
            "name": "x",
            "intent": "code",
            "tools": ["write_file", "patch"],
            "gate": {"type": "content_check"},
        })
        assert s.name == "x"
        assert s.intent == "code"
        assert s.tools == ["write_file", "patch"]

    def test_from_dict_handles_string_tools(self):
        s = WorkflowStep.from_dict({"name": "x", "tools": "a, b, c"})
        assert s.tools == ["a", "b", "c"]

    def test_from_dict_handles_garbage(self):
        s = WorkflowStep.from_dict({})
        assert s.name == "unnamed"
        s = WorkflowStep.from_dict("nope")  # type: ignore[arg-type]
        assert s.name == "unnamed"

    def test_to_dict(self):
        s = WorkflowStep(name="x", tools=["a"])
        d = s.to_dict()
        assert d["name"] == "x"
        assert d["tools"] == ["a"]


class TestWorkflow:
    def test_from_dict(self):
        wf = Workflow.from_dict({
            "name": "bug-fix",
            "description": "x",
            "version": "2.0",
            "steps": [{"name": "a"}, {"name": "b"}],
        })
        assert wf.name == "bug-fix"
        assert wf.version == "2.0"
        assert len(wf.steps) == 2

    def test_from_dict_handles_garbage(self):
        wf = Workflow.from_dict({})
        assert wf.name == "unnamed"
        wf = Workflow.from_dict("nope")  # type: ignore[arg-type]
        assert wf.name == "unnamed"

    def test_to_dict(self):
        wf = Workflow(name="x", steps=[WorkflowStep(name="a")])
        d = wf.to_dict()
        assert d["name"] == "x"
        assert len(d["steps"]) == 1


class TestWorkflowsStore:
    def test_list_empty(self, ygg_dir: Path):
        store = WorkflowsStore(ygg_dir)
        assert store.list() == []
        assert store.names() == []

    def test_get_missing(self, ygg_dir: Path):
        store = WorkflowsStore(ygg_dir)
        assert store.get("nope") is None

    def test_load_real(self, populated_ygg: Path):
        store = WorkflowsStore(populated_ygg)
        wf = store.get("bug-fix")
        assert wf is not None
        assert wf.name == "bug-fix"
        assert len(wf.steps) == 2
        assert wf.steps[0].name == "reproduce"
        assert wf.steps[0].gate["min_length"] == 50

    def test_list_real(self, populated_ygg: Path):
        store = WorkflowsStore(populated_ygg)
        names = store.names()
        assert names == ["bug-fix"]


# ═════════════════════════════════════════════════════════════════════════════
# CrossContext facade
# ═════════════════════════════════════════════════════════════════════════════


class TestCrossContext:
    def test_init_substores(self, ygg_dir: Path):
        cx = CrossContext(ygg_dir)
        assert cx.goals.ygg_dir == ygg_dir
        assert cx.audit.ygg_dir == ygg_dir
        assert cx.policies.ygg_dir == ygg_dir
        assert cx.workflows.ygg_dir == ygg_dir

    def test_exists(self, ygg_dir: Path):
        cx = CrossContext(ygg_dir)
        assert cx.exists is True

    def test_snapshot_empty(self, ygg_dir: Path):
        cx = CrossContext(ygg_dir)
        snap = cx.snapshot()
        assert snap["goals"]["count"] == 0
        assert snap["handoffs"]["count"] == 0
        assert snap["audit"]["count"] == 0
        assert snap["policies"]["rule_count"] == 0
        assert snap["workflows"]["count"] == 0

    def test_snapshot_populated(self, populated_ygg: Path):
        cx = CrossContext(populated_ygg)
        snap = cx.snapshot()
        assert snap["goals"]["count"] == 1
        assert snap["handoffs"]["count"] == 1
        assert snap["audit"]["count"] == 3
        assert snap["policies"]["rule_count"] == 5
        assert snap["workflows"]["count"] == 1

    def test_render_summary_populated(self, populated_ygg: Path):
        cx = CrossContext(populated_ygg)
        summary = cx.render_summary()
        assert "Goals:" in summary
        assert "1 total" in summary
        assert "Handoffs:" in summary
        assert "Audit:" in summary
        assert "3 events" in summary
        assert "Policies:" in summary
        assert "5 rules" in summary
        assert "Workflows:" in summary
        assert "bug-fix" in summary

    def test_render_summary_empty(self, ygg_dir: Path):
        cx = CrossContext(ygg_dir)
        summary = cx.render_summary()
        assert "Goals:    0" in summary
        assert "Workflows: <none>" in summary

    def test_real_data_smoke(self):
        """Smoke-test against the actual .ygg/ directory in the repo."""
        repo = Path(os.environ.get("YGGDRASIL_ROOT", str(Path.home() / "Yggdrasil"))) / ".ygg"
        if not repo.exists():
            pytest.skip("repo .ygg/ not present")
        cx = CrossContext(repo)
        snap = cx.snapshot()
        # We don't assert exact counts (the cron adds to it), but it should
        # be parseable.
        assert snap["goals"]["count"] >= 0
        assert snap["handoffs"]["count"] >= 0
        assert snap["audit"]["count"] >= 0
        # Policies file is committed, so we expect at least some rules
        assert snap["policies"]["exists"] is True
        assert snap["policies"]["rule_count"] > 0


# ═════════════════════════════════════════════════════════════════════════════
# Module-level exports
# ═════════════════════════════════════════════════════════════════════════════


class TestExports:
    def test_module_all(self):
        from lilith_skills import cross_context
        for name in [
            "Goal", "GoalTurn", "GoalGate", "GoalsStore",
            "HandoffPack", "HandoffsStore",
            "AuditEvent", "AuditLog",
            "PolicyRule", "PolicySet", "PoliciesStore",
            "Workflow", "WorkflowStep", "WorkflowsStore",
            "CrossContext",
        ]:
            assert name in cross_context.__all__, name

    def test_valid_rule_actions(self):
        assert "allow" in VALID_RULE_ACTIONS
        assert "deny" in VALID_RULE_ACTIONS
        assert "log" in VALID_RULE_ACTIONS
        assert "flag" in VALID_RULE_ACTIONS

    def test_valid_rule_types(self):
        assert "always" in VALID_RULE_TYPES
        assert "regex" in VALID_RULE_TYPES
        assert "rate_limit" in VALID_RULE_TYPES
