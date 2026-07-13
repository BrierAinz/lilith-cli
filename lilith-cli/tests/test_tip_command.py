"""Tests for /tip slash command."""

from __future__ import annotations

import io
from unittest.mock import patch

import pytest
import rich

from lilith_cli.extra_commands import LILITH_TIPS, run_tip_command


class DummySession:
    def __init__(self):
        self.history = []


@pytest.fixture(autouse=True)
def _rich_console(tmp_path, monkeypatch):
    """Redirect Rich console output to a StringIO for rendered assertions."""
    buf = io.StringIO()
    rich.reconfigure(file=buf, force_terminal=False, width=120)
    yield buf
    rich.reconfigure(file=None)


@pytest.mark.asyncio
async def test_tip_random_shows_one_tip(_rich_console):
    """/tip sin argumentos muestra un consejo aleatorio."""
    session = DummySession()

    with patch("lilith_cli.extra_commands.console.print", side_effect=rich.get_console().print):
        await run_tip_command(session, "")

    output = _rich_console.getvalue()
    # Header has ᛭ Consejo (current format)
    assert "Consejo" in output
    # Body should contain one of the 10 tips
    assert any(tip in output for tip in LILITH_TIPS)


@pytest.mark.asyncio
async def test_tip_specific_number(_rich_console):
    """/tip <n> muestra el consejo número n."""
    session = DummySession()

    with patch("lilith_cli.extra_commands.console.print", side_effect=rich.get_console().print):
        await run_tip_command(session, "3")

    output = _rich_console.getvalue()
    # 3rd tip (index 2) appears
    assert LILITH_TIPS[2] in output


@pytest.mark.asyncio
async def test_tip_list_shows_all_tips(_rich_console):
    """/tip list muestra todos los consejos numerados."""
    session = DummySession()

    with patch("lilith_cli.extra_commands.console.print", side_effect=rich.get_console().print):
        await run_tip_command(session, "list")

    output = _rich_console.getvalue()
    # Header for list view
    assert "Consejos disponibles" in output
    # All 10 tips are listed
    for tip in LILITH_TIPS:
        assert tip in output


@pytest.mark.asyncio
async def test_tip_invalid_number_shows_error():
    """/tip con un número fuera de rango muestra un error."""
    session = DummySession()
    prints = []

    def capture(text: str = "") -> None:
        prints.append(str(text))

    with patch("lilith_cli.extra_commands.render_error", side_effect=capture):
        await run_tip_command(session, "999")

    output = "\n".join(prints)
    assert "Índice fuera de rango" in output


@pytest.mark.asyncio
async def test_tip_non_numeric_shows_usage():
    """/tip con texto no numérico muestra la ayuda de uso."""
    session = DummySession()
    prints = []

    def capture(text: str = "") -> None:
        prints.append(str(text))

    with patch("lilith_cli.extra_commands.render_error", side_effect=capture):
        await run_tip_command(session, "hola")

    output = "\n".join(prints)
    assert "Uso: /tip" in output