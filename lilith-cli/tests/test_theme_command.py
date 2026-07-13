"""Tests for the /theme slash command."""
import pytest
from lilith_cli.commands import ThemeCommand
from lilith_cli.render import get_theme, set_theme


class _DummyConfig:
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


class _DummySession:
    def __init__(self):
        self.config = _DummyConfig()
        self.memory = None
        self.history = []
        self.provider = None


@pytest.mark.asyncio
async def test_theme_command_list(capsys):
    """/theme (and /theme list) prints all available themes."""
    cmd = ThemeCommand(_DummySession())
    assert cmd.name == "theme"

    # Default /theme behavior should list themes.
    await cmd.execute("")
    out = capsys.readouterr().out
    assert "Temas Disponibles" in out
    for name in ("norse", "cyberpunk", "minimal"):
        assert name in out


@pytest.mark.asyncio
async def test_theme_command_current_and_switch(capsys):
    """/theme current shows the active theme; switching changes it."""
    # Start from a known state.
    set_theme("norse")
    cmd = ThemeCommand(_DummySession())

    await cmd.execute("current")
    out = capsys.readouterr().out
    assert "Norse" in out
    assert "norse" in out

    await cmd.execute("cyberpunk")
    assert get_theme().name == "cyberpunk"

    # Restore default for subsequent tests.
    set_theme("norse")
