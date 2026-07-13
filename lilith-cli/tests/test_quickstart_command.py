"""Tests for the /quickstart command."""
import pytest

from lilith_cli.commands import (
    BaseCommand,
    CommandRegistry,
    HelpCommand,
    QuickstartCommand,
)


class DummyConfig:
    def __init__(self):
        self.model = "test"
        self.provider = "test"
        self.providers = {}
        self.api_key = ""

    def model_dump(self):
        return {
            "model": self.model,
            "provider": self.provider,
            "providers": self.providers,
            "api_key": self.api_key,
        }


class DummySession:
    def __init__(self):
        self.config = DummyConfig()
        self.memory = None
        self.history = []
        self.provider = None
        self.system_prompt = ""
        self._disabled_tools: set[str] = set()

    def _all_tool_names(self):
        return set()

    def get_tool_descriptions(self):
        return []

    def get_plan_progress_str(self):
        return ""


@pytest.mark.asyncio
async def test_quickstart_basic_command():
    cmd = QuickstartCommand(DummySession())
    assert cmd.name == "quickstart"
    assert "tour" in cmd.description.lower() or "nuevos" in cmd.description.lower()
    # execute prints to console; just ensure it doesn't raise.
    await cmd.execute("")


@pytest.mark.asyncio
async def test_quickstart_full_command():
    cmd = QuickstartCommand(DummySession())
    assert "qs" in cmd.aliases or "start" in cmd.aliases
    await cmd.execute("full")


@pytest.mark.asyncio
async def test_quickstart_registered_in_registry():
    registry = CommandRegistry(DummySession())
    registry.discover()
    cmd = registry.get("quickstart")
    assert cmd is not None
    assert isinstance(cmd, QuickstartCommand)
    assert registry.get("qs") == cmd
    assert registry.get("start") == cmd
