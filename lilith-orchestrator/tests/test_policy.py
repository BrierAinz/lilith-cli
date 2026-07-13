"""Tests for Omnigent-inspired Policy Engine (lilith-orchestrator)."""
from __future__ import annotations

import time

import pytest

from lilith_orchestrator.policy import (
    PolicyConfig,
    PolicyDecision,
    PolicyEngine,
    PolicyEvent,
    PolicyState,
    PolicyViolation,
    with_policy,
)


# ── PolicyConfig ───────────────────────────────────────────────────────────


class TestPolicyConfig:
    def test_default_config_allows_everything(self):
        cfg = PolicyConfig()
        assert cfg.is_path_allowed("/any/path")
        assert cfg.is_path_allowed("C:\\Windows")

    def test_forbidden_path_blocks(self):
        cfg = PolicyConfig(forbidden_paths={"/etc"})
        assert not cfg.is_path_allowed("/etc/passwd")
        assert cfg.is_path_allowed("/tmp/safe")

    def test_allowed_path_permits(self):
        cfg = PolicyConfig(allowed_paths={"/home/user/projects"})
        assert cfg.is_path_allowed("/home/user/projects/foo")
        assert not cfg.is_path_allowed("/etc/passwd")

    def test_allowed_with_forbidden_intersect(self):
        cfg = PolicyConfig(
            allowed_paths={"/work"},
            forbidden_paths={"/work/secrets"},
        )
        assert cfg.is_path_allowed("/work/file.txt")
        assert not cfg.is_path_allowed("/work/secrets/key.pem")


# ── PolicyEngine: tool allow/deny ───────────────────────────────────────────


class TestPolicyEngineToolChecks:
    def test_default_allows_any_tool(self):
        engine = PolicyEngine()
        decision, violation, detail = engine.check_tool("agent1", "any_tool")
        assert decision in (PolicyDecision.ALLOW, PolicyDecision.AUDIT)
        assert violation is None

    def test_forbidden_tool_denied(self):
        cfg = PolicyConfig(forbidden_tools={"delete_file", "format_disk"})
        engine = PolicyEngine(cfg)
        decision, violation, _ = engine.check_tool("agent1", "delete_file")
        assert decision == PolicyDecision.DENY
        assert violation == PolicyViolation.TOOL_FORBIDDEN

    def test_allowed_tool_whitelist(self):
        cfg = PolicyConfig(allowed_tools={"read_file", "list_dir"})
        engine = PolicyEngine(cfg)
        decision, _, _ = engine.check_tool("agent1", "read_file")
        assert decision in (PolicyDecision.ALLOW, PolicyDecision.AUDIT)
        decision, violation, _ = engine.check_tool("agent1", "write_file")
        assert decision == PolicyDecision.DENY
        assert violation == PolicyViolation.TOOL_NOT_ALLOWED


# ── PolicyEngine: path checks ───────────────────────────────────────────────


class TestPolicyEnginePathChecks:
    def test_forbidden_path(self):
        cfg = PolicyConfig(forbidden_paths={"/etc", "C:\\Windows"})
        engine = PolicyEngine(cfg)
        decision, violation, _ = engine.check_tool("a", "read_file", path="/etc/passwd")
        assert decision == PolicyDecision.DENY
        assert violation == PolicyViolation.PATH_NOT_ALLOWED

    def test_allowed_path(self):
        cfg = PolicyConfig(allowed_paths={"/work"})
        engine = PolicyEngine(cfg)
        decision, _, _ = engine.check_tool("a", "read_file", path="/work/file.txt")
        assert decision in (PolicyDecision.ALLOW, PolicyDecision.AUDIT)


# ── PolicyEngine: resource limits ───────────────────────────────────────────


class TestPolicyEngineResourceLimits:
    def test_tool_call_limit(self):
        cfg = PolicyConfig(max_tool_calls=3)
        engine = PolicyEngine(cfg)
        # First 3 should pass
        for _ in range(3):
            decision, _, _ = engine.check_tool("a", "tool")
            assert decision != PolicyDecision.DENY
        # 4th should deny
        decision, violation, _ = engine.check_tool("a", "tool")
        assert decision == PolicyDecision.DENY
        assert violation == PolicyViolation.RESOURCE_EXCEEDED

    def test_wall_time_limit(self):
        cfg = PolicyConfig(max_wall_time_seconds=0.0)
        engine = PolicyEngine(cfg)
        engine.create_state("a")
        time.sleep(0.01)
        decision, violation, _ = engine.check_tool("a", "tool")
        assert decision == PolicyDecision.DENY
        assert violation == PolicyViolation.RESOURCE_EXCEEDED

    def test_rate_limit(self):
        cfg = PolicyConfig(rate_limit_per_minute=2)
        engine = PolicyEngine(cfg)
        # First 2 in same minute should pass
        for _ in range(2):
            decision, _, _ = engine.check_tool("a", "tool")
            assert decision != PolicyDecision.DENY
        # 3rd in same minute should deny
        decision, violation, _ = engine.check_tool("a", "tool")
        assert decision == PolicyDecision.DENY
        assert violation == PolicyViolation.RATE_LIMITED


# ── PolicyEngine: audit trail ───────────────────────────────────────────────


class TestPolicyEngineAuditTrail:
    def test_audit_records_all_events(self):
        cfg = PolicyConfig(audit_all=True)
        engine = PolicyEngine(cfg)
        engine.check_tool("a", "tool1")
        engine.check_tool("a", "tool2")
        events = engine.audit("a")
        assert len(events) >= 2
        assert all(isinstance(e, PolicyEvent) for e in events)
        assert all(e.agent_name == "a" for e in events)

    def test_audit_only_denies_when_not_full(self):
        cfg = PolicyConfig(audit_all=False, forbidden_tools={"dangerous"})
        engine = PolicyEngine(cfg)
        engine.check_tool("a", "safe_tool")
        engine.check_tool("a", "dangerous")  # denied
        events = engine.audit("a")
        # Should only contain the deny event
        deny_events = [e for e in events if e.decision == PolicyDecision.DENY]
        assert len(deny_events) >= 1

    def test_audit_isolated_per_agent(self):
        engine = PolicyEngine()
        engine.check_tool("a", "tool")
        engine.check_tool("b", "tool")
        assert len(engine.audit("a")) >= 1
        assert len(engine.audit("b")) >= 1


# ── PolicyState ─────────────────────────────────────────────────────────────


class TestPolicyState:
    def test_initial_state(self):
        state = PolicyState(config=PolicyConfig())
        assert state.tool_calls_total == 0
        assert state.wall_time_elapsed >= 0
        assert state.audit_trail == []

    def test_record_tool_call_increments(self):
        state = PolicyState(config=PolicyConfig())
        before = state.tool_calls_total
        state.record_tool_call()
        assert state.tool_calls_total == before + 1


# ── with_policy decorator ───────────────────────────────────────────────────


class TestWithPolicyDecorator:
    def test_decorator_allows_safe_call(self):
        engine = PolicyEngine()
        called = []

        @with_policy(engine, "agent1")
        def invoke(tool_name: str = "", **kwargs):
            called.append(tool_name)
            return "ok"

        result = invoke(tool_name="read_file")
        assert result == "ok"
        assert "read_file" in called

    def test_decorator_denies_forbidden(self):
        cfg = PolicyConfig(forbidden_tools={"dangerous"})
        engine = PolicyEngine(cfg)

        @with_policy(engine, "agent1")
        def invoke(tool_name: str = "", **kwargs):
            return "should not run"

        with pytest.raises(PermissionError, match="forbidden"):
            invoke(tool_name="dangerous")


# ── Reset / lifecycle ───────────────────────────────────────────────────────


class TestPolicyEngineLifecycle:
    def test_reset_clears_all_state(self):
        engine = PolicyEngine()
        engine.check_tool("a", "tool")
        engine.check_tool("b", "tool")
        engine.reset()
        assert engine.audit("a") == []
        assert engine.audit("b") == []

    def test_reset_specific_agent(self):
        engine = PolicyEngine()
        engine.check_tool("a", "tool")
        engine.check_tool("b", "tool")
        engine.reset("a")
        assert engine.audit("a") == []
        assert engine.audit("b") != []
