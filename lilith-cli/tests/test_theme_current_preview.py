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
    fake_target = type(
        "T",
        (),
        {
            "name": "cyberpunk",
            "label": "Cyberpunk",
            "prompt_prefix": "▰",
            "border_style": "magenta",
            "description": "Neón y reflejos",
        },
    )()

    set_theme_called = []

    def fake_set_theme(name):
        set_theme_called.append(name)

    with patch("lilith_cli.render.get_theme", return_value=fake_target):
        with patch("lilith_cli.render.set_theme", side_effect=fake_set_theme):
            await run_theme_command(_Session(), "preview cyberpunk")

    out = capsys.readouterr().out
    assert "cyberpunk" in out
    assert "Cyberpunk" in out
    assert "magenta" in out
    assert "Neón y reflejos" in out
    # Critical: the live theme must not have been touched.
    assert set_theme_called == []


@pytest.mark.asyncio
async def test_theme_preview_without_name_errors(capsys):
    """/theme preview (no name) prints the usage hint, doesn't crash."""
    await run_theme_command(_Session(), "preview")

    out = capsys.readouterr().out
    assert "Uso:" in out


@pytest.mark.asyncio
async def test_theme_preview_unknown_theme_errors(capsys):
    """/theme preview <unknown> errors with a list-pointer, not a traceback."""
    from lilith_cli.render import get_theme as real_get_theme

    def fake_get_theme(name):
        raise KeyError(name)

    with patch("lilith_cli.render.get_theme", side_effect=fake_get_theme):
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
