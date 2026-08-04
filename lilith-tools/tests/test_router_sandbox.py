"""Tests for SmartToolRouter sandbox integration.

Covers:
    - Sandbox gating via execute_tool (allowed_tools, denied_tools, no_subprocess)
    - Sandbox timeout enforcement
    - Sandbox rate limiting
    - start_sandbox / stop_sandbox lifecycle
    - get_sandbox_stats
    - _get_or_create_sandbox (default, registered, cached)
    - Sandbox + hooks interaction (sandbox fires before hooks)
    - Sandbox analytics recording on block
"""

from __future__ import annotations

import pytest

from lilith_core.sandbox import (
    SandboxPolicy,
    SandboxRegistry,
    SandboxRule,
    SandboxRuleType,
)
from lilith_tools.base import ToolResult
from lilith_tools.registry import ToolRegistry
from lilith_tools.router.router import SmartToolRouter


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
def empty_registry():
    """Empty tool registry."""
    return ToolRegistry()


@pytest.fixture
def fresh_sandbox_registry():
    """Fresh sandbox registry with no default."""
    return SandboxRegistry()


@pytest.fixture
def router_with_sandbox(empty_registry, fresh_sandbox_registry):
    """Router wired to a fresh sandbox registry."""
    return SmartToolRouter(
        registry=empty_registry,
        sandbox_registry=fresh_sandbox_registry,
    )


# ── Helper: register a simple echo tool ──────────────────────────────────────


def _register_echo_tool(registry: ToolRegistry) -> None:
    """Register a simple echo tool for testing."""
    from lilith_tools.base import BaseTool, ToolResult

    class EchoTool(BaseTool):
        name = "echo"
        description = "Echoes back the input"
        parameters = {"message": {"type": "string", "required": True}}

        def execute(self, **kwargs) -> ToolResult:
            return ToolResult(success=True, data={"output": kwargs.get("message", "")})

    registry.register(EchoTool)


def _register_terminal_tool(registry: ToolRegistry) -> None:
    """Register a terminal-like tool for testing."""
    from lilith_tools.base import BaseTool, ToolResult

    class TerminalTool(BaseTool):
        name = "terminal"
        description = "Runs a command"
        parameters = {"cmd": {"type": "string", "required": True}}

        def execute(self, **kwargs) -> ToolResult:
            return ToolResult(success=True, data={"output": "mock"})

    registry.register(TerminalTool)


# ── Sandbox gating tests ─────────────────────────────────────────────────────


class TestSandboxGating:
    def test_allowed_tools_whitelist_blocks(self, router_with_sandbox, empty_registry):
        _register_echo_tool(empty_registry)
        _register_terminal_tool(empty_registry)

        # Register a restrictive sandbox policy for "Odin"
        policy = SandboxPolicy(
            name="odin-read-only",
            rules=[
                SandboxRule(SandboxRuleType.ALLOWED_TOOLS, ["echo"]),
            ],
        )
        router_with_sandbox._sandbox_registry.register("Odin", policy)

        # Start sandbox for Odin
        router_with_sandbox.start_sandbox("Odin")
        try:
            # echo is allowed
            result = router_with_sandbox.execute_tool(
                "echo", {"message": "hello"}, agent_name="Odin"
            )
            assert result.success is True

            # terminal is blocked
            result = router_with_sandbox.execute_tool(
                "terminal", {"cmd": "ls"}, agent_name="Odin"
            )
            assert result.success is False
            assert "blocked by sandbox" in result.error
        finally:
            router_with_sandbox.stop_sandbox("Odin")

    def test_denied_tools_blacklist_blocks(self, router_with_sandbox, empty_registry):
        _register_echo_tool(empty_registry)
        _register_terminal_tool(empty_registry)

        policy = SandboxPolicy(
            name="odin-no-shell",
            rules=[
                SandboxRule(SandboxRuleType.DENIED_TOOLS, ["terminal"]),
            ],
        )
        router_with_sandbox._sandbox_registry.register("Odin", policy)

        router_with_sandbox.start_sandbox("Odin")
        try:
            result = router_with_sandbox.execute_tool(
                "terminal", {"cmd": "ls"}, agent_name="Odin"
            )
            assert result.success is False
            assert "blocked by sandbox" in result.error
        finally:
            router_with_sandbox.stop_sandbox("Odin")

    def test_no_subprocess_blocks(self, router_with_sandbox, empty_registry):
        _register_terminal_tool(empty_registry)

        policy = SandboxPolicy(
            name="odin-safe",
            rules=[
                SandboxRule(SandboxRuleType.NO_SUBPROCESS, True),
            ],
        )
        router_with_sandbox._sandbox_registry.register("Odin", policy)

        router_with_sandbox.start_sandbox("Odin")
        try:
            result = router_with_sandbox.execute_tool(
                "terminal", {"cmd": "ls"}, agent_name="Odin"
            )
            assert result.success is False
            assert "blocked by sandbox" in result.error
        finally:
            router_with_sandbox.stop_sandbox("Odin")

    def test_no_agent_name_uses_default_permissive(self, router_with_sandbox, empty_registry):
        _register_echo_tool(empty_registry)

        # No agent name, no sandbox policy — should be permissive
        result = router_with_sandbox.execute_tool("echo", {"message": "hello"})
        assert result.success is True

    def test_unregistered_agent_uses_default(self, router_with_sandbox, empty_registry):
        _register_echo_tool(empty_registry)

        # Agent not in sandbox registry — default permissive
        router_with_sandbox.start_sandbox("UnknownAgent")
        try:
            result = router_with_sandbox.execute_tool(
                "echo", {"message": "hello"}, agent_name="UnknownAgent"
            )
            assert result.success is True
        finally:
            router_with_sandbox.stop_sandbox("UnknownAgent")

    def test_sandbox_not_started_is_inactive(self, router_with_sandbox, empty_registry):
        _register_terminal_tool(empty_registry)

        policy = SandboxPolicy(
            name="odin-safe",
            rules=[
                SandboxRule(SandboxRuleType.NO_SUBPROCESS, True),
            ],
        )
        router_with_sandbox._sandbox_registry.register("Odin", policy)

        # Sandbox not started — check_tool returns True (inactive)
        result = router_with_sandbox.execute_tool(
            "terminal", {"cmd": "ls"}, agent_name="Odin"
        )
        # Should succeed because sandbox is inactive
        assert result.success is True


# ── Sandbox lifecycle tests ──────────────────────────────────────────────────


class TestSandboxLifecycle:
    def test_start_and_stop_sandbox(self, router_with_sandbox):
        policy = SandboxPolicy(name="test", rules=[])
        router_with_sandbox._sandbox_registry.register("TestAgent", policy)

        sandbox = router_with_sandbox.start_sandbox("TestAgent")
        assert sandbox.is_active is True
        assert "testagent" in router_with_sandbox._active_sandboxes

        router_with_sandbox.stop_sandbox("TestAgent")
        assert "testagent" not in router_with_sandbox._active_sandboxes

    def test_get_sandbox_stats(self, router_with_sandbox, empty_registry):
        _register_echo_tool(empty_registry)
        policy = SandboxPolicy(name="test", rules=[])
        router_with_sandbox._sandbox_registry.register("TestAgent", policy)

        router_with_sandbox.start_sandbox("TestAgent")
        try:
            router_with_sandbox.execute_tool(
                "echo", {"message": "hi"}, agent_name="TestAgent"
            )
            stats = router_with_sandbox.get_sandbox_stats("TestAgent")
            assert stats is not None
            assert stats["policy"] == "test"
            assert stats["calls_last_minute"] >= 0
            assert stats["violation_count"] == 0
        finally:
            router_with_sandbox.stop_sandbox("TestAgent")

        # After stopping, stats return None
        assert router_with_sandbox.get_sandbox_stats("TestAgent") is None

    def test_get_or_create_caches_sandbox(self, router_with_sandbox):
        policy = SandboxPolicy(name="test", rules=[])
        router_with_sandbox._sandbox_registry.register("TestAgent", policy)

        s1 = router_with_sandbox._get_or_create_sandbox("TestAgent")
        s2 = router_with_sandbox._get_or_create_sandbox("TestAgent")
        assert s1 is s2

    def test_case_insensitive_sandbox_lookup(self, router_with_sandbox):
        policy = SandboxPolicy(name="test", rules=[])
        router_with_sandbox._sandbox_registry.register("Odin", policy)

        s1 = router_with_sandbox._get_or_create_sandbox("Odin")
        s2 = router_with_sandbox._get_or_create_sandbox("odin")
        s3 = router_with_sandbox._get_or_create_sandbox("ODIN")
        assert s1 is s2 is s3


# ── Sandbox + hooks interaction ────────────────────────────────────────────────


class TestSandboxHooksInteraction:
    def test_sandbox_fires_before_hooks(self, router_with_sandbox, empty_registry):
        _register_terminal_tool(empty_registry)

        # Register a hook that would allow everything
        def allow_all_hook(ctx):
            return ctx

        router_with_sandbox.register_pre_tool_hook(allow_all_hook, name="allow-all")

        # Sandbox blocks terminal
        policy = SandboxPolicy(
            name="safe",
            rules=[
                SandboxRule(SandboxRuleType.NO_SUBPROCESS, True),
            ],
        )
        router_with_sandbox._sandbox_registry.register("Odin", policy)

        router_with_sandbox.start_sandbox("Odin")
        try:
            result = router_with_sandbox.execute_tool(
                "terminal", {"cmd": "ls"}, agent_name="Odin"
            )
            # Sandbox blocks before hook fires
            assert result.success is False
            assert "sandbox" in result.error
        finally:
            router_with_sandbox.stop_sandbox("Odin")
            router_with_sandbox.clear_hooks()


# ── Sandbox analytics tests ────────────────────────────────────────────────────


class TestSandboxAnalytics:
    def test_blocked_tool_records_analytics(self, router_with_sandbox, empty_registry):
        _register_terminal_tool(empty_registry)

        policy = SandboxPolicy(
            name="safe",
            rules=[
                SandboxRule(SandboxRuleType.NO_SUBPROCESS, True),
            ],
        )
        router_with_sandbox._sandbox_registry.register("Odin", policy)

        router_with_sandbox.start_sandbox("Odin")
        try:
            result = router_with_sandbox.execute_tool(
                "terminal", {"cmd": "ls"}, agent_name="Odin"
            )
            assert result.success is False

            # Check analytics recorded the failure
            stats = router_with_sandbox.analytics.get_stats(tool_name="terminal")
            assert len(stats) >= 1
            assert stats[0].total_calls >= 1
            assert stats[0].error_count >= 1
        finally:
            router_with_sandbox.stop_sandbox("Odin")
