"""Tests for the /tour slash command."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from lilith_cli.extra_commands import _TOUR_STEPS, run_tour_command


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


class DummySession:
    def __init__(self):
        self.config = DummyConfig()
        self.memory = None
        self.history = []
        self.provider = None
        self.system_prompt = ""


def _capture_prints():
    """Devuelve (prints, patch_ctx) para capturar console.print."""
    prints = []

    def capture(text: str = ""):
        prints.append(str(text))

    return prints, patch("lilith_cli.extra_commands.console.print", side_effect=capture)


@pytest.mark.asyncio
async def test_tour_runs_all_steps():
    """/tour muestra los 5 pasos del recorrido interactivo."""
    session = DummySession()
    prints, ctx = _capture_prints()
    with ctx:
        await run_tour_command(session, "")

    assert any("Recorrido interactivo" in p for p in prints)
    for title, _ in _TOUR_STEPS:
        assert any(title in p for p in prints)
    assert any("Recorrido completado" in p for p in prints)


@pytest.mark.asyncio
async def test_tour_step_and_skip():
    """/tour step N salta al paso indicado; /tour skip termina el recorrido."""
    session = DummySession()
    prints, ctx = _capture_prints()
    with ctx:
        await run_tour_command(session, "step 2")
        await run_tour_command(session, "skip")

    joined = "\n".join(prints)
    assert "Seguridad: confirm_write y undo" in joined
    assert "Paso 2/5" in joined
    assert "Recorrido cancelado" in joined
    assert "Bienvenido a Lilith" not in joined
    assert "Herramientas principales" not in joined


@pytest.mark.asyncio
async def test_tour_list_shows_titles_only():
    """/tour list imprime los títulos numerados sin el contenido."""
    session = DummySession()
    prints, ctx = _capture_prints()
    with ctx:
        await run_tour_command(session, "list")

    joined = "\n".join(prints)
    # Todos los títulos presentes
    for title, _ in _TOUR_STEPS:
        assert title in joined, f"falta el título {title!r}"
    # Numeración 1..N visible
    for i in range(1, len(_TOUR_STEPS) + 1):
        assert f"{i}." in joined
    # No se volcó contenido (los cuerpos mencionan slash commands específicos)
    assert "/undo deshace" not in joined
    assert "read_file, write_file y patch" not in joined


@pytest.mark.asyncio
async def test_tour_next_advances_one_step():
    """/tour next avanza un paso desde el cursor de la sesión."""
    session = DummySession()
    prints, ctx = _capture_prints()
    with ctx:
        # Empezamos en el paso 1 explícitamente
        await run_tour_command(session, "step 1")
        prints.clear()
        await run_tour_command(session, "next")

    joined = "\n".join(prints)
    assert "Paso 2/5" in joined
    assert "Seguridad: confirm_write y undo" in joined
    # El cursor debe estar persistido en la sesión
    assert getattr(session, "_tour_cursor", None) == 2


@pytest.mark.asyncio
async def test_tour_prev_requires_existing_cursor():
    """/tour prev antes de haber avanzado falla con un error legible."""
    session = DummySession()
    prints, ctx = _capture_prints()
    with ctx:
        await run_tour_command(session, "prev")

    joined = "\n".join(prints)
    # render_error pinta con prefijo propio; basta con que diga "primer paso"
    assert "primer paso" in joined.lower() or "primer paso" in joined
    # El cursor no debería haberse creado
    assert getattr(session, "_tour_cursor", None) is None


@pytest.mark.asyncio
async def test_tour_reset_clears_cursor():
    """/tour reset limpia el cursor y deja un mensaje de confirmación."""
    session = DummySession()
    prints, ctx = _capture_prints()
    with ctx:
        await run_tour_command(session, "step 3")
        assert getattr(session, "_tour_cursor", None) == 3
        prints.clear()
        await run_tour_command(session, "reset")

    joined = "\n".join(prints)
    assert "reiniciado" in joined.lower()
    assert getattr(session, "_tour_cursor", None) is None


@pytest.mark.asyncio
async def test_tour_help_describes_subcommands():
    """/tour help lista los subcomandos disponibles."""
    session = DummySession()
    prints, ctx = _capture_prints()
    with ctx:
        await run_tour_command(session, "help")

    joined = "\n".join(prints)
    for sub in ("list", "step", "next", "prev", "reset", "skip", "help"):
        assert sub in joined, f"falta subcomando {sub!r} en la ayuda"


@pytest.mark.asyncio
async def test_tour_skip_aliases_clear_cursor():
    """/tour cancel y /tour quit son alias válidos que limpian el cursor."""
    session = DummySession()
    prints, ctx = _capture_prints()
    with ctx:
        await run_tour_command(session, "step 2")
        assert getattr(session, "_tour_cursor", None) == 2
        await run_tour_command(session, "cancel")
        await run_tour_command(session, "step 3")
        await run_tour_command(session, "quit")

    # Ambos alias deben limpiar el cursor
    assert getattr(session, "_tour_cursor", None) is None


@pytest.mark.asyncio
async def test_tour_unknown_arg_shows_usage():
    """/tour con un argumento desconocido imprime un error de uso."""
    session = DummySession()
    prints, ctx = _capture_prints()
    with ctx:
        await run_tour_command(session, "foobar")

    joined = "\n".join(prints)
    assert "Uso" in joined or "uso" in joined


@pytest.mark.asyncio
async def test_tour_step_sets_cursor_for_next_prev_chain():
    """/tour step N persiste el cursor y habilita next/prev coherentes."""
    session = DummySession()
    prints, ctx = _capture_prints()
    with ctx:
        await run_tour_command(session, "step 3")
        assert getattr(session, "_tour_cursor", None) == 3
        await run_tour_command(session, "prev")
        assert getattr(session, "_tour_cursor", None) == 2
        await run_tour_command(session, "next")
        assert getattr(session, "_tour_cursor", None) == 3

    joined = "\n".join(prints)
    assert "Paso 2/5" in joined
    assert "Paso 3/5" in joined
