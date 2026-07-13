"""Tests for lilith_cli.commands."""
import pytest
from lilith_cli.commands import (
    BaseCommand,
    HelpCommand,
    ToolsCommand,
    ModelCommand,
    ProviderCommand,
    MemoryCommand,
    ClearCommand,
    StatusCommand,
    BifrostCommand,
    ConfigCommand,
    QuitCommand,
    SaveCommand,
    RedoCommand,
    CopyCommand,
    SystemCommand,
    HistoryCommand,
    CompactCommand,
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

    def get_plan_progress_str(self):
        return ""

    def get_tool_descriptions(self):
        return []

    def clear_history(self):
        self.history = []

    def enable_tool(self, name: str) -> None:
        self._disabled_tools.discard(name)

    def disable_tool(self, name: str) -> None:
        self._disabled_tools.add(name)


def test_base_command_abstract():
    cmd = BaseCommand(DummySession())
    assert cmd.name == ""
    assert cmd.description == ""
    with pytest.raises(NotImplementedError):
        import asyncio
        asyncio.run(cmd.execute(""))


@pytest.mark.asyncio
async def test_help_command():
    cmd = HelpCommand(DummySession())
    assert cmd.name == "help"
    assert "comandos" in cmd.description.lower()
    # execute prints to console; just ensure it doesn't raise.
    await cmd.execute("")


@pytest.mark.asyncio
async def test_tools_command():
    cmd = ToolsCommand(DummySession())
    assert cmd.name == "tools"
    await cmd.execute("")


@pytest.mark.asyncio
async def test_model_command_show():
    cmd = ModelCommand(DummySession())
    assert cmd.name == "model"
    await cmd.execute("")


@pytest.mark.asyncio
async def test_provider_command_show():
    cmd = ProviderCommand(DummySession())
    assert cmd.name == "provider"
    await cmd.execute("")


@pytest.mark.asyncio
async def test_memory_command_no_memory():
    cmd = MemoryCommand(DummySession())
    assert cmd.name == "memory"
    await cmd.execute("")


@pytest.mark.asyncio
async def test_clear_command():
    sess = DummySession()
    sess.history = [{"role": "user", "content": "hi"}]
    cmd = ClearCommand(sess)
    assert cmd.name == "clear"
    await cmd.execute("")
    assert sess.history == []


@pytest.mark.asyncio
async def test_config_command():
    cmd = ConfigCommand(DummySession())
    assert cmd.name == "config"
    await cmd.execute("")


@pytest.mark.asyncio
async def test_system_command_show():
    cmd = SystemCommand(DummySession())
    assert cmd.name == "system"
    await cmd.execute("")


@pytest.mark.asyncio
async def test_history_command_empty():
    cmd = HistoryCommand(DummySession())
    assert cmd.name == "history"
    await cmd.execute("")


@pytest.mark.asyncio
async def test_compact_command_empty():
    cmd = CompactCommand(DummySession())
    assert cmd.name == "compact"
    await cmd.execute("")


@pytest.mark.asyncio
async def test_redo_command_no_history():
    cmd = RedoCommand(DummySession())
    assert cmd.name == "redo"
    await cmd.execute("")


@pytest.mark.asyncio
async def test_copy_command_no_history():
    from unittest.mock import MagicMock, patch
    mock_repl = MagicMock()
    mock_repl._copy_to_clipboard = MagicMock(return_value=True)
    with patch.dict("sys.modules", {"lilith_cli.repl": mock_repl}):
        from lilith_cli.commands import CopyCommand
        cmd = CopyCommand(DummySession())
        assert cmd.name == "copy"
        await cmd.execute("")



@pytest.mark.asyncio
async def test_status_command():
    cmd = StatusCommand(DummySession())
    assert cmd.name == "status"
    await cmd.execute("")


@pytest.mark.asyncio
async def test_bifrost_command():
    cmd = BifrostCommand(DummySession())
    assert cmd.name == "bifrost"
    await cmd.execute("")


def test_quit_command_raises():
    cmd = QuitCommand(DummySession())
    assert cmd.name == "quit"
    with pytest.raises(SystemExit):
        import asyncio
        asyncio.run(cmd.execute(""))
