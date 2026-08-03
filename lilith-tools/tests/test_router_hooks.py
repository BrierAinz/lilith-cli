"""Tests for SmartToolRouter hook integration (pre/post tool call gating)."""

from __future__ import annotations

from typing import Any

import pytest

from lilith_core.hooks import HookContext, HookType, get_hook_registry
from lilith_tools.base import BaseTool, ToolResult
from lilith_tools.registry import ToolRegistry
from lilith_tools.router.router import SmartToolRouter


# ── Mock tools ────────────────────────────────────────────────────────────────


class EchoTool(BaseTool):
    """Echo tool for testing."""
    name = "echo"
    description = "Echoes back the input message"
    parameters = {"message": {"type": "string", "required": True}}

    def execute(self, message: str = "", **kwargs: Any) -> ToolResult:
        if not message:
            return ToolResult(success=False, data=None, error="Empty message")
        return ToolResult(success=True, data={"echo": message})


class DangerousTool(BaseTool):
    """Tool that simulates a dangerous operation."""
    name = "dangerous"
    description = "A potentially dangerous tool"
    parameters = {"cmd": {"type": "string", "required": True}}

    def execute(self, cmd: str = "", **kwargs: Any) -> ToolResult:
        return ToolResult(success=True, data={"executed": cmd})


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def registry():
    """Registry with echo + dangerous tool classes."""
    ToolRegistry._tools.clear()
    ToolRegistry._tools["echo"] = EchoTool
    ToolRegistry._tools["dangerous"] = DangerousTool
    return ToolRegistry


@pytest.fixture
def router(registry):
    """SmartToolRouter with clean hook registry."""
    r = SmartToolRouter(registry)
    # Clean global hooks before each test
    get_hook_registry().clear()
    return r


# ── Pre-tool hook tests ───────────────────────────────────────────────────────


class TestPreToolHooks:
    """Tests for pre_tool_call hooks — gate, approve, rewrite."""

    def test_approve_passes_through(self, router):
        """Hook that passes through should not affect execution."""
        def approve_hook(ctx: HookContext) -> HookContext:
            return ctx

        router.register_pre_tool_hook(approve_hook, name="approve")
        result = router.execute_tool("echo", {"message": "hello"})
        assert result.success
        assert result.data["echo"] == "hello"

    def test_gate_blocks_execution(self, router):
        """Hook that returns None should gate (block) the tool."""
        def gate_hook(ctx: HookContext) -> None:
            return None

        router.register_pre_tool_hook(gate_hook, name="blocker")
        result = router.execute_tool("echo", {"message": "hello"})
        assert not result.success
        assert "gated" in result.error

    def test_rewrite_params(self, router):
        """Hook that modifies params should use the modified values."""
        def rewrite_hook(ctx: HookContext) -> HookContext:
            ctx.data["params"]["message"] = "rewritten!"
            return ctx

        router.register_pre_tool_hook(rewrite_hook, name="rewriter")
        result = router.execute_tool("echo", {"message": "original"})
        assert result.success
        assert result.data["echo"] == "rewritten!"

    def test_conditional_gate(self, router):
        """Hook that gates only for specific tools."""
        def block_dangerous(ctx: HookContext) -> HookContext | None:
            if ctx.data.get("tool_name") == "dangerous":
                return None  # Gate it
            return ctx

        router.register_pre_tool_hook(block_dangerous, name="block_dangerous")

        # echo should pass
        result = router.execute_tool("echo", {"message": "safe"})
        assert result.success

        # dangerous should be gated
        result = router.execute_tool("dangerous", {"cmd": "ls"})
        assert not result.success
        assert "gated" in result.error

    def test_priority_order(self, router):
        """Hooks should fire in priority order."""
        order = []

        def hook_a(ctx: HookContext) -> HookContext:
            order.append("a")
            return ctx

        def hook_b(ctx: HookContext) -> HookContext:
            order.append("b")
            return ctx

        router.register_pre_tool_hook(hook_a, name="a", priority=10)
        router.register_pre_tool_hook(hook_b, name="b", priority=1)
        router.execute_tool("echo", {"message": "test"})
        assert order == ["b", "a"]


# ── Post-tool hook tests ──────────────────────────────────────────────────────


class TestPostToolHooks:
    """Tests for post_tool_call hooks — modify, suppress."""

    def test_modify_result(self, router):
        """Post hook can modify the ToolResult."""
        def modify_hook(ctx: HookContext) -> HookContext:
            result = ctx.data.get("result")
            if result and result.success:
                result.data["modified"] = True
            return ctx

        router.register_post_tool_hook(modify_hook, name="modifier")
        result = router.execute_tool("echo", {"message": "hello"})
        assert result.success
        assert result.data.get("modified") is True

    def test_suppress_result(self, router):
        """Post hook that returns None suppresses the result."""
        def suppress_hook(ctx: HookContext) -> None:
            return None

        router.register_post_tool_hook(suppress_hook, name="suppressor")
        result = router.execute_tool("echo", {"message": "hello"})
        assert not result.success
        assert "suppressed" in result.error

    def test_no_post_hook_normal_execution(self, router):
        """Without post hooks, execution should be normal."""
        result = router.execute_tool("echo", {"message": "clean"})
        assert result.success
        assert result.data["echo"] == "clean"


# ── Combined hook tests ───────────────────────────────────────────────────────


class TestCombinedHooks:
    """Tests for pre + post hooks working together."""

    def test_pre_gate_skips_execution_and_post(self, router):
        """If pre hook gates, the tool should not execute and post hooks should not fire."""
        post_fired = []

        def gate(ctx: HookContext) -> None:
            return None

        def post_hook(ctx: HookContext) -> HookContext:
            post_fired.append("yes")
            return ctx

        router.register_pre_tool_hook(gate, name="gate")
        router.register_post_tool_hook(post_hook, name="post")

        result = router.execute_tool("echo", {"message": "test"})
        assert not result.success
        assert post_fired == []  # Post hook should not have fired

    def test_pre_rewrite_then_post_modify(self, router):
        """Pre rewrites params, post modifies result — both should work."""
        def pre_rewrite(ctx: HookContext) -> HookContext:
            ctx.data["params"]["message"] = "intercepted"
            return ctx

        def post_tag(ctx: HookContext) -> HookContext:
            result = ctx.data.get("result")
            if result and result.success:
                result.data["tag"] = "audited"
            return ctx

        router.register_pre_tool_hook(pre_rewrite, name="rewriter")
        router.register_post_tool_hook(post_tag, name="tagger")

        result = router.execute_tool("echo", {"message": "original"})
        assert result.success
        assert result.data["echo"] == "intercepted"
        assert result.data["tag"] == "audited"


# ── Hook management tests ─────────────────────────────────────────────────────


class TestHookManagement:
    """Tests for router hook management methods."""

    def test_register_pre_tool_hook(self, router):
        def hook(ctx: HookContext) -> HookContext:
            return ctx

        router.register_pre_tool_hook(hook, name="test_pre")
        assert len(router._hooks.hooks_for(HookType.PRE_TOOL_CALL)) == 1

    def test_register_post_tool_hook(self, router):
        def hook(ctx: HookContext) -> HookContext:
            return ctx

        router.register_post_tool_hook(hook, name="test_post")
        assert len(router._hooks.hooks_for(HookType.POST_TOOL_CALL)) == 1

    def test_clear_hooks(self, router):
        def hook(ctx: HookContext) -> HookContext:
            return ctx

        router.register_pre_tool_hook(hook, name="a")
        router.register_post_tool_hook(hook, name="b")
        router.clear_hooks()
        assert len(router._hooks.hooks_for(HookType.PRE_TOOL_CALL)) == 0
        assert len(router._hooks.hooks_for(HookType.POST_TOOL_CALL)) == 0


# ── Heimdall integration pattern test ─────────────────────────────────────────


class TestHeimdallPattern:
    """Test the Heimdall auditor pattern as a pre-tool hook."""

    def test_heimdall_gate_blocks_dangerous_command(self, router):
        """Simulate Heimdall blocking a dangerous command before execution."""
        import re

        DANGEROUS = re.compile(r"rm\s+-rf\s+/")

        def heimdall_gate(ctx: HookContext) -> HookContext | None:
            params = ctx.data.get("params", {})
            cmd = params.get("cmd", "")
            if DANGEROUS.search(cmd):
                return None  # Gate it
            return ctx

        router.register_pre_tool_hook(heimdall_gate, name="heimdall")

        # Safe command passes
        result = router.execute_tool("dangerous", {"cmd": "ls -la"})
        assert result.success

        # Dangerous command is gated
        result = router.execute_tool("dangerous", {"cmd": "rm -rf /"})
        assert not result.success
        assert "gated" in result.error
