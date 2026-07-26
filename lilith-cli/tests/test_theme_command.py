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


def test_set_theme_no_reemplaza_el_objeto_console():
    """Media docena de módulos hacen ``from .render import console``.

    Ese import copia la referencia, así que reasignar la global dejaba a todos
    ellos escribiendo por la Console vieja: el tema no se aplicaba a la mayor
    parte de la salida y, en los tests, el mock del console dejaba de recibir
    los prints (contaminaba suites enteras). El objeto debe mutarse, no
    reemplazarse.
    """
    from lilith_cli import extra_commands
    from lilith_cli import render

    original = render.console
    try:
        set_theme("cyberpunk")
        assert render.console is original, "set_theme no debe reemplazar el console"
        assert extra_commands.console is original, "los importadores quedarían desconectados"
        assert get_theme().name == "cyberpunk"
    finally:
        set_theme("norse")


def test_cambiar_de_tema_varias_veces_no_apila_indefinidamente():
    """Cada cambio saca el tema anterior antes de apilar el nuevo."""
    from lilith_cli import render

    try:
        for nombre in ("cyberpunk", "norse", "cyberpunk", "norse"):
            set_theme(nombre)
        # Un solo tema nuestro sobre el base.
        assert len(render.console._theme_stack._entries) <= 2
    finally:
        set_theme("norse")
