"""Tests for AgentSession.execute_tool pre/post hook integration.

Inspired by Talon's tool-gating hooks and SmartToolRouter's pre/post
hook firing. These tests verify that the CLI's tool execution path:

- Fires pre_tool_call hooks (gating + param rewrite)
- Fires post_tool_call hooks (result rewrite + suppression)
- Resiliently degrades when hooks raise exceptions
- Is no-op when no HookRegistry is attached
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from typing import Any

import pytest


# Ensure the package root is on sys.path
_PKG_ROOT = str(Path(__file__).resolve().parent.parent)
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)


# ── Minimal config + tool stub set-up ──────────────────────────────────


class _StubConfig:
    """Minimal stand-in for YggdrasilConfig — execute_tool only touches
    `model` and `memory.enabled`, plus we want to avoid loading providers.
    """

    def __init__(self) -> None:
        self.model = "test-model"
        self.api_key = ""
        self.base_url = ""
        self.temperature = 0.0
        self.max_tokens = 64
        self.system_prompt = ""
        self.provider = "test"
        self.providers: dict[str, Any] = {}
        # Memory
        self.memory = types.SimpleNamespace(enabled=False, db_path="")
        # Tools
        self.tools = types.SimpleNamespace(
            enabled=True,
            allowed=["echo", "dangerous"],
            timeout=5,
        )


class _EchoResult:
    """Mimics lilith_tools.base.ToolResult — used by the stub tool."""

    def __init__(self, success: bool, data: Any = None, error: str | None = None) -> None:
        self.success = success
        self.data = data
        self.error = error


class _EchoTool:
    name = "echo"
    description = "Echoes back the input message"
    parameters = {"message": {"type": "string", "required": True}}

    def execute(self, message: str = "", **kwargs: Any) -> _EchoResult:
        if not message:
            return _EchoResult(False, None, "Empty message")
        return _EchoResult(True, {"echo": message})


class _DangerousTool:
    """Tool that records its actual call args for assertion."""

    name = "dangerous"
    description = "Records the cmd it was given"
    parameters = {"cmd": {"type": "string", "required": True}}
    last_cmd: str | None = None

    def execute(self, cmd: str = "", **kwargs: Any) -> _EchoResult:
        _DangerousTool.last_cmd = cmd
        return _EchoResult(True, {"executed": cmd})


class _StubRegistry:
    """Stub of lilith_tools.registry.ToolRegistry — only ``get`` is needed."""

    _tools: dict[str, type] = {}

    @classmethod
    def get(cls, name: str) -> type | None:
        return cls._tools.get(name)


_StubRegistry._tools["echo"] = _EchoTool
_StubRegistry._tools["dangerous"] = _DangerousTool


class _ToolCall:
    """Minimal stand-in for lilith_cli.providers.ToolCall."""

    def __init__(self, id: str, name: str, arguments: dict[str, Any]) -> None:
        self.id = id
        self.name = name
        self.arguments = arguments


def _make_session(monkeypatch=None):
    """Build an AgentSession with stub tool registry — bypasses provider init."""
    from lilith_cli.agent import AgentSession
    from lilith_cli.providers import ToolResult

    # Use __new__ + manual attribute set so we skip __init__'s provider creation
    session = AgentSession.__new__(AgentSession)
    session.config = _StubConfig()
    session.provider = None  # not exercised in execute_tool
    session.history = []
    session.system_prompt = ""
    session._tools_enabled = True
    session._total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    session._last_user_message = ""
    session._memory = None
    session._tool_registry = _StubRegistry()
    session._tools_cache = None
    # Hook state
    session._hook_registry = None
    session._session_id = ""
    session._hook_failures = 0

    # Telemetry counters used by /metrics.
    session._tool_call_history: list[dict[str, Any]] = []
    session._command_history: list[dict[str, Any]] = []
    session._file_edit_history: list[dict[str, Any]] = []

    # execute_tool calls self._init_tools() which imports lilith_tools submodules
    # and overwrites self._tool_registry with the real one (or None on import
    # failure). For our tests we don't want that — patch _init_tools to a no-op
    # so our stub registry stays in place.
    session._init_tools = lambda: None  # type: ignore[assignment]
    return session, ToolResult


# ── Hook stubs ──────────────────────────────────────────────────────────


class _HookRecord:
    """Record of one hook invocation for assertions."""

    def __init__(self, hook_type: str) -> None:
        self.hook_type = hook_type
        self.call_count = 0
        self.last_ctx: Any = None

    def __call__(self, ctx: Any) -> Any:
        self.call_count += 1
        self.last_ctx = ctx
        return ctx


def _make_hook_registry():
    """Build a real HookRegistry from lilith_core (not a stub)."""
    from lilith_core.hooks import HookRegistry, HookType
    reg = HookRegistry()
    return reg, HookType


# ── Tests ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_hooks_attached_is_noop():
    """execute_tool must work normally when no hook registry is set."""
    session, _ = _make_session()
    tc = _ToolCall("1", "echo", {"message": "hi"})
    res = await session.execute_tool(tc)
    assert "hi" in res.content


@pytest.mark.asyncio
async def test_pre_tool_call_hook_can_gate_execution():
    """A pre_tool_call hook returning None must block the tool call."""
    session, _ = _make_session()
    reg, HookType = _make_hook_registry()

    def gate(ctx):
        # Returning None aborts the chain — tool must be skipped
        return None

    reg.register(HookType.PRE_TOOL_CALL, gate, name="gate")
    session.attach_hooks(reg, session_id="sess-1")

    tc = _ToolCall("42", "echo", {"message": "should-not-run"})
    res = await session.execute_tool(tc)
    assert "gated by pre_tool_call hook" in res.content
    assert "echo" in res.content
    # Hook was registered for the right type
    pre_hooks = reg._hooks[HookType.PRE_TOOL_CALL]
    assert any(h.name == "gate" for h in pre_hooks)


@pytest.mark.asyncio
async def test_pre_tool_call_hook_can_rewrite_args():
    """A pre_tool_call hook can rewrite tool args via ctx.data['params']."""
    session, _ = _make_session()
    _DangerousTool.last_cmd = None
    reg, HookType = _make_hook_registry()

    def rewrite(ctx):
        # Mutate the params in the data dict
        ctx.data["params"]["cmd"] = "REWRITTEN"
        return ctx

    reg.register(HookType.PRE_TOOL_CALL, rewrite, name="rewriter")
    session.attach_hooks(reg, session_id="sess-2")

    tc = _ToolCall("7", "dangerous", {"cmd": "original"})
    res = await session.execute_tool(tc)
    assert _DangerousTool.last_cmd == "REWRITTEN"


@pytest.mark.asyncio
async def test_post_tool_call_hook_can_rewrite_result():
    """A post_tool_call hook can rewrite the final ToolResult content."""
    session, _ = _make_session()
    reg, HookType = _make_hook_registry()

    def rewrite(ctx):
        # Build a new ToolResult and stash in data
        from lilith_cli.providers import ToolResult as TR
        new = TR(
            tool_call_id=ctx.data["result"].tool_call_id,
            name=ctx.data["tool_name"],
            content="REWRITTEN_BY_HOOK",
        )
        ctx.data["result"] = new
        return ctx

    reg.register(HookType.POST_TOOL_CALL, rewrite, name="post-rewriter")
    session.attach_hooks(reg, session_id="sess-3")

    tc = _ToolCall("9", "echo", {"message": "hello"})
    res = await session.execute_tool(tc)
    assert res.content == "REWRITTEN_BY_HOOK"


@pytest.mark.asyncio
async def test_post_tool_call_hook_can_suppress_result():
    """A post_tool_call hook returning None must replace the result with an error."""
    session, _ = _make_session()
    reg, HookType = _make_hook_registry()

    def suppress(ctx):
        return None  # Suppress: tool_result is replaced with error

    reg.register(HookType.POST_TOOL_CALL, suppress, name="suppressor")
    session.attach_hooks(reg, session_id="sess-4")

    tc = _ToolCall("11", "echo", {"message": "x"})
    res = await session.execute_tool(tc)
    assert "suppressed by post_tool_call hook" in res.content


@pytest.mark.asyncio
async def test_pre_and_post_hooks_fire_in_order():
    """Both pre and post hooks should fire on a single tool call."""
    session, _ = _make_session()
    reg, HookType = _make_hook_registry()
    pre_rec = _HookRecord("pre")
    post_rec = _HookRecord("post")
    reg.register(HookType.PRE_TOOL_CALL, pre_rec, name="pre")
    reg.register(HookType.POST_TOOL_CALL, post_rec, name="post")
    session.attach_hooks(reg, session_id="sess-5")

    tc = _ToolCall("13", "echo", {"message": "z"})
    await session.execute_tool(tc)
    assert pre_rec.call_count == 1
    assert post_rec.call_count == 1


@pytest.mark.asyncio
async def test_hook_exception_does_not_crash_tool_call():
    """A raising hook must be swallowed and the tool call should still work."""
    session, _ = _make_session()
    reg, HookType = _make_hook_registry()

    def boom(ctx):
        raise RuntimeError("hook went boom")

    reg.register(HookType.PRE_TOOL_CALL, boom, name="boom")
    session.attach_hooks(reg, session_id="sess-6")

    tc = _ToolCall("15", "echo", {"message": "survive"})
    res = await session.execute_tool(tc)
    # Tool should still execute (hook failure is logged + counted, not fatal)
    assert session._hook_failures >= 1
    assert "survive" in res.content


@pytest.mark.asyncio
async def test_attach_hooks_with_none_disables_hooks():
    """Passing None to attach_hooks must clear the registry."""
    session, _ = _make_session()
    reg, HookType = _make_hook_registry()
    rec = _HookRecord("pre")
    reg.register(HookType.PRE_TOOL_CALL, rec, name="rec")
    session.attach_hooks(reg, session_id="sess-7")

    # First call — hook fires
    tc1 = _ToolCall("a", "echo", {"message": "1"})
    await session.execute_tool(tc1)
    assert rec.call_count == 1

    # Detach
    session.attach_hooks(None, session_id="sess-7")
    tc2 = _ToolCall("b", "echo", {"message": "2"})
    await session.execute_tool(tc2)
    # No new hook calls
    assert rec.call_count == 1


@pytest.mark.asyncio
async def test_pre_hook_can_validate_and_block_dangerous_calls():
    """Realistic scenario: a policy hook blocks a `dangerous` tool call."""
    session, _ = _make_session()
    _DangerousTool.last_cmd = None
    reg, HookType = _make_hook_registry()
    BLOCKED_TOOLS = {"dangerous"}

    def policy(ctx):
        if ctx.data["tool_name"] in BLOCKED_TOOLS:
            return None  # Abort
        return ctx

    reg.register(HookType.PRE_TOOL_CALL, policy, name="policy", priority=10)
    session.attach_hooks(reg, session_id="sess-policy")

    # dangerous — blocked
    tc_bad = _ToolCall("d1", "dangerous", {"cmd": "rm -rf /"})
    res_bad = await session.execute_tool(tc_bad)
    assert "gated" in res_bad.content
    assert _DangerousTool.last_cmd is None  # Never executed

    # echo — allowed
    tc_ok = _ToolCall("e1", "echo", {"message": "ok"})
    res_ok = await session.execute_tool(tc_ok)
    assert "ok" in res_ok.content
