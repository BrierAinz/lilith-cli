"""Tests for the /tools enable/disable command in lilith_cli."""
from __future__ import annotations

import pytest

from lilith_cli.commands import ToolsCommand


class DummyConfig:
    def __init__(self):
        self.model = "test"
        self.provider = "test"
        self.providers = {}
        self.api_key = ""
        self.system_prompt = ""

    def model_dump(self):
        return {
            "model": self.model,
            "provider": self.provider,
            "providers": self.providers,
            "api_key": self.api_key,
        }


class DummyToolRegistry:
    def __init__(self, tools):
        self._tools = tools

    def list_tools(self):
        return self._tools

    def get(self, name):
        return self._tools.get(name)


class DummySession:
    def __init__(self, tool_registry=None):
        self.config = DummyConfig()
        self._tool_registry = tool_registry
        self._tools_cache = None
        self._disabled_tools: set[str] = set()

    def _all_tool_names(self):
        if self._tool_registry is None:
            return set()
        return set(self._tool_registry.list_tools().keys())

    def get_tool_descriptions(self):
        if self._tools_cache is not None:
            return self._tools_cache

        if self._tool_registry is None:
            self._tools_cache = []
            return self._tools_cache

        tools = []
        for name, _desc in self._tool_registry.list_tools().items():
            if name in self._disabled_tools:
                continue
            tools.append({"name": name, "description": _desc, "parameters": {}})
        self._tools_cache = tools
        return tools

    def enable_tool(self, name: str) -> None:
        self._disabled_tools.discard(name)
        self._tools_cache = None

    def disable_tool(self, name: str) -> None:
        self._disabled_tools.add(name)
        self._tools_cache = None


@pytest.mark.asyncio
async def test_tools_command_disable_removes_from_enabled():
    registry = DummyToolRegistry({"file_read": "read files", "system": "system info"})
    session = DummySession(registry)

    cmd = ToolsCommand(session)
    await cmd.execute("disable file_read")

    assert "file_read" in session._disabled_tools
    assert "file_read" not in {t["name"] for t in session.get_tool_descriptions()}


@pytest.mark.asyncio
async def test_tools_command_enable_restores_tool():
    registry = DummyToolRegistry({"file_read": "read files", "system": "system info"})
    session = DummySession(registry)

    session.disable_tool("file_read")
    cmd = ToolsCommand(session)
    await cmd.execute("enable file_read")

    assert "file_read" not in session._disabled_tools
    assert {t["name"] for t in session.get_tool_descriptions()} == {"file_read", "system"}


@pytest.mark.asyncio
async def test_tools_command_list_all_shows_status():
    registry = DummyToolRegistry({"file_read": "read files", "system": "system info"})
    session = DummySession(registry)

    session.disable_tool("file_read")
    cmd = ToolsCommand(session)

    # Should not raise; verify internal state is consistent.
    await cmd.execute("")
    await cmd.execute("enabled")
    await cmd.execute("disabled")

    enabled = {t["name"] for t in session.get_tool_descriptions()}
    assert enabled == {"system"}
