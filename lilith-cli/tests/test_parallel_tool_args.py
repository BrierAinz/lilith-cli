"""Regression: parallel tool calls must each keep their own arguments.

The provider layer accumulates SSE deltas itself and emits every tool
call fully formed. ``process_message_stream`` used to re-accumulate that
list keyed by a nonexistent ``index`` field, collapsing parallel calls
into one slot — names concatenated, and every call but the first
executed with empty arguments.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_pkg_dir = str(Path(__file__).resolve().parent.parent)
if _pkg_dir not in sys.path:
    sys.path.insert(0, _pkg_dir)

from lilith_cli.agent import AgentSession, ToolResult
from lilith_cli.config import YggdrasilConfig


class _ParallelToolProvider:
    """First turn: one chunk with 3 complete parallel tool calls
    (arguments as dict and as JSON string, both real provider shapes).
    Second turn: plain stop."""

    def __init__(self) -> None:
        self.turn = 0
        self.config = MagicMock(model="local-model", temperature=1.0)

    async def stream(self, messages, tools=None, **kwargs):
        self.turn += 1
        if self.turn == 1:
            yield {
                "content": "",
                "finish_reason": "tool_calls",
                "tool_calls": [
                    {"id": "a", "name": "todo_add", "arguments": {"text": "uno"}},
                    {"id": "b", "name": "todo_add", "arguments": {"text": "dos"}},
                    {"id": "c", "name": "todo_add", "arguments": '{"text": "tres"}'},
                ],
            }
        else:
            yield {"content": "listo", "finish_reason": "stop", "tool_calls": None}


@pytest.mark.asyncio
async def test_parallel_tool_calls_keep_their_arguments():
    cfg = YggdrasilConfig(provider="local", model="local-model")
    session = AgentSession(cfg)
    session.provider = _ParallelToolProvider()
    session._tools_enabled = True
    session.get_tool_descriptions = lambda: [
        {"name": "todo_add", "description": "agrega un todo"}
    ]
    session.get_openai_tools = lambda: [
        {"type": "function", "function": {"name": "todo_add"}}
    ]
    session._init_tools = lambda: None

    executed: list[tuple[str, dict]] = []

    async def _fake_execute(tc):
        executed.append((tc.name, tc.arguments))
        return ToolResult(tool_call_id=tc.id, name=tc.name, content="ok")

    session.execute_tool = _fake_execute

    tool_call_events = []
    async for event in session.process_message_stream("agrega tres todos"):
        if event.get("type") == "tool_call":
            tool_call_events.append(event)

    assert executed == [
        ("todo_add", {"text": "uno"}),
        ("todo_add", {"text": "dos"}),
        ("todo_add", {"text": "tres"}),
    ]
    # The REPL-facing events must carry the same per-call arguments.
    assert [e["arguments"] for e in tool_call_events] == [
        {"text": "uno"},
        {"text": "dos"},
        {"text": "tres"},
    ]
