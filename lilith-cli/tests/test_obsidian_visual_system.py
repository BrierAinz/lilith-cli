"""Contracts for Lilith's Obsidian Moon visual system."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from lilith_cli.commands import FocusCommand, ThemeCommand
from lilith_cli.render import THEMES, render_welcome, set_theme
from lilith_cli.repl import _SLASH_COMMANDS, build_bottom_toolbar
from lilith_cli.tool_progress import _build_tool_progress_renderable


class _Session:
    def __init__(self) -> None:
        self.config = SimpleNamespace(provider="sakana", model="fugu-ultra")
        self.total_usage = {
            "prompt_tokens": 1_200,
            "completion_tokens": 300,
            "total_tokens": 1_500,
        }
        self.agent_mode = "default"
        self._focus_mode = False

    def get_plan_progress_str(self) -> str:
        return "[Plan: 1/3] visual"


def _plain_toolbar(parts: list[tuple[str, str]]) -> str:
    return "".join(text for _, text in parts)


def test_lilith_native_themes_are_registered_with_semantic_roles() -> None:
    for name in ("obsidian", "blood-moon", "violet-void", "monochrome"):
        theme = THEMES[name]
        assert theme.name == name
        assert theme.prompt_prefix
        for role in (
            "accent",
            "muted",
            "provider",
            "state.observe",
            "state.reason",
            "state.execute",
            "state.verify",
            "surface.title",
        ):
            assert role in theme.theme


def test_obsidian_is_the_primary_lilith_identity() -> None:
    theme = THEMES["obsidian"]
    assert theme.label == "Obsidian Moon"
    assert "L I L I T H" in theme.banner
    assert theme.spinner_label == "OBSERVANDO"
    assert theme.prompt_prefix == "❯"


@pytest.mark.asyncio
async def test_theme_command_preview_is_non_mutating(capsys) -> None:
    session = _Session()
    command = ThemeCommand(session)
    previous = set_theme("obsidian").name

    await command.execute("preview blood-moon")

    assert THEMES[previous].name == "obsidian"
    from lilith_cli.render import get_theme

    assert get_theme().name == "obsidian"
    output = capsys.readouterr().out
    assert "Blood Moon" in output
    assert "OBSERVANDO" in output
    assert "EJECUTANDO" in output


@pytest.mark.asyncio
async def test_focus_command_and_autocomplete_contract(capsys) -> None:
    session = _Session()
    command = FocusCommand(session)

    await command.execute("on")
    assert session._focus_mode is True
    assert "FOCUS ACTIVO" in capsys.readouterr().out

    await command.execute("off")
    assert session._focus_mode is False
    assert "/focus" in _SLASH_COMMANDS
    assert "/zen" in _SLASH_COMMANDS


def test_toolbar_switches_between_telemetry_and_focus() -> None:
    session = _Session()
    normal = _plain_toolbar(
        build_bottom_toolbar(session, multiline=False, live_tokens={"turns": 2})
    )
    assert "LILITH · SAKANA/fugu-ultra" in normal
    assert "CTX" in normal
    assert "$0.0150" in normal
    assert "T2" in normal
    assert "[Plan: 1/3]" in normal

    session._focus_mode = True
    focused = _plain_toolbar(build_bottom_toolbar(session, multiline=False))
    assert "FOCUS" in focused
    assert "CTX" not in focused
    assert "$" not in focused
    assert "Plan" not in focused


def test_welcome_is_lilith_first_not_legacy_yggdrasil(capsys) -> None:
    set_theme("obsidian")
    render_welcome(
        model="fugu-ultra",
        provider="sakana",
        tools_count=42,
        has_memory=True,
        project="Asgard",
    )
    output = capsys.readouterr().out
    assert "L I L I T H" in output
    assert "SAKANA" in output
    assert "fugu-ultra" in output
    assert "42 herramientas" in output
    assert "memoria online" in output
    assert "/focus" in output


def test_tool_progress_uses_activity_tree() -> None:
    panel = _build_tool_progress_renderable(
        ["file_read"],
        [("search_files", 0.038)],
        [("coding", "exit 1")],
        time.perf_counter() - 0.5,
    )
    assert "LILITH · ACTIVIDAD" in str(panel.title)
    assert panel.border_style == THEMES["obsidian"].border_style
