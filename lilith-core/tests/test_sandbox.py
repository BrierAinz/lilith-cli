"""Tests for sandbox.py — AgentSandbox, SandboxPolicy, SandboxRegistry.

Covers:
    - SandboxRuleType enum
    - SandboxAction enum
    - SandboxRule dataclass
    - SandboxViolation dataclass
    - SandboxPolicy (get_rule, has_rule, to_dict)
    - SandboxState (call tracking, token tracking, violations, reset)
    - AgentSandbox (context manager, run, check_tool, violations, stats)
    - SandboxRegistry (register, get, set_default, remove, list_agents, stats)
    - SandboxError
    - get_sandbox_registry singleton
"""

from __future__ import annotations

import time

import pytest

from lilith_core.sandbox import (
    AgentSandbox,
    SandboxAction,
    SandboxError,
    SandboxPolicy,
    SandboxRegistry,
    SandboxRule,
    SandboxRuleType,
    SandboxState,
    SandboxViolation,
    get_sandbox_registry,
)


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
def fresh_registry():
    """Return a fresh SandboxRegistry for each test."""
    reg = SandboxRegistry()
    return reg


@pytest.fixture
def permissive_policy():
    """A policy with no restrictions."""
    return SandboxPolicy(name="permissive", rules=[])


@pytest.fixture
def restrictive_policy():
    """A policy that blocks file writes and subprocess."""
    return SandboxPolicy(
        name="restrictive",
        rules=[
            SandboxRule(SandboxRuleType.NO_FILE_WRITE, True),
            SandboxRule(SandboxRuleType.NO_SUBPROCESS, True),
            SandboxRule(SandboxRuleType.MAX_EXEC_TIME, 5),
        ],
    )


@pytest.fixture
def tool_whitelist_policy():
    """A policy that only allows read_file and search_files."""
    return SandboxPolicy(
        name="read-only",
        rules=[
            SandboxRule(SandboxRuleType.ALLOWED_TOOLS, ["read_file", "search_files"]),
        ],
    )


@pytest.fixture
def tool_blacklist_policy():
    """A policy that denies terminal and shell."""
    return SandboxPolicy(
        name="no-shell",
        rules=[
            SandboxRule(SandboxRuleType.DENIED_TOOLS, ["terminal", "shell"]),
        ],
    )


# ── Enum tests ─────────────────────────────────────────────────────────────────


class TestSandboxRuleType:
    def test_all_members_present(self):
        members = {m.value for m in SandboxRuleType}
        expected = {
            "max_exec_time",
            "max_memory_mb",
            "no_network",
            "no_file_write",
            "no_file_delete",
            "no_subprocess",
            "allowed_tools",
            "denied_tools",
            "max_tokens",
            "max_calls_per_min",
        }
        assert members == expected

    def test_member_lookup(self):
        assert SandboxRuleType.MAX_EXEC_TIME.value == "max_exec_time"
        assert SandboxRuleType.NO_NETWORK.value == "no_network"


class TestSandboxAction:
    def test_all_members_present(self):
        members = {m.value for m in SandboxAction}
        assert members == {"block", "warn", "terminate"}


# ── Data class tests ───────────────────────────────────────────────────────────


class TestSandboxRule:
    def test_defaults(self):
        rule = SandboxRule(SandboxRuleType.MAX_EXEC_TIME, 30)
        assert rule.type == SandboxRuleType.MAX_EXEC_TIME
        assert rule.value == 30
        assert rule.action == SandboxAction.BLOCK

    def test_custom_action(self):
        rule = SandboxRule(SandboxRuleType.NO_NETWORK, True, SandboxAction.WARN)
        assert rule.action == SandboxAction.WARN


class TestSandboxViolation:
    def test_to_dict(self):
        v = SandboxViolation(
            rule_type=SandboxRuleType.NO_FILE_WRITE,
            message="File write blocked",
            context={"file": "/etc/passwd"},
        )
        d = v.to_dict()
        assert d["rule_type"] == "no_file_write"
        assert d["message"] == "File write blocked"
        assert d["context"] == {"file": "/etc/passwd"}
        assert "timestamp" in d


class TestSandboxPolicy:
    def test_get_rule_found(self, restrictive_policy):
        rule = restrictive_policy.get_rule(SandboxRuleType.NO_FILE_WRITE)
        assert rule is not None
        assert rule.value is True

    def test_get_rule_not_found(self, restrictive_policy):
        rule = restrictive_policy.get_rule(SandboxRuleType.MAX_MEMORY_MB)
        assert rule is None

    def test_has_rule(self, restrictive_policy):
        assert restrictive_policy.has_rule(SandboxRuleType.NO_FILE_WRITE) is True
        assert restrictive_policy.has_rule(SandboxRuleType.MAX_MEMORY_MB) is False

    def test_to_dict(self, restrictive_policy):
        d = restrictive_policy.to_dict()
        assert d["name"] == "restrictive"
        assert d["enabled"] is True
        assert len(d["rules"]) == 3
        assert d["rules"][0]["type"] == "no_file_write"

    def test_empty_policy(self):
        policy = SandboxPolicy(name="empty")
        assert policy.rules == []
        assert policy.description == ""


# ── SandboxState tests ─────────────────────────────────────────────────────────


class TestSandboxState:
    def test_initial_state(self):
        state = SandboxState()
        assert state.call_count_last_minute == 0
        assert state.token_count == 0
        assert state.elapsed == 0.0
        assert state.violations == []
        assert state.terminated is False

    def test_record_call(self):
        state = SandboxState()
        state.record_call()
        assert state.call_count_last_minute == 1

    def test_record_tokens(self):
        state = SandboxState()
        state.record_tokens(100)
        state.record_tokens(50)
        assert state.token_count == 150

    def test_add_violation(self):
        state = SandboxState()
        v = SandboxViolation(SandboxRuleType.NO_FILE_WRITE, "blocked")
        state.add_violation(v)
        assert len(state.violations) == 1
        assert state.violations[0].message == "blocked"

    def test_terminate(self):
        state = SandboxState()
        assert state.terminated is False
        state.terminate()
        assert state.terminated is True

    def test_reset(self):
        state = SandboxState()
        state.record_call()
        state.record_tokens(100)
        state.add_violation(SandboxViolation(SandboxRuleType.NO_FILE_WRITE, "blocked"))
        state.terminate()
        state._start_time = time.time()
        state.reset()
        assert state.call_count_last_minute == 0
        assert state.token_count == 0
        assert state.violations == []
        assert state.terminated is False
        assert state.elapsed == 0.0

    def test_call_count_last_minute_ignores_old_calls(self):
        state = SandboxState()
        # Manually inject an old timestamp
        state._call_timestamps.append(time.time() - 120)
        state._call_timestamps.append(time.time())
        assert state.call_count_last_minute == 1


# ── AgentSandbox tests ─────────────────────────────────────────────────────────


class TestAgentSandbox:
    def test_context_manager_activation(self, permissive_policy):
        sandbox = AgentSandbox(permissive_policy)
        assert sandbox.is_active is False
        with sandbox:
            assert sandbox.is_active is True
            assert sandbox.state.elapsed >= 0.0
        assert sandbox.is_active is False

    def test_run_without_context_manager_raises(self, permissive_policy):
        sandbox = AgentSandbox(permissive_policy)
        with pytest.raises(RuntimeError, match="Sandbox not active"):
            sandbox.run(lambda: 42)

    def test_run_simple_function(self, permissive_policy):
        sandbox = AgentSandbox(permissive_policy)
        with sandbox:
            result = sandbox.run(lambda x: x * 2, 21)
        assert result == 42

    def test_run_with_kwargs(self, permissive_policy):
        sandbox = AgentSandbox(permissive_policy)
        with sandbox:
            result = sandbox.run(lambda a, b=10: a + b, 5, b=3)
        assert result == 8

    def test_timeout_violation_blocks(self, restrictive_policy):
        sandbox = AgentSandbox(restrictive_policy)
        with sandbox:
            # Simulate elapsed time by setting start time far in the past
            sandbox.state._start_time = time.time() - 10
            with pytest.raises(SandboxError, match="timeout"):
                sandbox.run(lambda: 42)
        assert len(sandbox.violations) == 1
        assert sandbox.violations[0].rule_type == SandboxRuleType.MAX_EXEC_TIME

    def test_timeout_terminate_action(self):
        policy = SandboxPolicy(
            name="terminate",
            rules=[
                SandboxRule(SandboxRuleType.MAX_EXEC_TIME, 1, SandboxAction.TERMINATE),
            ],
        )
        sandbox = AgentSandbox(policy)
        with sandbox:
            sandbox.state._start_time = time.time() - 10
            with pytest.raises(SandboxError, match="timeout"):
                sandbox.run(lambda: 42)
        assert sandbox.is_terminated is True

    def test_rate_limit_blocks(self):
        policy = SandboxPolicy(
            name="rate-limited",
            rules=[
                SandboxRule(SandboxRuleType.MAX_CALLS_PER_MIN, 2),
            ],
        )
        sandbox = AgentSandbox(policy)
        with sandbox:
            sandbox.run(lambda: 1)
            sandbox.run(lambda: 2)
            with pytest.raises(SandboxError, match="Rate limit exceeded"):
                sandbox.run(lambda: 3)
        assert len(sandbox.violations) == 1
        assert sandbox.violations[0].rule_type == SandboxRuleType.MAX_CALLS_PER_MIN

    def test_token_budget_blocks(self):
        policy = SandboxPolicy(
            name="token-limited",
            rules=[
                SandboxRule(SandboxRuleType.MAX_TOKENS, 100),
            ],
        )
        sandbox = AgentSandbox(policy)
        with sandbox:
            sandbox.state.record_tokens(150)
            with pytest.raises(SandboxError, match="Token budget exceeded"):
                sandbox.run(lambda: 42)

    def test_check_tool_allowed(self, permissive_policy):
        sandbox = AgentSandbox(permissive_policy)
        with sandbox:
            assert sandbox.check_tool("read_file") is True

    def test_check_tool_whitelist_blocks(self, tool_whitelist_policy):
        sandbox = AgentSandbox(tool_whitelist_policy)
        with sandbox:
            assert sandbox.check_tool("read_file") is True
            assert sandbox.check_tool("search_files") is True
            with pytest.raises(SandboxError, match="not in allowed list"):
                sandbox.check_tool("terminal")
        assert len(sandbox.violations) == 1

    def test_check_tool_blacklist_blocks(self, tool_blacklist_policy):
        sandbox = AgentSandbox(tool_blacklist_policy)
        with sandbox:
            assert sandbox.check_tool("read_file") is True
            with pytest.raises(SandboxError, match="in denied list"):
                sandbox.check_tool("terminal")

    def test_check_tool_no_subprocess_blocks(self, restrictive_policy):
        sandbox = AgentSandbox(restrictive_policy)
        with sandbox:
            with pytest.raises(SandboxError, match="blocked by NO_SUBPROCESS"):
                sandbox.check_tool("terminal")

    def test_check_tool_inactive_returns_true(self, tool_whitelist_policy):
        sandbox = AgentSandbox(tool_whitelist_policy)
        # Not inside context manager
        assert sandbox.check_tool("terminal") is True

    def test_stats(self, restrictive_policy):
        sandbox = AgentSandbox(restrictive_policy)
        with sandbox:
            sandbox.run(lambda: 42)
            stats = sandbox.stats()
        assert stats["policy"] == "restrictive"
        # stats() is called inside the context manager, so active is True
        assert stats["active"] is True
        assert stats["violation_count"] == 0
        assert stats["calls_last_minute"] == 1

    def test_terminated_prevents_run(self):
        policy = SandboxPolicy(
            name="terminate",
            rules=[
                SandboxRule(SandboxRuleType.MAX_EXEC_TIME, 1, SandboxAction.TERMINATE),
            ],
        )
        sandbox = AgentSandbox(policy)
        with sandbox:
            sandbox.state._start_time = time.time() - 10
            with pytest.raises(SandboxError):
                sandbox.run(lambda: 42)
            # After termination, further runs are blocked
            with pytest.raises(SandboxError, match="terminated"):
                sandbox.run(lambda: 42)

    def test_run_post_call_timeout_check(self):
        policy = SandboxPolicy(
            name="slow",
            rules=[
                SandboxRule(SandboxRuleType.MAX_EXEC_TIME, 0.01),
            ],
        )
        sandbox = AgentSandbox(policy)
        with sandbox:
            # This should trigger timeout in the post-call check
            time.sleep(0.02)
            with pytest.raises(SandboxError, match="timeout"):
                sandbox.run(lambda: 42)


# ── SandboxRegistry tests ──────────────────────────────────────────────────────


class TestSandboxRegistry:
    def test_register_and_get(self, fresh_registry):
        policy = SandboxPolicy(name="test-policy")
        fresh_registry.register("Odin", policy)
        assert fresh_registry.get("Odin") == policy

    def test_get_case_insensitive(self, fresh_registry):
        policy = SandboxPolicy(name="test-policy")
        fresh_registry.register("Odin", policy)
        assert fresh_registry.get("odin") == policy
        assert fresh_registry.get("ODIN") == policy

    def test_get_default(self, fresh_registry):
        default = SandboxPolicy(name="default")
        fresh_registry.set_default(default)
        assert fresh_registry.get("Unknown") == default

    def test_get_none_when_no_default(self, fresh_registry):
        assert fresh_registry.get("Unknown") is None

    def test_remove(self, fresh_registry):
        policy = SandboxPolicy(name="test-policy")
        fresh_registry.register("Odin", policy)
        assert fresh_registry.remove("Odin") is True
        assert fresh_registry.get("Odin") is None
        assert fresh_registry.remove("Odin") is False

    def test_list_agents(self, fresh_registry):
        fresh_registry.register("Odin", SandboxPolicy(name="p1"))
        fresh_registry.register("Mimir", SandboxPolicy(name="p2"))
        agents = fresh_registry.list_agents()
        assert sorted(agents) == ["mimir", "odin"]

    def test_stats(self, fresh_registry):
        fresh_registry.register("Odin", SandboxPolicy(name="p1"))
        fresh_registry.set_default(SandboxPolicy(name="default"))
        stats = fresh_registry.stats()
        assert stats["registered_agents"] == 1
        assert stats["agents"] == ["odin"]
        assert stats["has_default"] is True


class TestGetSandboxRegistry:
    def test_singleton(self):
        reg1 = get_sandbox_registry()
        reg2 = get_sandbox_registry()
        assert reg1 is reg2

    def test_is_sandbox_registry(self):
        reg = get_sandbox_registry()
        assert isinstance(reg, SandboxRegistry)


# ── SandboxError tests ─────────────────────────────────────────────────────────


class TestSandboxError:
    def test_is_exception(self):
        with pytest.raises(SandboxError, match="test error"):
            raise SandboxError("test error")

    def test_can_be_caught_as_exception(self):
        try:
            raise SandboxError("boom")
        except Exception as exc:
            assert isinstance(exc, SandboxError)
            assert str(exc) == "boom"
