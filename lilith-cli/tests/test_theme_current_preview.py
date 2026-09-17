"""Tests for /theme current and /theme preview subcommands."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from lilith_cli.extra_commands import run_theme_command


class _Cfg:
    model = "t"
    provider = "t"


class _Session:
    config = _Cfg()


# ── /theme current ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_theme_current_shows_active_theme(capsys):
    """/theme current prints the active theme's name and attributes."""
    fake_theme = type(
        "T",
        (),
        {
            "name": "nord",
            "label": "Nord",
            "prompt_prefix": "❄",
            "border_style": "cyan",
            "description": "Frío y silencioso",
        },
    )()
    with patch("lilith_cli.render.get_theme", return_value=fake_theme):
        await run_theme_command(_Session(), "current")

    out = capsys.readouterr().out
    assert "nord" in out
    assert "Nord" in out
    assert "❄" in out
    assert "cyan" in out
    assert "Frío y silencioso" in out


# ── /theme preview ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_theme_preview_renders_sample_panel(capsys):
    """/theme preview <name> shows a sample panel styled with the target
    theme WITHOUT calling set_theme (so the live theme is untouched)."""
    from lilith_cli.render import THEMES, get_theme, set_theme

    # Start from a known state.
    original = get_theme().name
    try:
        set_theme("norse")
        assert get_theme().name == "norse"

        await run_theme_command(_Session(), "preview obsidiana")

        out = capsys.readouterr().out
        assert "obsidiana" in out.lower()
        # La previsualizacion tiene que mostrar el banner DE ESE tema. Se
        # comprueba contra el banner real del objeto y no contra una cadena
        # escrita a mano: aqui habia un literal "O B S I D I A N A" y se rompio
        # en cuanto el banner dejo de llevar el nombre del tema (el producto se
        # llama Lilith; obsidiana es la piel). Comprobar la RELACION en vez del
        # texto sobrevive al siguiente rediseno y sigue detectando lo que
        # importa: que no se pinte el banner equivocado.
        objetivo = [l.strip() for l in THEMES["obsidiana"].banner.splitlines() if l.strip()]
        assert objetivo, "el tema objetivo no tiene banner"
        for linea in objetivo:
            assert linea in out, f"falta esta linea del banner de obsidiana: {linea!r}"
        activo = [l.strip() for l in THEMES["norse"].banner.splitlines() if l.strip()]
        for linea in [l for l in activo if l not in objetivo]:
            assert linea not in out, f"se pinto el banner del tema activo: {linea!r}"
        # Tool line sample.
        assert "file_read" in out
        # Error sample.
        assert "error" in out.lower()
        # Critical: the live theme must NOT have changed.
        assert get_theme().name == "norse"
    finally:
        set_theme(original)


@pytest.mark.asyncio
async def test_theme_preview_without_name_errors(capsys):
    """/theme preview (no name) prints the usage hint, doesn't crash."""
    await run_theme_command(_Session(), "preview")

    out = capsys.readouterr().out
    assert "Uso:" in out


@pytest.mark.asyncio
async def test_theme_preview_unknown_theme_errors(capsys):
    """/theme preview <unknown> errors with a list-pointer, not a traceback."""
    await run_theme_command(_Session(), "preview inexistente")

    out = capsys.readouterr().out
    assert "inexistente" in out or "desconocido" in out.lower()
    assert "/theme list" in out or "disponibles" in out.lower()


# ── backward compat ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_theme_no_args_lists_themes(capsys):
    """/theme (no args) still lists themes — backward compat preserved."""
    fake_themes = [
        type("T", (), {"name": "nord", "description": "Frío"})(),
        type("T", (), {"name": "lilith", "description": "Místico"})(),
    ]
    with patch("lilith_cli.render.list_themes", return_value=fake_themes):
        await run_theme_command(_Session(), "")

    out = capsys.readouterr().out
    assert "nord" in out
    assert "lilith" in out
    assert "Frío" in out
    assert "Místico" in out


@pytest.mark.asyncio
async def test_theme_list_alias_works(capsys):
    """/theme list is an explicit alias for /theme with no args."""
    fake_themes = [type("T", (), {"name": "nord", "description": "Frío"})()]
    with patch("lilith_cli.render.list_themes", return_value=fake_themes):
        await run_theme_command(_Session(), "list")

    out = capsys.readouterr().out
    assert "nord" in out
