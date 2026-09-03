from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from lilith_cli.agent import AgentSession
from lilith_cli.agent_modes import (
    get_agent_mode,
    mode_allows_tool,
    tool_capability,
)
from lilith_cli.config import YggdrasilConfig
from lilith_cli.main import _apply_overrides
from lilith_cli.providers import ToolCall
from lilith_tools.base import BaseTool, ToolResult as BackendToolResult


class ReadTool(BaseTool):
    name = "file_read"
    description = "read"
    parameters = {}
    calls = 0

    def execute(self, **kwargs):
        type(self).calls += 1
        return BackendToolResult(success=True, data="read")


class MutatingTool(BaseTool):
    name = "file_append"
    description = "append"
    parameters = {}
    calls = 0

    def execute(self, **kwargs):
        type(self).calls += 1
        return BackendToolResult(success=True, data="mutated")


class FakeRegistry:
    tools = {ReadTool.name: ReadTool, MutatingTool.name: MutatingTool}

    @classmethod
    def list_tools(cls):
        return {name: tool.description for name, tool in cls.tools.items()}

    @classmethod
    def get(cls, name):
        return cls.tools.get(name)


def make_session(*, mode: str = "default", tools_enabled: bool = True) -> AgentSession:
    cfg = YggdrasilConfig(
        provider="local",
        model="local-model",
        agent_mode=mode,
        tools_enabled=tools_enabled,
    )
    session = AgentSession(cfg, provider=MagicMock())
    session._tool_registry = FakeRegistry
    session._tools_cache = None
    session._init_tools = lambda: None
    return session


def test_no_tools_override_is_terminal() -> None:
    cfg = YggdrasilConfig(provider="local", model="local-model")
    _apply_overrides(cfg, no_tools=True)
    session = AgentSession(cfg, provider=MagicMock())
    session._tool_registry = FakeRegistry
    session._init_tools = lambda: None
    assert session._tools_enabled is False
    assert session.get_tool_descriptions() == []


@pytest.mark.asyncio
async def test_no_tools_denies_direct_execution_before_instantiation() -> None:
    MutatingTool.calls = 0
    session = make_session(tools_enabled=False)
    result = await session.execute_tool(
        ToolCall(id="disabled", name="file_append", arguments={})
    )
    assert "--no-tools" in result.content
    assert MutatingTool.calls == 0


def test_configured_review_only_mode_is_applied_at_bootstrap() -> None:
    session = make_session(mode="review-only")
    assert session.agent_mode == "review-only"
    assert session._agent_allow_writes is False
    assert session.config.confirm_write is True
    assert {tool["name"] for tool in session.get_tool_descriptions()} == {"file_read"}


def test_unknown_agent_mode_fails_closed_at_bootstrap() -> None:
    cfg = YggdrasilConfig(
        provider="local",
        model="local-model",
        agent_mode="invented-mode",
    )
    with pytest.raises(ValueError, match="Unknown agent_mode"):
        AgentSession(cfg, provider=MagicMock())


@pytest.mark.asyncio
async def test_review_only_denies_mutation_before_instantiation() -> None:
    MutatingTool.calls = 0
    session = make_session(mode="review-only")
    result = await session.execute_tool(
        ToolCall(id="mutating", name="file_append", arguments={})
    )
    assert "deniega la capacidad mutante" in result.content
    assert MutatingTool.calls == 0


@pytest.mark.asyncio
async def test_review_only_allows_declared_read_capability() -> None:
    ReadTool.calls = 0
    session = make_session(mode="review-only")
    result = await session.execute_tool(
        ToolCall(id="read", name="file_read", arguments={})
    )
    assert result.content == "read"
    assert ReadTool.calls == 1


def test_capability_policy_is_fail_closed_for_new_tools() -> None:
    review_only = get_agent_mode("review-only")
    assert review_only is not None
    assert tool_capability("file_read") == "read"
    assert tool_capability("new_dynamic_mcp_tool") == "mutate"
    assert mode_allows_tool(review_only, "file_read") is True
    assert mode_allows_tool(review_only, "new_dynamic_mcp_tool") is False
