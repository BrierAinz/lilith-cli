"""Tests for the /diff-config slash command."""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from rich.console import Console

from lilith_cli.commands import DiffConfigCommand
from lilith_cli.render import YGGDRASIL_THEME


def _render(renderable) -> str:
    """Render a Rich renderable to a plain string for assertion.

    Reuses Lilith's render theme so custom styles (e.g. ``tool.name``,
    ``tool.result``) remain valid in the test render context.
    """
    buf = StringIO()
    c = Console(
        file=buf,
        force_terminal=False,
        width=200,
        theme=YGGDRASIL_THEME,
        legacy_windows=False,
    )
    c.print(renderable)
    return buf.getvalue()


class DummyConfig:
    def __init__(self, model="test-model", provider="test-provider", api_key=None):
        self.model = model
        self.provider = provider
        self.api_key = api_key or "sk-test-key"
        self.system_prompt = "test"
        self.temperature = 0.7
        self.max_tokens = 4096
        self.tools = SimpleNamespace(
            filesystem=True, coding=True, web_search=True, browser=True, system=True
        )
        self.memory = SimpleNamespace(enabled=True, db_path="~/.yggdrasil/memory.db")
        self.history = SimpleNamespace(max_turns=50, save=True)
        self.providers = {}
        self.confirm_write = True
        self.agent_mode = "default"

    def model_dump(self):
        return {
            "model": self.model,
            "provider": self.provider,
            "api_key": self.api_key,
            "system_prompt": self.system_prompt,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "tools": dict(self.tools.__dict__),
            "memory": dict(self.memory.__dict__),
            "history": dict(self.history.__dict__),
            "providers": self.providers,
            "confirm_write": self.confirm_write,
            "agent_mode": self.agent_mode,
        }


class DummySession:
    def __init__(self, config=None):
        self.config = config or DummyConfig()


@pytest.mark.asyncio
async def test_diff_config_shows_all_keys_no_project(tmp_path, monkeypatch):
    """Without project config, /diff-config lists all keys with (no definido)."""
    monkeypatch.chdir(tmp_path)

    global_config = tmp_path / "global_config.yaml"
    global_config.write_text(
        "model: test-model\nprovider: test-provider\napi_key: sk-test\n",
        encoding="utf-8",
    )

    session = DummySession()
    prints = []

    def capture(renderable):
        prints.append(_render(renderable))

    with patch("lilith_cli.commands.CONFIG_FILE", global_config), patch(
        "lilith_cli.config.CONFIG_FILE", global_config
    ), patch(
        "lilith_cli.commands.find_project_config", return_value=None
    ), patch("lilith_cli.commands.console.print", side_effect=capture):
        cmd = DiffConfigCommand(session)
        await cmd.execute("")

    assert prints, "Expected output to be printed"
    rendered = "".join(prints)
    assert "Clave" in rendered
    assert "Global" in rendered
    assert "Proyecto" in rendered
    assert "Efectiva" in rendered
    assert "test-model" in rendered
    assert "(no definido)" in rendered


@pytest.mark.asyncio
async def test_diff_config_only_different_with_override(tmp_path, monkeypatch):
    """With a project override, only-different shows the differing key."""
    monkeypatch.chdir(tmp_path)
    project_dir = tmp_path / ".lilith"
    project_dir.mkdir()
    project_config = project_dir / "config.yaml"
    project_config.write_text("model: project-model\n", encoding="utf-8")

    global_config = tmp_path / "global_config.yaml"
    global_config.write_text(
        "model: global-model\nprovider: global-provider\napi_key: sk-test\n",
        encoding="utf-8",
    )

    session = DummySession(config=DummyConfig(model="project-model"))
    prints = []

    def capture(renderable):
        prints.append(_render(renderable))

    with patch("lilith_cli.commands.CONFIG_FILE", global_config), patch(
        "lilith_cli.config.CONFIG_FILE", global_config
    ), patch("lilith_cli.commands.console.print", side_effect=capture):
        cmd = DiffConfigCommand(session)
        await cmd.execute("only-different")

    assert prints, "Expected output to be printed"
    rendered = prints[0]
    # The model row is included because the effective value differs from global.
    assert "project-model" in rendered
    assert "global-model" in rendered
    # The provider row appears in the table (global-provider value); what
    # only-different guarantees is that the EFFECTIVE value differs from global.
    assert "global-provider" in rendered
