"""Tests for PolicyAuditTrail and ResourceLimitRule (lilith-core v2.8.0)."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

from lilith_core.audit_trail import (
    AuditEntry,
    PolicyAuditTrail,
    make_default_trail,
    summarize_entries,
)
from lilith_core.hooks import HookContext, HookType, get_hook_registry
from lilith_core.policy_engine import (
    Policy,
    PolicyAction,
    PolicyEngine,
    PolicyScope,
    ResourceLimitRule,
)


# ── AuditEntry basics ──────────────────────────────────────────────────────


class TestAuditEntry:
    """AuditEntry dataclass: serialization, factories, defaults."""

    def test_defaults(self) -> None:
        entry = AuditEntry(policy="p", agent="a", session="s")
        assert entry.policy == "p"
        assert entry.agent == "a"
        assert entry.session == "s"
        assert entry.tool == ""
        assert entry.message == ""
        assert entry.hook_type == ""
        assert entry.action == ""
        assert entry.note == ""
        assert entry.data == {}
        # Timestamp is ISO and recent
        assert "T" in entry.ts  # ISO 8601 has T separator
        assert entry.ts.endswith("+00:00") or entry.ts.endswith("Z")

    def test_to_json_is_single_line(self) -> None:
        entry = AuditEntry(
            policy="p",
            agent="a",
            session="s",
            message="line1\nline2\nline3",
            note="with\nnewlines",
        )
        line = entry.to_json()
        assert "\n" not in line, "audit lines must not contain raw newlines"
        payload = json.loads(line)
        assert payload["message"] == "line1\nline2\nline3"

    def test_to_json_round_trip(self) -> None:
        original = AuditEntry(
            policy="deny-shell",
            agent="Odin",
            session="abc-123",
            tool="terminal",
            hook_type="pre_tool_call",
            action="deny",
            note="unit test",
            data={"params": {"command": "rm -rf /"}},
        )
        payload = json.loads(original.to_json())
        assert payload["policy"] == "deny-shell"
        assert payload["agent"] == "Odin"
        assert payload["session"] == "abc-123"
        assert payload["data"] == {"params": {"command": "rm -rf /"}}

    def test_from_hook_factory(self) -> None:
        ctx = HookContext(
            hook_type=HookType.PRE_TOOL_CALL,
            agent_name="Odin",
            session_id="sess-1",
            data={"tool_name": "terminal", "params": {"command": "ls"}},
        )
        from lilith_core.policy_engine import PolicyResult

        result = PolicyResult(
            action=PolicyAction.DENY,
            matched_policies=["deny-shell"],
            message="denied",
        )
        entry = AuditEntry.from_hook(ctx, result, "deny-shell")
        assert entry.policy == "deny-shell"
        assert entry.agent == "Odin"
        assert entry.session == "sess-1"
        assert entry.tool == "terminal"
        assert entry.hook_type == "pre_tool_call"
        assert entry.action == "deny"
        assert entry.data["params"]["command"] == "ls"

    def test_from_hook_truncates_long_messages(self) -> None:
        ctx = HookContext(
            hook_type=HookType.PRE_LLM_CALL,
            agent_name="Odin",
            session_id="s",
            data={"message": "x" * 5000},
        )
        from lilith_core.policy_engine import PolicyResult

        result = PolicyResult(action=PolicyAction.ALLOW, matched_policies=[])
        entry = AuditEntry.from_hook(ctx, result, "allow")
        assert len(entry.message) <= 512


# ── PolicyAuditTrail: file operations ────────────────────────────────────────


class TestPolicyAuditTrailWrite:
    """Trails create dirs, append JSONL safely, and rotate correctly."""

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        target = tmp_path / "deep" / "nested" / "audit.jsonl"
        trail = PolicyAuditTrail(path=target)
        trail.record(AuditEntry(policy="p", agent="a", session="s"))
        assert target.exists()
        assert target.parent.is_dir()

    def test_appends_jsonl(self, tmp_path: Path) -> None:
        path = tmp_path / "audit.jsonl"
        trail = PolicyAuditTrail(path=path)
        for i in range(5):
            trail.record(
                AuditEntry(policy=f"p{i}", agent="Odin", session="s", action="allow")
            )
        # Verify on disk
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 5
        for i, line in enumerate(lines):
            payload = json.loads(line)
            assert payload["policy"] == f"p{i}"

    def test_thread_safe_writes(self, tmp_path: Path) -> None:
        path = tmp_path / "audit.jsonl"
        trail = PolicyAuditTrail(path=path)

        def worker(wid: int) -> None:
            for i in range(20):
                trail.record(
                    AuditEntry(
                        policy=f"w{wid}-p{i}",
                        agent=f"agent{wid}",
                        session="s",
                    )
                )

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        lines = path.read_text(encoding="utf-8").strip().splitlines()
        # 5 workers × 20 entries = 100 lines, no torn writes
        assert len(lines) == 100
        for line in lines:
            payload = json.loads(line)  # Raises on malformed → proves JSON-valid
            assert "policy" in payload

    def test_on_record_callback(self, tmp_path: Path) -> None:
        path = tmp_path / "audit.jsonl"
        received: list[AuditEntry] = []

        def collector(entry: AuditEntry) -> None:
            received.append(entry)

        trail = PolicyAuditTrail(path=path, on_record=collector)
        trail.record(AuditEntry(policy="p1", agent="a", session="s"))
        trail.record(AuditEntry(policy="p2", agent="a", session="s"))
        assert len(received) == 2
        assert received[0].policy == "p1"
        assert received[1].policy == "p2"

    def test_rotation_on_max_entries(self, tmp_path: Path) -> None:
        path = tmp_path / "audit.jsonl"
        trail = PolicyAuditTrail(path=path, max_entries=5)
        for i in range(12):
            trail.record(AuditEntry(policy=f"p{i}", agent="a", session="s"))
        # After rotation, the active file is small again, archives exist
        assert path.exists()
        archives = list(path.parent.glob("*.rotated"))
        assert len(archives) >= 1
        # The active file holds only post-rotation entries
        content = path.read_text(encoding="utf-8") if path.exists() else ""
        post = len([line for line in content.splitlines() if line.strip()])
        assert post < 12, f"Active file should be rotated (got {post} lines)"

    def test_rotation_on_max_bytes(self, tmp_path: Path) -> None:
        path = tmp_path / "audit.jsonl"
        trail = PolicyAuditTrail(path=path, max_entries=0, max_bytes=200)
        # Each entry ≈ 100 bytes json line; write enough to force rotation
        for i in range(10):
            trail.record(
                AuditEntry(
                    policy=f"p{i}",
                    agent="agent",
                    session="s",
                    message="x" * 80,
                )
            )
        archives = list(path.parent.glob("*.rotated"))
        assert len(archives) >= 1

    def test_explicit_rotate_returns_size(self, tmp_path: Path) -> None:
        path = tmp_path / "audit.jsonl"
        trail = PolicyAuditTrail(path=path, max_entries=0)
        trail.record(AuditEntry(policy="p", agent="a", session="s"))
        size = trail.rotate()
        assert size > 0
        # After rotate, file may be missing (just renamed)
        archives = list(path.parent.glob("*.rotated"))
        assert len(archives) == 1

    def test_clear(self, tmp_path: Path) -> None:
        path = tmp_path / "audit.jsonl"
        trail = PolicyAuditTrail(path=path)
        trail.record(AuditEntry(policy="p1", agent="a", session="s"))
        trail.record(AuditEntry(policy="p2", agent="a", session="s"))
        cleared = trail.clear()
        assert cleared == 2
        assert trail.tail(10) == []
        assert not path.exists()


# ── PolicyAuditTrail: queries ────────────────────────────────────────────────


class TestPolicyAuditTrailQuery:
    """In-memory buffer queries + stats aggregation."""

    def test_tail_returns_recent(self, tmp_path: Path) -> None:
        path = tmp_path / "audit.jsonl"
        trail = PolicyAuditTrail(path=path)
        for i in range(10):
            trail.record(AuditEntry(policy=f"p{i}", agent="a", session="s"))
        recent = trail.tail(3)
        assert len(recent) == 3
        assert [e.policy for e in recent] == ["p7", "p8", "p9"]

    def test_filter_by_action(self, tmp_path: Path) -> None:
        path = tmp_path / "audit.jsonl"
        trail = PolicyAuditTrail(path=path)
        for action, n in [("allow", 5), ("deny", 3), ("flag", 2)]:
            for _ in range(n):
                trail.record(
                    AuditEntry(policy="p", agent="a", session="s", action=action)
                )
        denied = trail.filter(action="deny")
        assert len(denied) == 3
        assert all(e.action == "deny" for e in denied)

    def test_filter_by_agent_and_policy(self, tmp_path: Path) -> None:
        path = tmp_path / "audit.jsonl"
        trail = PolicyAuditTrail(path=path)
        trail.record(AuditEntry(policy="p1", agent="Odin", session="s"))
        trail.record(AuditEntry(policy="p1", agent="Mimir", session="s"))
        trail.record(AuditEntry(policy="p2", agent="Odin", session="s"))
        odin_p1 = trail.filter(agent="Odin", policy="p1")
        assert len(odin_p1) == 1
        assert odin_p1[0].agent == "Odin"

    def test_stats_aggregate(self, tmp_path: Path) -> None:
        path = tmp_path / "audit.jsonl"
        trail = PolicyAuditTrail(path=path)
        trail.record(AuditEntry(policy="p1", agent="Odin", session="s", action="allow", tool="terminal"))
        trail.record(AuditEntry(policy="p1", agent="Odin", session="s", action="deny", tool="terminal"))
        trail.record(AuditEntry(policy="p2", agent="Mimir", session="s", action="flag", tool="read_file"))
        stats = trail.stats()
        assert stats["buffered"] == 3
        assert stats["by_action"]["allow"] == 1
        assert stats["by_action"]["deny"] == 1
        assert stats["by_action"]["flag"] == 1
        assert stats["by_agent_top10"]["Odin"] == 2
        assert stats["by_policy_top10"]["p1"] == 2
        assert stats["by_tool_top10"]["terminal"] == 2

    def test_iter_file_yields_all_entries(self, tmp_path: Path) -> None:
        path = tmp_path / "audit.jsonl"
        trail = PolicyAuditTrail(path=path)
        for i in range(5):
            trail.record(AuditEntry(policy=f"p{i}", agent="a", session="s"))
        entries = list(trail.iter_file())
        assert len(entries) == 5
        assert [e.policy for e in entries] == ["p0", "p1", "p2", "p3", "p4"]


# ── PolicyAuditTrail: hook/PolicyEngine integration ─────────────────────────


class TestPolicyAuditTrailAttach:
    """The trail wires into PolicyEngine.evaluate automatically."""

    def test_attach_writes_entries(self, tmp_path: Path) -> None:
        path = tmp_path / "audit.jsonl"
        trail = PolicyAuditTrail(path=path)
        engine = PolicyEngine()
        engine.add_policy(
            Policy(
                name="block-shell",
                scope=PolicyScope(tool="terminal"),
                action=PolicyAction.DENY,
                priority=10,
            )
        )
        # Use a ToolDenylistRule so we get a "match on shell" pattern
        from lilith_core.policy_engine import ToolDenylistRule

        engine.add_policy(
            Policy(
                name="deny-shell-rule",
                scope=PolicyScope(agent="Odin"),
                rule=ToolDenylistRule(tools=["terminal"]),
                action=PolicyAction.DENY,
                priority=1,
            )
        )

        trail.attach(engine)

        ctx = HookContext(
            hook_type=HookType.PRE_TOOL_CALL,
            agent_name="Odin",
            session_id="s",
            data={"tool_name": "terminal"},
        )
        engine.evaluate(ctx)

        entries = trail.tail(10)
        assert len(entries) >= 1
        # Find the deny record
        deny_entries = [e for e in entries if e.action == "deny"]
        assert len(deny_entries) >= 1
        assert deny_entries[0].agent == "Odin"
        assert deny_entries[0].tool == "terminal"

        # Cleanup
        trail.detach(engine)

    def test_attach_is_idempotent(self, tmp_path: Path) -> None:
        path = tmp_path / "audit.jsonl"
        trail = PolicyAuditTrail(path=path)
        engine = PolicyEngine()
        trail.attach(engine)
        trail.attach(engine)  # Second call should not double-wrap
        engine.add_policy(
            Policy(
                name="p",
                scope=PolicyScope(),
                action=PolicyAction.ALLOW,
                priority=10,
            )
        )
        ctx = HookContext(
            hook_type=HookType.PRE_LLM_CALL,
            agent_name="a",
            session_id="s",
            data={"message": "hi"},
        )
        engine.evaluate(ctx)
        entries = trail.tail(10)
        # Exactly one entry, not two
        assert len(entries) == 1


# ── ResourceLimitRule ───────────────────────────────────────────────────────


class TestResourceLimitRule:
    """Per-agent resource caps: payload size, concurrent calls, session duration."""

    def test_payload_size_cap_rejects_large(self) -> None:
        rule = ResourceLimitRule(max_payload_bytes=10)
        ctx = HookContext(
            hook_type=HookType.PRE_LLM_CALL,
            agent_name="Odin",
            session_id="s",
            data={"message": "x" * 100},
        )
        from lilith_core.policy_engine import PolicyState

        state = PolicyState()
        assert rule.evaluate(ctx, state) is True
        reason = state.get("resource_reason:Odin:s")
        assert reason is not None
        assert "payload_size=" in reason

    def test_payload_size_cap_allows_small(self) -> None:
        rule = ResourceLimitRule(max_payload_bytes=10_000)
        ctx = HookContext(
            hook_type=HookType.PRE_LLM_CALL,
            agent_name="Odin",
            session_id="s",
            data={"message": "hello"},
        )
        from lilith_core.policy_engine import PolicyState

        state = PolicyState()
        assert rule.evaluate(ctx, state) is False

    def test_payload_size_handles_binary(self) -> None:
        rule = ResourceLimitRule(max_payload_bytes=10)
        ctx = HookContext(
            hook_type=HookType.PRE_TOOL_CALL,
            agent_name="Odin",
            session_id="s",
            data={"file_bytes": b"x" * 100},
        )
        from lilith_core.policy_engine import PolicyState

        state = PolicyState()
        assert rule.evaluate(ctx, state) is True

    def test_concurrent_calls_rejects_overflow(self) -> None:
        rule = ResourceLimitRule(max_concurrent_calls=3)
        from lilith_core.policy_engine import PolicyState

        state = PolicyState()
        state.increment("inflight:Odin:s", 5)
        ctx = HookContext(
            hook_type=HookType.PRE_TOOL_CALL,
            agent_name="Odin",
            session_id="s",
            data={"tool_name": "t"},
        )
        assert rule.evaluate(ctx, state) is True

    def test_concurrent_calls_allows_within(self) -> None:
        rule = ResourceLimitRule(max_concurrent_calls=3)
        from lilith_core.policy_engine import PolicyState

        state = PolicyState()
        state.increment("inflight:Odin:s", 2)
        ctx = HookContext(
            hook_type=HookType.PRE_TOOL_CALL,
            agent_name="Odin",
            session_id="s",
            data={"tool_name": "t"},
        )
        assert rule.evaluate(ctx, state) is False

    def test_session_duration_rejects_expired(self) -> None:
        rule = ResourceLimitRule(max_session_duration_seconds=10)
        from lilith_core.policy_engine import PolicyState

        state = PolicyState()
        state.set("session_started:Odin:s", time.time() - 100)
        ctx = HookContext(
            hook_type=HookType.PRE_TOOL_CALL,
            agent_name="Odin",
            session_id="s",
            data={"tool_name": "t"},
        )
        assert rule.evaluate(ctx, state) is True

    def test_session_duration_allows_fresh(self) -> None:
        rule = ResourceLimitRule(max_session_duration_seconds=600)
        from lilith_core.policy_engine import PolicyState

        state = PolicyState()
        state.set("session_started:Odin:s", time.time() - 5)
        ctx = HookContext(
            hook_type=HookType.PRE_TOOL_CALL,
            agent_name="Odin",
            session_id="s",
            data={"tool_name": "t"},
        )
        assert rule.evaluate(ctx, state) is False

    def test_session_duration_no_started_no_match(self) -> None:
        rule = ResourceLimitRule(max_session_duration_seconds=600)
        from lilith_core.policy_engine import PolicyState

        state = PolicyState()
        ctx = HookContext(
            hook_type=HookType.PRE_TOOL_CALL,
            agent_name="Odin",
            session_id="s",
            data={"tool_name": "t"},
        )
        assert rule.evaluate(ctx, state) is False

    def test_all_caps_zero_disabled(self) -> None:
        rule = ResourceLimitRule()  # all defaults to 0
        from lilith_core.policy_engine import PolicyState

        state = PolicyState()
        ctx = HookContext(
            hook_type=HookType.PRE_LLM_CALL,
            agent_name="Odin",
            session_id="s",
            data={"message": "x" * 1_000_000},
        )
        # Nothing is capped → never matches
        assert rule.evaluate(ctx, state) is False

    def test_combined_caps_first_match_wins(self) -> None:
        rule = ResourceLimitRule(
            max_payload_bytes=10,
            max_concurrent_calls=2,
        )
        from lilith_core.policy_engine import PolicyState

        state = PolicyState()
        # Big payload → matches on payload first
        ctx = HookContext(
            hook_type=HookType.PRE_LLM_CALL,
            agent_name="Odin",
            session_id="s",
            data={"message": "x" * 1000},
        )
        assert rule.evaluate(ctx, state) is True
        # The "first" reason is recorded
        reason = state.get("resource_reason:Odin:s")
        assert "payload_size=" in reason


# ── YAML Loading for ResourceLimitRule ───────────────────────────────────────


class TestPolicyLoading:
    """ResourceLimitRule registered as YAML-loadable rule type."""

    def test_resource_limit_loads_from_yaml(self) -> None:
        import yaml

        spec = yaml.safe_load(
            """
policies:
  - name: payload-cap
    description: Reject calls with payload > 1MB
    priority: 30
    action: deny
    scope: {}
    rule:
      type: resource_limit
      max_payload_bytes: 1048576
      max_concurrent_calls: 5
      max_session_duration_seconds: 3600
"""
        )
        engine = PolicyEngine.from_dict(spec)
        assert len(engine.list_policies()) == 1
        p = engine.list_policies()[0]
        assert isinstance(p.rule, ResourceLimitRule)
        assert p.rule.max_payload_bytes == 1048576
        assert p.rule.max_concurrent_calls == 5
        assert p.rule.max_session_duration_seconds == 3600.0


# ── Helpers ─────────────────────────────────────────────────────────────────


class TestHelpers:
    """Module-level helpers: make_default_trail and summarize_entries."""

    def test_make_default_trail_under_root(self, tmp_path: Path) -> None:
        trail = make_default_trail(tmp_path)
        assert trail.path == tmp_path / ".ygg" / "audit.jsonl"
        assert trail.max_entries > 0
        assert trail.max_bytes > 0

    def test_summarize_entries(self, tmp_path: Path) -> None:
        entries = [
            AuditEntry(policy="p1", agent="Odin", session="s", action="allow"),
            AuditEntry(policy="p1", agent="Odin", session="s", action="deny"),
            AuditEntry(policy="p2", agent="Mimir", session="s", action="flag"),
        ]
        summary = summarize_entries(entries)
        assert summary["count"] == 3
        assert summary["by_action"]["allow"] == 1
        assert summary["by_action"]["deny"] == 1
        assert summary["by_action"]["flag"] == 1
        assert summary["by_agent"]["Odin"] == 2
