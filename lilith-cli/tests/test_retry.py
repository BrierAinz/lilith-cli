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


@pytest.mark.asyncio
async def test_delegation_failure_is_not_retried_and_keeps_reference():
    import json
    from lilith_tools.cli_delegate import VorDelegateTool

    class FailedDelegation(VorDelegateTool):
        calls = 0

        def execute(self, **kwargs):
            type(self).calls += 1
            return _StubResult(False, {"status": "timeout", "reference": "a" * 32,
                                      "effects_unknown": True, "output": "private output"},
                               "timeout observing worker")

    session, _ = _make_session()
    _StubRegistry.register("vor_delegate", FailedDelegation)
    result = await session.execute_tool(_ToolCall("delegate", "vor_delegate", {"task": "review", "timeout": 5}))
    assert FailedDelegation.calls == 1
    payload = json.loads(result.content.removeprefix("Error: "))
    assert payload["recovery"]["reference"] == "a" * 32
    assert payload["recovery"]["effects_unknown"]
    assert "private output" not in result.content


@pytest.mark.asyncio
async def test_delegation_outer_timeout_never_retries(monkeypatch):
    import asyncio
    from lilith_tools.cli_delegate import VorDelegateTool

    observed = []
    async def expire(awaitable, timeout):
        observed.append(timeout)
        awaitable.close()
        raise asyncio.TimeoutError

    session, _ = _make_session()
    _StubRegistry.register("vor_delegate", VorDelegateTool)
    monkeypatch.setattr(asyncio, "wait_for", expire)
    result = await session.execute_tool(_ToolCall("delegate", "vor_delegate", {"task": "review", "timeout": 5}))
    assert observed == [35]
    assert "puede seguir activo" in result.content


def test_both_delegate_tools_disable_retries():
    from lilith_tools.cli_delegate import HuginnDelegateTool, VorDelegateTool
    for cls in (HuginnDelegateTool, VorDelegateTool):
        assert cls.allow_automatic_retry is False
        assert cls.timeout_for_arguments({"timeout": 60}) == 90


@pytest.mark.asyncio
async def test_session_assigns_stable_request_key_to_same_call():
    from lilith_tools.cli_delegate import VorDelegateTool
    seen = []
    class Capture(VorDelegateTool):
        def execute(self, **kwargs):
            seen.append(kwargs["request_id"])
            return _StubResult(True, {"status": "ok"})
    session, _ = _make_session()
    _StubRegistry.register("vor_delegate", Capture)
    first = _ToolCall("same", "vor_delegate", {"task": "review"})
    await session.execute_tool(first)
    await session.execute_tool(_ToolCall("same", "vor_delegate", {"task": "review"}))
    assert seen[0] == seen[1] == first.arguments["request_id"]
    other, _ = _make_session()
    await other.execute_tool(_ToolCall("same", "vor_delegate", {"task": "review"}))
    assert seen[2] != seen[0]


@pytest.mark.asyncio
async def test_compact_delegate_result_keeps_reference_without_false_failure():
    import json
    from lilith_tools.cli_delegate import VorDelegateTool
    class Large(VorDelegateTool):
        def execute(self, **kwargs):
            return _StubResult(True, {"status": "ok", "reference": "d" * 32, "output": "x" * 5000})
    session, _ = _make_session()
    session._tool_result_char_limit = 2000
    _StubRegistry.register("vor_delegate", Large)
    result = await session.execute_tool(_ToolCall("large", "vor_delegate", {"task": "review"}))
    payload = json.loads(result.content)
    assert payload["reference"] == "d" * 32
    assert payload["output_omitted"]
    assert payload["status"] == "ok"
    assert len(result.content) <= 2000


@pytest.mark.asyncio
async def test_restored_session_reuses_journal_reservation(tmp_path, monkeypatch):
    import json
    import types
    import lilith_tools.cli_delegate as delegate
    from lilith_cli import repl
    from lilith_cli.work_session import restore_session

    monkeypatch.setattr(repl, "_CONVERSATIONS_DIR", tmp_path / "conversations")
    monkeypatch.setattr(delegate, "VOR_WRAPPER", tmp_path / "vor.ps1")
    original_exists = delegate.Path.exists
    monkeypatch.setattr(delegate.Path, "exists", lambda path: True if path == delegate.VOR_WRAPPER else original_exists(path))
    launches = []
    def fake_powershell(args, timeout):
        launches.append(args)
        return types.SimpleNamespace(stdout="--- Vor finished (exit 0) ---", stderr="", returncode=0)
    monkeypatch.setattr(delegate, "_powershell", fake_powershell)
    _StubRegistry.register("vor_delegate", delegate.VorDelegateTool)
    first, _ = _make_session()
    first.config.history = types.SimpleNamespace(save=True)
    first._project_root = str(tmp_path)
    first._per_model_usage = {}
    first.history = [{"role": "user", "content": "synthetic review"}]
    initial = await first.execute_tool(_ToolCall("saved-call", "vor_delegate", {"task": "synthetic review"}))
    reference = json.loads(initial.content)["reference"]
    path = repl._auto_save_conversation(first)
    assert path is not None
    restored, _ = _make_session()
    restore_session(restored, repl._load_conversation(path), tmp_path)
    replay = await restored.execute_tool(_ToolCall("saved-call", "vor_delegate", {"task": "synthetic review"}))
    payload = json.loads(replay.content.removeprefix("Error: "))
    assert payload["recovery"]["status"] == "existing_attempt"
    assert payload["recovery"]["reference"] == reference
    assert len(launches) == 1
    rephrased_call = await restored.execute_tool(_ToolCall("new-call-id", "vor_delegate", {"task": "synthetic review"}))
    second_payload = json.loads(rephrased_call.content.removeprefix("Error: "))
    assert second_payload["recovery"]["reference"] == reference
    assert len(launches) == 1


@pytest.mark.asyncio
async def test_delegation_identity_is_on_disk_before_execute(tmp_path, monkeypatch):
    import json
    from lilith_cli import repl
    from lilith_tools.cli_delegate import VorDelegateTool
    monkeypatch.setattr(repl, "_CONVERSATIONS_DIR", tmp_path / "conversations")
    seen = []
    class VerifyCheckpoint(VorDelegateTool):
        def execute(self, **kwargs):
            snapshots = list((tmp_path / "conversations").glob("conv_*.json"))
            assert len(snapshots) == 1
            data = json.loads(snapshots[0].read_text(encoding="utf-8"))
            assert data["delegation_requests"]["entries"][0]["request_id"] == kwargs["request_id"]
            assert len(data["delegation_namespace"]) == 32
            seen.append(True)
            return _StubResult(True, {"status": "ok"})
    current, _ = _make_session()
    current.config.history = types.SimpleNamespace(save=True)
    current._project_root = str(tmp_path)
    current._per_model_usage = {}
    current.history = [{"role": "user", "content": "synthetic"}]
    _StubRegistry.register("vor_delegate", VerifyCheckpoint)
    result = await current.execute_tool(_ToolCall("checkpoint", "vor_delegate", {"task": "synthetic"}))
    assert not result.content.startswith("Error")
    assert seen == [True]


@pytest.mark.asyncio
async def test_failed_identity_checkpoint_prevents_delegation(monkeypatch):
    from lilith_cli import repl
    from lilith_tools.cli_delegate import VorDelegateTool
    class Forbidden(VorDelegateTool):
        calls = 0
        def execute(self, **kwargs):
            type(self).calls += 1
            return _StubResult(True, {})
    current, _ = _make_session()
    current.config.history = types.SimpleNamespace(save=True)
    monkeypatch.setattr(repl, "_auto_save_conversation", lambda session: None)
    _StubRegistry.register("vor_delegate", Forbidden)
    result = await current.execute_tool(_ToolCall("blocked", "vor_delegate", {"task": "synthetic"}))
    assert "no se lanz" in result.content
    assert Forbidden.calls == 0


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
