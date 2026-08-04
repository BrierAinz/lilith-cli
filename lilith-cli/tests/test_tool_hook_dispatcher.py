"""Focused tests for the tool-hook runtime boundary."""

from __future__ import annotations

from lilith_cli.providers import ToolResult
from lilith_cli.tool_hook_dispatcher import ToolHookDispatcher
from lilith_core.hooks import HookRegistry, HookType


def test_pre_hook_builds_context_and_rewrites_params() -> None:
    registry = HookRegistry()
    observed = {}

    def rewrite(context):
        observed["context"] = context
        context.data["params"] = {"value": "rewritten"}
        return context

    registry.register(HookType.PRE_TOOL_CALL, rewrite, name="rewrite")
    dispatcher = ToolHookDispatcher(registry, session_id="session-1")

    allowed, params = dispatcher.fire_pre(
        "echo",
        {"value": "original"},
        agent_name="test-model",
    )

    assert allowed is True
    assert params == {"value": "rewritten"}
    assert observed["context"].agent_name == "test-model"
    assert observed["context"].session_id == "session-1"


def test_pre_hook_can_gate_execution() -> None:
    registry = HookRegistry()
    registry.register(HookType.PRE_TOOL_CALL, lambda _context: None, name="gate")
    dispatcher = ToolHookDispatcher(registry)

    assert dispatcher.fire_pre("dangerous", {"x": 1}, agent_name="agent") == (
        False,
        {"x": 1},
    )


def test_post_hook_can_rewrite_and_suppress_results() -> None:
    rewrite_registry = HookRegistry()

    def rewrite(context):
        context.data["result"] = "rewritten"
        return context

    rewrite_registry.register(HookType.POST_TOOL_CALL, rewrite, name="rewrite")
    dispatcher = ToolHookDispatcher(rewrite_registry)
    assert (
        dispatcher.fire_post("echo", {}, "original", agent_name="agent")
        == "rewritten"
    )

    suppress_registry = HookRegistry()
    suppress_registry.register(
        HookType.POST_TOOL_CALL,
        lambda _context: None,
        name="suppress",
    )
    dispatcher.attach(suppress_registry, session_id="session-2")
    suppressed = dispatcher.fire_post("echo", {}, "original", agent_name="agent")
    assert isinstance(suppressed, ToolResult)
    assert "suppressed by post_tool_call hook" in suppressed.content


def test_hook_exception_is_counted_and_fails_open() -> None:
    registry = HookRegistry()

    def fail(_context):
        raise RuntimeError("broken hook")

    registry.register(HookType.PRE_TOOL_CALL, fail, name="fail")
    dispatcher = ToolHookDispatcher(registry)

    allowed, params = dispatcher.fire_pre("echo", {"x": 1}, agent_name="agent")

    assert allowed is True
    assert params == {"x": 1}
    assert dispatcher.failures == 1


def test_missing_registry_is_a_noop() -> None:
    dispatcher = ToolHookDispatcher()
    result = ToolResult(tool_call_id="1", name="echo", content="ok")

    assert dispatcher.fire_pre("echo", {"x": 1}, agent_name="agent") == (
        True,
        {"x": 1},
    )
    assert dispatcher.fire_post("echo", {}, result, agent_name="agent") is result
