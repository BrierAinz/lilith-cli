"""Tests for the /init slash command and project-instruction injection."""

from __future__ import annotations

import sys
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure lilith_cli is importable when running this file directly
_pkg_dir = str(Path(__file__).resolve().parent.parent)
if _pkg_dir not in sys.path:
    sys.path.insert(0, _pkg_dir)

from lilith_cli.commands import CommandRegistry, InitCommand


class DummySession:
    """Minimal session stand-in for InitCommand tests."""

    def __init__(self):
        self.config = MagicMock()
        self.config.model = "test"
        self.config.provider = "test"
        self.config.providers = {}
        self.config.api_key = ""
        self.memory = None
        self.history = []
        self.system_prompt = ""
        self._disabled_tools: set[str] = set()

    def _all_tool_names(self):
        return set()

    def get_tool_descriptions(self):
        return []

    def clear_history(self):
        self.history = []

    def enable_tool(self, name: str) -> None:
        self._disabled_tools.discard(name)

    def disable_tool(self, name: str) -> None:
        self._disabled_tools.add(name)


class TestInitCommand:
    """Verify /init creates a .lilith/CLAUDE.md template with the correct project type."""

    @pytest.fixture(autouse=True)
    def _isolate_fs(self, tmp_path, monkeypatch):
        """Run each test in a temporary directory and patch os.getcwd."""
        self._tmp = tmp_path
        monkeypatch.chdir(tmp_path)
        yield

    @pytest.mark.asyncio
    async def test_command_metadata(self):
        cmd = InitCommand(DummySession())
        assert cmd.name == "init"
        assert "CLAUDE.md" in cmd.description

    @pytest.mark.asyncio
    async def test_creates_python_template(self):
        (self._tmp / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        cmd = InitCommand(DummySession())
        await cmd.execute("")

        claude_md = self._tmp / ".lilith" / "CLAUDE.md"
        assert claude_md.exists()
        content = claude_md.read_text(encoding="utf-8")
        assert "# Instrucciones del proyecto" in content
        assert "pytest" in content
        assert "Instalar dependencias" in content

    @pytest.mark.asyncio
    async def test_detects_node_project(self):
        (self._tmp / "package.json").write_text("{}", encoding="utf-8")
        cmd = InitCommand(DummySession())
        await cmd.execute("")

        claude_md = self._tmp / ".lilith" / "CLAUDE.md"
        assert claude_md.exists()
        content = claude_md.read_text(encoding="utf-8")
        assert "npm install" in content
        assert "npm test" in content

    @pytest.mark.asyncio
    async def test_refuses_overwrite(self):
        claude_md = self._tmp / ".lilith" / "CLAUDE.md"
        claude_md.parent.mkdir(parents=True, exist_ok=True)
        claude_md.write_text("existente", encoding="utf-8")

        with patch("lilith_cli.commands.render_error") as mock_error:
            cmd = InitCommand(DummySession())
            await cmd.execute("")
            mock_error.assert_called_once()
            assert "Ya existe" in mock_error.call_args[0][0]

        assert claude_md.read_text(encoding="utf-8") == "existente"

    @pytest.mark.asyncio
    async def test_custom_path(self):
        subdir = self._tmp / "subdir"
        subdir.mkdir()
        cmd = InitCommand(DummySession())
        await cmd.execute(str(subdir))

        claude_md = subdir / ".lilith" / "CLAUDE.md"
        assert claude_md.exists()
        assert "subdir" in claude_md.read_text(encoding="utf-8")


class TestProjectInstructionsInjection:
    """Verify .lilith/CLAUDE.md content is injected into the system prompt."""

    def _make_session(self, cwd: Path, instructions: str):
        """Build a minimal AgentSession in a controlled directory."""
        import os as _os
        from lilith_cli.agent import AgentSession
        from lilith_cli.config import YggdrasilConfig

        cfg = YggdrasilConfig(provider="local", model="local-model")
        session = AgentSession.__new__(AgentSession)
        session.config = cfg
        session.system_prompt = cfg.system_prompt
        session.history = []
        session._tools_enabled = False
        session._total_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        session._per_model_usage = {}
        session._last_user_message = ""
        session._cancel_event = None
        session._memory = None
        session._tool_registry = None
        session._tools_cache = []
        session._disabled_tools = set()
        session._hook_registry = None
        session._session_id = ""
        session._hook_failures = 0
        session._project_instructions = None
        session.provider = MagicMock()
        session.provider.stream = AsyncMock(return_value=iter([]))

        # Write project instructions and patch cwd.
        instructions_path = cwd / ".lilith" / "CLAUDE.md"
        instructions_path.parent.mkdir(parents=True, exist_ok=True)
        instructions_path.write_text(instructions, encoding="utf-8")
        self._saved_cwd = _os.getcwd()
        _os.chdir(str(cwd))
        return session

    def teardown_method(self):
        import os as _os
        if hasattr(self, "_saved_cwd"):
            _os.chdir(self._saved_cwd)

    def test_injects_local_instructions(self, tmp_path):
        session = self._make_session(tmp_path, "Usá siempre async/await.")
        messages = session._build_messages()
        assert messages[0]["role"] == "system"
        assert "PROJECT INSTRUCTIONS:" in messages[0]["content"]
        assert "Usá siempre async/await." in messages[0]["content"]

    def test_injects_global_instructions(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        lilith_dir = home / ".lilith"
        lilith_dir.mkdir()
        (lilith_dir / "CLAUDE.md").write_text("Reglas globales.", encoding="utf-8")

        with patch("pathlib.Path.home", return_value=home):
            with patch("pathlib.Path.cwd", return_value=tmp_path / "nowhere"):
                from lilith_cli.agent import AgentSession
                from lilith_cli.config import YggdrasilConfig

                cfg = YggdrasilConfig(provider="local", model="local-model")
                session = AgentSession.__new__(AgentSession)
                session.config = cfg
                session.system_prompt = cfg.system_prompt
                session.history = []
                session._tools_enabled = False
                session._total_usage = {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                }
                session._per_model_usage = {}
                session._last_user_message = ""
                session._cancel_event = None
                session._memory = None
                session._tool_registry = None
                session._tools_cache = []
                session._disabled_tools = set()
                session._hook_registry = None
                session._session_id = ""
                session._hook_failures = 0
                session._project_instructions = None
                session.provider = MagicMock()
                session.provider.stream = AsyncMock(return_value=iter([]))

                messages = session._build_messages()
                assert "PROJECT INSTRUCTIONS:" in messages[0]["content"]
                assert "Reglas globales." in messages[0]["content"]

    def test_no_instructions_when_file_missing(self, tmp_path):
        session = self._make_session(tmp_path, "")
        # Remove the file so the directory exists but the instructions don't.
        (tmp_path / ".lilith" / "CLAUDE.md").unlink()
        messages = session._build_messages()
        assert "PROJECT INSTRUCTIONS:" not in messages[0]["content"]

    def test_registry_has_init_command(self):
        registry = CommandRegistry(DummySession())
        registry.discover()
        assert registry.get("init") is not None
        assert registry.get("init").name == "init"
