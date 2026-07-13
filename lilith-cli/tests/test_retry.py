"""Tests for smart retry with exponential backoff in AgentSession.execute_tool.

Verifies that transient errors (timeout, connection, network, 5xx, rate limit)
are retried up to ``retry_count`` extra times with exponential backoff, and
that non-transient errors are not retried.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest


# Ensure the package root is on sys.path
_PKG_ROOT = str(Path(__file__).resolve().parent.parent)
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)


# ── Minimal config + tool stubs ───────────────────────────────────────


class _StubConfig:
    """Minimal stand-in for YggdrasilConfig."""

    def __init__(self, retry_count: int = 2, retry_backoff: float = 0.01) -> None:
        self.model = "test-model"
        self.api_key = ""
        self.base_url = ""
        self.temperature = 0.0
        self.max_tokens = 64
        self.system_prompt = ""
        self.provider = "test"
        self.providers: dict[str, Any] = {}
        self.memory = types.SimpleNamespace(enabled=False, db_path="")
        self.tools = types.SimpleNamespace(
            enabled=True,
            allowed=["always_fail", "recovering", "fatal_error"],
            tool_timeout=5,
            retry_count=retry_count,
            retry_backoff=retry_backoff,
        )


class _StubResult:
    """Mimics lilith_tools.base.ToolResult."""

    def __init__(self, success: bool, data: Any = None, error: str | None = None) -> None:
        self.success = success
        self.data = data
        self.error = error


class _StubRegistry:
    """Stub of lilith_tools.registry.ToolRegistry."""

    _tools: dict[str, type] = {}

    @classmethod
    def get(cls, name: str) -> type | None:
        return cls._tools.get(name)

    @classmethod
    def register(cls, name: str, tool_cls: type) -> None:
        cls._tools[name] = tool_cls

    @classmethod
    def clear(cls) -> None:
        cls._tools.clear()


class _ToolCall:
    """Minimal stand-in for lilith_cli.providers.ToolCall."""

    def __init__(self, id: str, name: str, arguments: dict[str, Any]) -> None:
        self.id = id
        self.name = name
        self.arguments = arguments


def _make_session(retry_count: int = 2, retry_backoff: float = 0.01):
    """Build an AgentSession with a stub registry and retry settings."""
    from lilith_cli.agent import AgentSession
    from lilith_cli.providers import ToolResult

    session = AgentSession.__new__(AgentSession)
    session.config = _StubConfig(retry_count=retry_count, retry_backoff=retry_backoff)
    session.provider = None
    session.history = []
    session.system_prompt = ""
    session._tools_enabled = True
    session._total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    session._last_user_message = ""
    session._memory = None
    session._tool_registry = _StubRegistry()
    session._tools_cache = None
    session._hook_registry = None
    session._session_id = ""
    session._hook_failures = 0
    # Telemetry counters used by /metrics.
    session._tool_call_history: list[dict[str, Any]] = []
    session._command_history: list[dict[str, Any]] = []
    session._file_edit_history: list[dict[str, Any]] = []
    session._init_tools = lambda: None  # type: ignore[assignment]
    return session, ToolResult


# ── Tests ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_retry_happens_for_transient_errors():
    """A tool that always returns a transient error is retried retry_count times."""
    _StubRegistry.clear()

    class _AlwaysFailTool:
        name = "always_fail"
        calls = 0

        def execute(self, **kwargs: Any) -> _StubResult:
            _AlwaysFailTool.calls += 1
            return _StubResult(False, error="Request timeout waiting for response")

    _StubRegistry.register("always_fail", _AlwaysFailTool)
    session, _ = _make_session(retry_count=2, retry_backoff=0.005)

    tc = _ToolCall("1", "always_fail", {})
    res = await session.execute_tool(tc)

    assert _AlwaysFailTool.calls == 3  # initial + 2 retries
    assert "timeout" in res.content.lower()


@pytest.mark.asyncio
async def test_retry_recovers_after_transient_errors():
    """A tool that recovers after transient errors returns the final success result."""
    _StubRegistry.clear()

    class _RecoveringTool:
        name = "recovering"
        calls = 0

        def execute(self, **kwargs: Any) -> _StubResult:
            _RecoveringTool.calls += 1
            if _RecoveringTool.calls < 3:
                return _StubResult(False, error="Connection reset by peer")
            return _StubResult(True, data={"ok": True})

    _StubRegistry.register("recovering", _RecoveringTool)
    session, _ = _make_session(retry_count=2, retry_backoff=0.005)

    tc = _ToolCall("2", "recovering", {})
    res = await session.execute_tool(tc)

    assert _RecoveringTool.calls == 3
    assert "ok" in res.content


@pytest.mark.asyncio
async def test_non_transient_error_is_not_retried():
    """A non-transient error should be returned immediately without retries."""
    _StubRegistry.clear()

    class _FatalErrorTool:
        name = "fatal_error"
        calls = 0

        def execute(self, **kwargs: Any) -> _StubResult:
            _FatalErrorTool.calls += 1
            return _StubResult(False, error="Syntax error in parameters")

    _StubRegistry.register("fatal_error", _FatalErrorTool)
    session, _ = _make_session(retry_count=2, retry_backoff=0.005)

    tc = _ToolCall("3", "fatal_error", {})
    res = await session.execute_tool(tc)

    assert _FatalErrorTool.calls == 1
    assert "Syntax error" in res.content
