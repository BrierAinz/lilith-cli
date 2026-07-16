"""Autonomy safeguards: repeated-tool anti-loop and text continuation."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from lilith_cli.agent import AgentSession
from lilith_cli.providers import ToolCall


class FailingTool:
    calls = 0

    def execute(self, **kwargs):
        from lilith_tools.base import ToolResult
        type(self).calls += 1
        return ToolResult(False, None, "boom")


@pytest.mark.asyncio
async def test_third_identical_failed_tool_call_is_blocked(fake_session) -> None:
    FailingTool.calls = 0
    fake_session._tool_registry = SimpleNamespace(
        get=lambda name: FailingTool if name == "bad" else None,
        list_tools=lambda: {"bad": "fails"},
    )
    fake_session._init_tools = lambda: None
    call = ToolCall(id="1", name="bad", arguments={"value": 7})

    first = await fake_session.execute_tool(call)
    second = await fake_session.execute_tool(call)
    third = await fake_session.execute_tool(call)

    assert first.content.startswith("Error:")
    assert second.content.startswith("Error:")
    assert "misma llamada fallo 2 veces" in third.content
    assert FailingTool.calls == 2


class ContinuationProvider:
    def __init__(self):
        self.calls = 0

    async def complete(self, messages, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return {"content": "primera parte ", "finish_reason": "length", "usage": {}, "tool_calls": []}
        return {"content": "segunda parte", "finish_reason": "stop", "usage": {}, "tool_calls": []}


@pytest.mark.asyncio
async def test_length_text_auto_continuation_stitches_two_parts(fake_session) -> None:
    provider = ContinuationProvider()
    session = AgentSession(fake_session.config, provider=provider)
    session.get_tool_descriptions = lambda: []

    result = await session.process_message("cuenta algo")

    assert result == "primera parte segunda parte\n\n[continuación automática: 1]"
    assert provider.calls == 2
    assert session._last_auto_continuations == 1
    assert session.history[-1]["content"] == result
