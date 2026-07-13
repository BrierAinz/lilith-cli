"""Tests for sandbox_hooks.py — integration between AgentSandbox and HookRegistry.

Covers:
    - activate_sandbox_hooks / deactivate_sandbox_hooks
    - _sandbox_tool_hook (tool gating via sandbox policy)
    - _sandbox_llm_hook (token budget + rate limiting via sandbox policy)
    - sandbox_hooks_active
    - get_sandbox_hook_stats
    - Edge cases: no policy, disabled policy, case-insensitive agent names
    - Interaction with existing hooks (priority ordering)
"""

from __future__ import annotations

import pytest

from lilith_core.hooks import HookContext, HookType, get_hook_registry
from lilith_core.sandbox import (
    AgentSandbox,
    SandboxPolicy,
    SandboxRule,
    SandboxRuleType,
    get_sandbox_registry,
)
from lilith_core.sandbox_hooks import (
    activate_sandbox_hooks,
    deactivate_sandbox_hooks,
    get_sandbox_hook_stats,
    sandbox_hooks_active,
)


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
def fresh_hooks():
    """Clear all hooks before each test."""
    reg = get_hook_registry()
    reg.clear()
    yield reg
    reg.clear()


@pytest.fixture
def fresh_sandbox_registry():
    """Return a fresh sandbox registry for each test."""
    # We can't easily replace the singleton, so we just clear it
    reg = get_sandbox_registry()
    # Remove all registered agents
    for agent in reg.list_agents():
        reg.remove(agent)
    reg.set_default(None)
    yield reg
    for agent in reg.list_agents():
        reg.remove(agent)
    reg.set_default(None)


@pytest.fixture
def tool_ctx():
    """A basic tool call hook context."""
    return HookContext(
        hook_type=HookType.PRE_TOOL_CALL,
        agent_name="Odin",
        session_id="sess-001",
        data={"tool_name": "terminal", "params": {"cmd": "ls"}},
    )


@pytest.fixture
def llm_ctx():
    """A basic LLM call hook context."""
    return HookContext(
        hook_type=HookType.PRE_LLM_CALL,
        agent_name="Odin",
        session_id="sess-001",
        data={"message": "Hello world"},
    )


# ── Activation / Deactivation ──────────────────────────────────────────────────


class TestActivateDeactivate:
    def test_activate_registers_both_hooks(self, fresh_hooks):
        assert not sandbox_hooks_active()
        activate_sandbox_hooks()
        assert sandbox_hooks_active()
        tool_hooks = fresh_hooks.hooks_for(HookType.PRE_TOOL_CALL)
        llm_hooks = fresh_hooks.hooks_for(HookType.PRE_LLM_CALL)
        assert len(tool_hooks) == 1
        assert len(llm_hooks) == 1
        assert tool_hooks[0].name == "sandbox_tool_gate"
        assert llm_hooks[0].name == "sandbox_llm_gate"
        assert tool_hooks[0].priority == -20
        assert llm_hooks[0].priority == -20

    def test_deactivate_removes_hooks(self, fresh_hooks):
        activate_sandbox_hooks()
        assert sandbox_hooks_active()
        deactivate_sandbox_hooks()
        assert not sandbox_hooks_active()
        assert len(fresh_hooks.hooks_for(HookType.PRE_TOOL_CALL)) == 0
        assert len(fresh_hooks.hooks_for(HookType.PRE_LLM_CALL)) == 0

    def test_activate_is_idempotent(self, fresh_hooks):
        activate_sandbox_hooks()
        activate_sandbox_hooks()
        assert len(fresh_hooks.hooks_for(HookType.PRE_TOOL_CALL)) == 1
        assert len(fresh_hooks.hooks_for(HookType.PRE_LLM_CALL)) == 1

    def test_deactivate_is_idempotent(self, fresh_hooks):
        deactivate_sandbox_hooks()
        deactivate_sandbox_hooks()
        assert not sandbox_hooks_active()


# ── Tool Hook ──────────────────────────────────────────────────────────────────


class TestSandboxToolHook:
    def test_no_policy_allows_tool(self, fresh_hooks, fresh_sandbox_registry, tool_ctx):
        activate_sandbox_hooks()
        result = fresh_hooks.fire(tool_ctx)
        assert result is not None
        assert result.data["tool_name"] == "terminal"

    def test_whitelist_blocks_unlisted_tool(
        self, fresh_hooks, fresh_sandbox_registry, tool_ctx
    ):
        policy = SandboxPolicy(
            name="read-only",
            rules=[
                SandboxRule(SandboxRuleType.ALLOWED_TOOLS, ["read_file", "search_files"]),
            ],
        )
        fresh_sandbox_registry.register("Odin", policy)
        activate_sandbox_hooks()
        result = fresh_hooks.fire(tool_ctx)
        assert result is None  # Blocked

    def test_whitelist_allows_listed_tool(
        self, fresh_hooks, fresh_sandbox_registry, tool_ctx
    ):
        policy = SandboxPolicy(
            name="read-only",
            rules=[
                SandboxRule(SandboxRuleType.ALLOWED_TOOLS, ["read_file", "terminal"]),
            ],
        )
        fresh_sandbox_registry.register("Odin", policy)
        activate_sandbox_hooks()
        result = fresh_hooks.fire(tool_ctx)
        assert result is not None
        assert result.data["tool_name"] == "terminal"

    def test_blacklist_blocks_denied_tool(
        self, fresh_hooks, fresh_sandbox_registry, tool_ctx
    ):
        policy = SandboxPolicy(
            name="no-shell",
            rules=[
                SandboxRule(SandboxRuleType.DENIED_TOOLS, ["terminal", "shell"]),
            ],
        )
        fresh_sandbox_registry.register("Odin", policy)
        activate_sandbox_hooks()
        result = fresh_hooks.fire(tool_ctx)
        assert result is None

    def test_no_subprocess_blocks_terminal(
        self, fresh_hooks, fresh_sandbox_registry, tool_ctx
    ):
        policy = SandboxPolicy(
            name="no-subprocess",
            rules=[
                SandboxRule(SandboxRuleType.NO_SUBPROCESS, True),
            ],
        )
        fresh_sandbox_registry.register("Odin", policy)
        activate_sandbox_hooks()
        result = fresh_hooks.fire(tool_ctx)
        assert result is None

    def test_disabled_policy_allows_all(
        self, fresh_hooks, fresh_sandbox_registry, tool_ctx
    ):
        policy = SandboxPolicy(
            name="disabled",
            rules=[
                SandboxRule(SandboxRuleType.DENIED_TOOLS, ["terminal"]),
            ],
            enabled=False,
        )
        fresh_sandbox_registry.register("Odin", policy)
        activate_sandbox_hooks()
        result = fresh_hooks.fire(tool_ctx)
        assert result is not None

    def test_case_insensitive_agent_lookup(
        self, fresh_hooks, fresh_sandbox_registry, tool_ctx
    ):
        policy = SandboxPolicy(
            name="case-test",
            rules=[
                SandboxRule(SandboxRuleType.DENIED_TOOLS, ["terminal"]),
            ],
        )
        fresh_sandbox_registry.register("ODIN", policy)
        activate_sandbox_hooks()
        result = fresh_hooks.fire(tool_ctx)
        assert result is None  # Should match "Odin" to "ODIN"

    def test_default_policy_applies_when_no_specific_policy(
        self, fresh_hooks, fresh_sandbox_registry, tool_ctx
    ):
        default = SandboxPolicy(
            name="default",
            rules=[
                SandboxRule(SandboxRuleType.DENIED_TOOLS, ["terminal"]),
            ],
        )
        fresh_sandbox_registry.set_default(default)
        activate_sandbox_hooks()
        result = fresh_hooks.fire(tool_ctx)
        assert result is None

    def test_no_tool_name_in_context_passes_through(
        self, fresh_hooks, fresh_sandbox_registry
    ):
        ctx = HookContext(
            hook_type=HookType.PRE_TOOL_CALL,
            agent_name="Odin",
            session_id="sess-001",
            data={"params": {}},  # No tool_name
        )
        activate_sandbox_hooks()
        result = fresh_hooks.fire(ctx)
        assert result is not None


# ── LLM Hook ───────────────────────────────────────────────────────────────────


class TestSandboxLLMHook:
    def test_no_policy_allows_llm(self, fresh_hooks, fresh_sandbox_registry, llm_ctx):
        activate_sandbox_hooks()
        result = fresh_hooks.fire(llm_ctx)
        assert result is not None

    def test_token_budget_blocks_when_exceeded(
        self, fresh_hooks, fresh_sandbox_registry, llm_ctx
    ):
        policy = SandboxPolicy(
            name="token-limited",
            rules=[
                SandboxRule(SandboxRuleType.MAX_TOKENS, 2),  # Very low budget
            ],
        )
        fresh_sandbox_registry.register("Odin", policy)
        activate_sandbox_hooks()
        result = fresh_hooks.fire(llm_ctx)
        # "Hello world" = 11 chars / 4 = 2 tokens (max(1, len//4) = 2)
        # used=0 + estimated=2 = 2 which is NOT > budget=2, so it passes
        # Budget is exceeded on the SECOND call: used=2 + estimated=2 = 4 > 2
        assert result is not None  # First call: 2 tokens <= 2 budget
        result2 = fresh_hooks.fire(llm_ctx)
        assert result2 is None  # Second call: accumulated 4 > 2

    def test_token_budget_allows_when_under(
        self, fresh_hooks, fresh_sandbox_registry, llm_ctx
    ):
        policy = SandboxPolicy(
            name="token-generous",
            rules=[
                SandboxRule(SandboxRuleType.MAX_TOKENS, 1000),
            ],
        )
        fresh_sandbox_registry.register("Odin", policy)
        activate_sandbox_hooks()
        result = fresh_hooks.fire(llm_ctx)
        assert result is not None

    def test_token_budget_tracks_across_calls(
        self, fresh_hooks, fresh_sandbox_registry, llm_ctx
    ):
        policy = SandboxPolicy(
            name="token-tracker",
            rules=[
                SandboxRule(SandboxRuleType.MAX_TOKENS, 10),
            ],
        )
        fresh_sandbox_registry.register("Odin", policy)
        activate_sandbox_hooks()
        # "Hello world" = 11 chars / 4 = 2 tokens per call
        # First call: used=0 + 2 = 2 <= 10 — allowed
        result1 = fresh_hooks.fire(llm_ctx)
        assert result1 is not None
        # Second call: used=2 + 2 = 4 <= 10 — allowed
        result2 = fresh_hooks.fire(llm_ctx)
        assert result2 is not None
        # Third call: used=4 + 2 = 6 <= 10 — allowed
        result3 = fresh_hooks.fire(llm_ctx)
        assert result3 is not None
        # Fourth call: used=6 + 2 = 8 <= 10 — allowed
        result4 = fresh_hooks.fire(llm_ctx)
        assert result4 is not None
        # Fifth call: used=8 + 2 = 10 <= 10 — allowed (exactly at budget)
        result5 = fresh_hooks.fire(llm_ctx)
        assert result5 is not None
        # Sixth call: used=10 + 2 = 12 > 10 — blocked
        result6 = fresh_hooks.fire(llm_ctx)
        assert result6 is None

    def test_rate_limit_blocks_when_exceeded(
        self, fresh_hooks, fresh_sandbox_registry, llm_ctx
    ):
        policy = SandboxPolicy(
            name="rate-limited",
            rules=[
                SandboxRule(SandboxRuleType.MAX_CALLS_PER_MIN, 2),
            ],
        )
        fresh_sandbox_registry.register("Odin", policy)
        activate_sandbox_hooks()
        result1 = fresh_hooks.fire(llm_ctx)
        assert result1 is not None
        result2 = fresh_hooks.fire(llm_ctx)
        assert result2 is not None
        result3 = fresh_hooks.fire(llm_ctx)
        assert result3 is None  # 3rd call exceeds limit of 2

    def test_rate_limit_resets_after_window(
        self, fresh_hooks, fresh_sandbox_registry, llm_ctx
    ):
        import time

        policy = SandboxPolicy(
            name="rate-limited",
            rules=[
                SandboxRule(SandboxRuleType.MAX_CALLS_PER_MIN, 1),
            ],
        )
        fresh_sandbox_registry.register("Odin", policy)
        activate_sandbox_hooks()
        # First call allowed
        result1 = fresh_hooks.fire(llm_ctx)
        assert result1 is not None
        # Second call blocked
        result2 = fresh_hooks.fire(llm_ctx)
        assert result2 is None
        # Manually expire the rate window in metadata
        llm_ctx.metadata.clear()
        # Third call allowed again (window reset)
        result3 = fresh_hooks.fire(llm_ctx)
        assert result3 is not None

    def test_non_string_message_skips_token_check(
        self, fresh_hooks, fresh_sandbox_registry
    ):
        ctx = HookContext(
            hook_type=HookType.PRE_LLM_CALL,
            agent_name="Odin",
            session_id="sess-001",
            data={"message": {"role": "user", "content": "hello"}},
        )
        policy = SandboxPolicy(
            name="token-limited",
            rules=[
                SandboxRule(SandboxRuleType.MAX_TOKENS, 0),
            ],
        )
        fresh_sandbox_registry.register("Odin", policy)
        activate_sandbox_hooks()
        result = fresh_hooks.fire(ctx)
        assert result is not None  # Non-string message, estimated_tokens=0


# ── Stats ──────────────────────────────────────────────────────────────────────


class TestGetSandboxHookStats:
    def test_before_activation(self, fresh_hooks, fresh_sandbox_registry):
        stats = get_sandbox_hook_stats()
        assert stats["tool_hook_registered"] is False
        assert stats["llm_hook_registered"] is False
        assert stats["registered_agents"] == []
        assert stats["has_default_policy"] is False

    def test_after_activation_with_policies(
        self, fresh_hooks, fresh_sandbox_registry
    ):
        fresh_sandbox_registry.register("Odin", SandboxPolicy(name="p1"))
        fresh_sandbox_registry.register("Mimir", SandboxPolicy(name="p2"))
        fresh_sandbox_registry.set_default(SandboxPolicy(name="default"))
        activate_sandbox_hooks()
        stats = get_sandbox_hook_stats()
        assert stats["tool_hook_registered"] is True
        assert stats["llm_hook_registered"] is True
        assert sorted(stats["registered_agents"]) == ["mimir", "odin"]
        assert stats["has_default_policy"] is True


# ── Priority ordering ────────────────────────────────────────────────────────────


class TestPriorityOrdering:
    def test_sandbox_runs_before_policy_engine(self, fresh_hooks, fresh_sandbox_registry):
        # Register a low-priority hook first
        def low_priority_hook(c):
            return c

        fresh_hooks.register(
            HookType.PRE_TOOL_CALL, low_priority_hook, name="low", priority=0
        )
        activate_sandbox_hooks()
        hooks = fresh_hooks.hooks_for(HookType.PRE_TOOL_CALL)
        names = [h.name for h in hooks]
        assert names.index("sandbox_tool_gate") < names.index("low")

    def test_sandbox_runs_before_other_high_priority(
        self, fresh_hooks, fresh_sandbox_registry
    ):
        def mid_hook(c):
            return c

        fresh_hooks.register(
            HookType.PRE_TOOL_CALL, mid_hook, name="mid", priority=-10
        )
        activate_sandbox_hooks()
        hooks = fresh_hooks.hooks_for(HookType.PRE_TOOL_CALL)
        names = [h.name for h in hooks]
        assert names.index("sandbox_tool_gate") < names.index("mid")
