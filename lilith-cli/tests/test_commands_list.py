"""Tests for the /commands slash command."""
import pytest

from lilith_cli.commands import CommandsCommand, CommandRegistry


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

    def _all_tool_names(self):
        return set()

    def get_tool_descriptions(self):
        return []


@pytest.fixture
def registry():
    return CommandRegistry(DummySession())


@pytest.mark.asyncio
async def test_commands_command_lists_grouped(registry, capsys):
    registry.discover()
    cmd = registry.get("commands")
    assert cmd is not None
    assert cmd.name == "commands"
    await cmd.execute("")
    captured = capsys.readouterr()
    assert "Comandos de Yggdrasil" in captured.out
    # Check a few representative categories/commands show up.
    assert "Sesión" in captured.out
    assert "Info" in captured.out
    assert "/tools" in captured.out
    assert "/plan" in captured.out


@pytest.mark.asyncio
async def test_commands_command_filters(registry, capsys):
    registry.discover()
    cmd = registry.get("commands")
    await cmd.execute("plan")
    captured = capsys.readouterr()
    assert "Comandos de Yggdrasil" in captured.out
    assert "/plan" in captured.out
    # Filtered output should not include unrelated commands.
    assert "/model" not in captured.out


@pytest.mark.asyncio
async def test_commands_command_alias(registry):
    registry.discover()
    assert registry.get("cmds") is not None
    assert registry.get("cmds").name == "commands"


@pytest.mark.asyncio
async def test_commands_command_filter_no_match(capsys):
    cmd = CommandsCommand(DummySession())
    await cmd.execute("zzzzz")
    captured = capsys.readouterr()
    assert "Ningún comando coincide" in captured.out
