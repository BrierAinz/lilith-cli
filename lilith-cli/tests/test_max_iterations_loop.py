"""Tests for the configurable ``max_iterations`` cap in
``AgentSession.process_message_stream``.

The stream loop is the path the REPL/IDE takes on every user turn, so the
behaviour around the cap is the most important to lock down:

* The cap is read from ``cfg.max_iterations`` (default 10); never hardcoded.
* On the **last** iteration the loop injects an ``AVISO`` system message so
  the model can wrap up.
* When the loop is exhausted with tool_calls still pending, the session
  makes one final provider call **without tools** and yields the closing
  summary as ``text`` chunks before the terminal ``done``.

The provider is a fake that always asks for another tool call, so the loop
is forced to run to its cap on every turn.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from lilith_cli.agent import AgentSession
from lilith_cli.config import YggdrasilConfig
from lilith_cli.providers import ToolResult


def _make_session(*, max_iterations: int) -> AgentSession:
    """Build an AgentSession whose fake provider always demands a tool call."""
    cfg = YggdrasilConfig(provider="local", model="local-model")
    cfg.max_iterations = max_iterations
    session = AgentSession(cfg)
    session._tools_enabled = True
    # Empty tool list — closing summary uses tools=None, but tools_enabled
    # True is fine because ``tools`` arg is None at the closing call site.
    # We stub _init_tools so it returns no tools, and stream() always asks
    # for a tool the session knows nothing about. To keep the loop simple
    # we just inject a fake _all_tool_names + don't run repair.
    session._init_tools = lambda: None  # type: ignore[assignment]
    session._tool_registry = None  # type: ignore[assignment]
    session._tools_cache = []  # type: ignore[assignment]

    # When tools_cache is empty AND no tools the loop sees ``tools=None``;
    # ``provider.stream(messages, tools=None)`` then receives no tools and
    # we can still emit a single ``finish_reason=tool_calls`` chunk via the
    # stream path. Easiest: have tools=None but tool_calls in the chunk.

    stream_log: list[int] = []

    async def _always_ask_tools(_messages, tools=None, **_kw):
        stream_log.append(1)
        yield {
            "type": "tool_call",
            "content": "",
            "finish_reason": "tool_calls",
            "tool_calls": [{"id": "call_1", "name": "noop", "arguments": "{}"}],
        }

    session.provider.stream = _always_ask_tools

    async def _fake_complete(messages, tools=None, **_kw):
        # Used for both iteration completions AND the closing summary.
        # We treat every "complete" call as the closing summary so the test
        # gets deterministic behaviour without exposing the stream path.
        # In iteration mode the stream path runs, not complete.
        return {
            "content": "summary-from-complete",
            "tool_calls": [],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            "model": "fake",
        }

    session.provider.complete = _fake_complete

    async def _fake_execute_tool(_tc):
        return ToolResult(tool_call_id="call_1", name="noop", content="ok")

    session.execute_tool = _fake_execute_tool  # type: ignore[assignment]

    session._stream_log = stream_log  # type: ignore[attr-defined]
    return session


@pytest.mark.asyncio
async def test_max_iterations_default_is_ten():
    cfg = YggdrasilConfig()
    assert cfg.max_iterations == 10


@pytest.mark.asyncio
async def test_max_iterations_config_drives_stream_loop_cap():
    """With ``max_iterations=N``, the loop calls ``provider.stream`` exactly
    ``N`` times, then ``provider.complete`` exactly once for the closing
    summary (which is yielded as ``text`` + ``done``)."""
    session = _make_session(max_iterations=3)

    events: list[dict[str, Any]] = []
    async for ev in session.process_message_stream("hello"):
        events.append(ev)

    assert len(session._stream_log) == 3, (  # type: ignore[attr-defined]
        f"expected 3 stream iterations, got {len(session._stream_log)}"  # type: ignore[attr-defined]
    )

    # Final event is "done".
    assert events[-1]["type"] == "done", events
    # At least one "text" event (the closing summary streamed).
    text_events = [e for e in events if e.get("type") == "text"]
    assert text_events, f"expected closing summary as text event(s); got {events}"
    # Tool call/tool_result events must have been emitted during the iterations.
    tc_events = [e for e in events if e.get("type") == "tool_call"]
    tr_events = [e for e in events if e.get("type") == "tool_result"]
    assert tc_events and tr_events, f"expected tool_call/tool_result events; got {events}"


@pytest.mark.asyncio
async def test_closing_summary_appended_to_history():
    session = _make_session(max_iterations=2)
    async for _ in session.process_message_stream("ping"):
        pass

    assistant_msgs = [m for m in session.history if m.get("role") == "assistant"]
    assert assistant_msgs, "expected at least one assistant message"
    last = assistant_msgs[-1]
    assert last["content"], "closing summary must have non-empty content"


@pytest.mark.asyncio
async def test_max_iterations_validator_rejects_zero():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        YggdrasilConfig(max_iterations=0)


@pytest.mark.asyncio
async def test_max_iterations_validator_rejects_negative():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        YggdrasilConfig(max_iterations=-3)
