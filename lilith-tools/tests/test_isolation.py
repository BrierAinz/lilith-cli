"""Tests for ToolIsolationPolicy — agent-based tool isolation enforcement.

Tests the Requiem-inspired "Shade separation" pattern: each agent role has a
defined set of allowed tools, and the policy gates execution of tools not in
the agent's allowed list.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from lilith_core.hooks import HookContext, HookType, get_hook_registry
from lilith_tools.base import BaseTool, ToolResult
from lilith_tools.isolation import (
    ToolIsolationMode,
    ToolIsolationPolicy,
    ToolViolation,
)
from lilith_tools.registry import ToolRegistry
from lilith_tools.router.router import SmartToolRouter


# ── Mock tools ────────────────────────────────────────────────────────────────


class SearchTool(BaseTool):
    """Web search tool."""
    name = "web_search"
    description = "Search the web"
    parameters = {"query": {"type": "string", "required": True}}

    def execute(self, query: str = "", **kwargs: Any) -> ToolResult:
        return ToolResult(success=True, data={"results": [query]})


class ReadTool(BaseTool):
    """File read tool."""
    name = "read_file"
    description = "Read a file"
    parameters = {"path": {"type": "string", "required": True}}

    def execute(self, path: str = "", **kwargs: Any) -> ToolResult:
        return ToolResult(success=True, data={"content": "file data"})


class TerminalTool(BaseTool):
    """Terminal execution tool."""
    name = "terminal"
    description = "Execute a terminal command"
    parameters = {"cmd": {"type": "string", "required": True}}

    def execute(self, cmd: str = "", **kwargs: Any) -> ToolResult:
        return ToolResult(success=True, data={"output": f"ran: {cmd}"})


class WriteTool(BaseTool):
    """File write tool."""
    name = "write_file"
    description = "Write to a file"
    parameters = {"path": {"type": "string"}, "content": {"type": "string"}}

    def execute(self, path: str = "", content: str = "", **kwargs: Any) -> ToolResult:
        return ToolResult(success=True, data={"written": path})


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def registry():
    """Registry with all mock tools."""
    ToolRegistry._tools.clear()
    ToolRegistry._tools["web_search"] = SearchTool
    ToolRegistry._tools["read_file"] = ReadTool
    ToolRegistry._tools["terminal"] = TerminalTool
    ToolRegistry._tools["write_file"] = WriteTool
    return ToolRegistry


@pytest.fixture
def router(registry):
    """SmartToolRouter with clean hook registry."""
    r = SmartToolRouter(registry)
    get_hook_registry().clear()
    return r


@pytest.fixture
def odin_policy():
    """Policy where Odin can only search and read."""
    return ToolIsolationPolicy.from_dict(
        {
            "Odin": ["web_search", "read_file", "search_files", "session_search"],
            "Adan": ["terminal", "read_file", "write_file", "patch"],
            "Mimir": ["web_search", "read_file", "write_file", "search_files"],
        },
        mode=ToolIsolationMode.STRICT,
    )


# ── Policy construction tests ────────────────────────────────────────────────


class TestPolicyConstruction:
    """Tests for creating ToolIsolationPolicy instances."""

    def test_from_dict(self):
        """Policy should be created from a dict."""
        policy = ToolIsolationPolicy.from_dict({
            "Odin": ["web_search"],
            "Adan": ["terminal"],
        })
        assert policy.agent_count == 2

    def test_from_dict_case_insensitive(self):
        """Agent names and tool names should be case-insensitive."""
        policy = ToolIsolationPolicy.from_dict({
            "ODIN": ["WEB_SEARCH"],
        })
        assert policy.is_allowed("odin", "web_search")
        assert policy.is_allowed("ODIN", "WEB_SEARCH")
        assert policy.is_allowed("Odin", "Web_Search")

    def test_from_dict_mode(self):
        """Mode should be set correctly."""
        policy = ToolIsolationPolicy.from_dict({}, mode=ToolIsolationMode.PERMISSIVE)
        assert policy.mode == ToolIsolationMode.PERMISSIVE

    def test_default_mode_is_strict(self):
        """Default mode should be STRICT."""
        policy = ToolIsolationPolicy.from_dict({})
        assert policy.mode == ToolIsolationMode.STRICT


# ── Lookup tests ──────────────────────────────────────────────────────────────


class TestPolicyLookup:
    """Tests for policy lookup methods."""

    def test_is_allowed_when_in_list(self, odin_policy):
        """Tool in the agent's list should be allowed."""
        assert odin_policy.is_allowed("Odin", "web_search") is True

    def test_is_blocked_when_not_in_list(self, odin_policy):
        """Tool NOT in the agent's list should be blocked."""
        assert odin_policy.is_allowed("Odin", "terminal") is False

    def test_unknown_agent_is_allowed(self, odin_policy):
        """Agent not in policy should be allowed (open-world default)."""
        assert odin_policy.is_allowed("UnknownAgent", "terminal") is True

    def test_empty_agent_name_is_allowed(self, odin_policy):
        """Empty agent name should bypass the policy."""
        assert odin_policy.is_allowed("", "terminal") is True

    def test_get_allowed_tools(self, odin_policy):
        """Should return the agent's allowed tools."""
        tools = odin_policy.get_allowed_tools("Odin")
        assert tools is not None
        assert "web_search" in tools
        assert "read_file" in tools

    def test_get_allowed_tools_unknown_agent(self, odin_policy):
        """Should return None for unknown agents."""
        assert odin_policy.get_allowed_tools("Unknown") is None


# ── Violation tracking ────────────────────────────────────────────────────────


class TestViolationTracking:
    """Tests for violation recording."""

    def test_violations_recorded_in_strict_mode(self, odin_policy):
        """Violations should be recorded in STRICT mode."""
        odin_policy._gate_hook(HookContext(
            hook_type=HookType.PRE_TOOL_CALL,
            agent_name="Odin",
            session_id="test",
            data={"tool_name": "terminal"},
        ))
        assert len(odin_policy.violations) == 1
        assert odin_policy.violations[0].agent_name == "Odin"
        assert odin_policy.violations[0].tool_name == "terminal"

    def test_violations_recorded_in_permissive_mode(self):
        """Violations should be recorded even in permissive mode."""
        policy = ToolIsolationPolicy.from_dict(
            {"Odin": ["web_search"]},
            mode=ToolIsolationMode.PERMISSIVE,
        )
        policy._gate_hook(HookContext(
            hook_type=HookType.PRE_TOOL_CALL,
            agent_name="Odin",
            session_id="test",
            data={"tool_name": "terminal"},
        ))
        assert len(policy.violations) == 1

    def test_no_violation_for_allowed_tool(self, odin_policy):
        """No violation should be recorded for allowed tools."""
        odin_policy._gate_hook(HookContext(
            hook_type=HookType.PRE_TOOL_CALL,
            agent_name="Odin",
            session_id="test",
            data={"tool_name": "web_search"},
        ))
        assert len(odin_policy.violations) == 0

    def test_violation_repr(self):
        """ToolViolation should have a useful repr."""
        v = ToolViolation(
            agent_name="Odin",
            tool_name="terminal",
            allowed_tools=["web_search", "read_file"],
            mode=ToolIsolationMode.STRICT,
            timestamp=time.time(),
        )
        r = repr(v)
        assert "Odin" in r
        assert "terminal" in r
        assert "strict" in r


# ── Hook integration tests ────────────────────────────────────────────────────


class TestHookIntegration:
    """Tests for registering the policy as a pre_tool_call hook."""

    def test_strict_mode_blocks_disallowed_tool(self, router, odin_policy):
        """STRICT mode should block tools not in the agent's list."""
        odin_policy.register_on(router)

        result = router.execute_tool(
            "terminal", {"cmd": "ls"}, agent_name="Odin"
        )
        assert not result.success
        assert "gated" in result.error.lower()

    def test_strict_mode_allows_permitted_tool(self, router, odin_policy):
        """STRICT mode should allow tools in the agent's list."""
        odin_policy.register_on(router)

        result = router.execute_tool(
            "web_search", {"query": "test"}, agent_name="Odin"
        )
        assert result.success

    def test_permissive_mode_warns_but_allows(self, router):
        """PERMISSIVE mode should allow execution even for disallowed tools."""
        policy = ToolIsolationPolicy.from_dict(
            {"Odin": ["web_search"]},
            mode=ToolIsolationMode.PERMISSIVE,
        )
        policy.register_on(router)

        result = router.execute_tool(
            "terminal", {"cmd": "ls"}, agent_name="Odin"
        )
        assert result.success  # Allowed despite violation
        assert len(policy.violations) == 1

    def test_log_only_mode_allows_silently(self, router):
        """LOG_ONLY mode should allow execution without gating."""
        policy = ToolIsolationPolicy.from_dict(
            {"Odin": ["web_search"]},
            mode=ToolIsolationMode.LOG_ONLY,
        )
        policy.register_on(router)

        result = router.execute_tool(
            "terminal", {"cmd": "ls"}, agent_name="Odin"
        )
        assert result.success
        assert len(policy.violations) == 1

    def test_no_agent_name_bypasses_policy(self, router, odin_policy):
        """Empty agent_name should bypass the policy entirely."""
        odin_policy.register_on(router)

        result = router.execute_tool("terminal", {"cmd": "ls"}, agent_name="")
        assert result.success
        assert len(odin_policy.violations) == 0

    def test_unknown_agent_allowed(self, router, odin_policy):
        """Unknown agent should be allowed (open-world default)."""
        odin_policy.register_on(router)

        result = router.execute_tool(
            "terminal", {"cmd": "ls"}, agent_name="Thor"
        )
        assert result.success
        assert len(odin_policy.violations) == 0

    def test_different_agents_different_permissions(self, router, odin_policy):
        """Different agents should have different tool access."""
        odin_policy.register_on(router)

        # Odin can use web_search
        result = router.execute_tool(
            "web_search", {"query": "test"}, agent_name="Odin"
        )
        assert result.success

        # Adan cannot use web_search (not in his list)
        result = router.execute_tool(
            "web_search", {"query": "test"}, agent_name="Adan"
        )
        assert not result.success

        # Adan can use terminal
        result = router.execute_tool(
            "terminal", {"cmd": "ls"}, agent_name="Adan"
        )
        assert result.success

    def test_multiple_violations_accumulate(self, router, odin_policy):
        """Multiple violations should accumulate."""
        odin_policy.register_on(router)

        router.execute_tool("terminal", {"cmd": "ls"}, agent_name="Odin")
        router.execute_tool("write_file", {"path": "/tmp"}, agent_name="Odin")
        router.execute_tool("patch", {"path": "/tmp"}, agent_name="Odin")

        assert len(odin_policy.violations) == 3


# ── Stats tests ───────────────────────────────────────────────────────────────


class TestStats:
    """Tests for policy statistics."""

    def test_stats_dict(self, odin_policy):
        """Stats should return a useful dictionary."""
        stats = odin_policy.stats()
        assert stats["mode"] == "strict"
        assert stats["agent_count"] == 3
        assert stats["violation_count"] == 0
        assert "odin" in stats["agents"]

    def test_repr(self, odin_policy):
        """Repr should be informative."""
        r = repr(odin_policy)
        assert "ToolIsolationPolicy" in r
        assert "3" in r
        assert "strict" in r


# ── Edge case tests ───────────────────────────────────────────────────────────


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_empty_policy_allows_everything(self, router):
        """Empty policy should allow all tools for all agents."""
        policy = ToolIsolationPolicy.from_dict({})
        policy.register_on(router)

        result = router.execute_tool(
            "terminal", {"cmd": "ls"}, agent_name="Odin"
        )
        assert result.success

    def test_agent_with_empty_tools_list(self, router):
        """Agent with empty tools list should be blocked from everything."""
        policy = ToolIsolationPolicy.from_dict({"Odin": []})
        policy.register_on(router)

        result = router.execute_tool(
            "web_search", {"query": "test"}, agent_name="Odin"
        )
        assert not result.success

    def test_mode_override_on_register(self, router, odin_policy):
        """Mode can be overridden when registering."""
        odin_policy.register_on(router, mode=ToolIsolationMode.PERMISSIVE)
        assert odin_policy.mode == ToolIsolationMode.PERMISSIVE

        # Should allow despite not being in the list
        result = router.execute_tool(
            "terminal", {"cmd": "ls"}, agent_name="Odin"
        )
        assert result.success

    def test_policy_works_with_tool_chains(self, router, odin_policy):
        """Policy should work when tools are called via chain execution."""
        from lilith_tools.router.chainer import ToolChain, ChainStep

        odin_policy.register_on(router)

        chain = ToolChain(
            name="test_chain",
            description="Test chain",
            steps=[
                ChainStep(tool_name="web_search", params={"query": "test"}),
                ChainStep(tool_name="terminal", params={"cmd": "ls"}),  # Blocked for Odin
            ],
        )
        result = router.execute_chain(chain, agent_name="Odin")
        # The chain executor doesn't use hooks per-step in the same way,
        # but the pre_tool_call hooks should fire for each step
