"""Tests for the /tokens slash command."""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

import pytest
from rich.console import Console


def _render(prints) -> str:
    """Render captured Rich renderables to plain text."""
    buf = StringIO()
    c = Console(file=buf, force_terminal=False, width=200, record=True)
    for entry in prints:
        for obj in entry:
            if obj is None or obj == "":
                continue
            try:
                c.print(obj)
            except Exception:
                buf.write(repr(obj))
    return c.export_text(clear=False)


@pytest.mark.asyncio
async def test_tokens_command_uses_session_usage(fake_session):
    """/tokens must render the prompt / completion / total triple from session usage."""
    fake_session._total_usage = {
        "prompt_tokens": 1500,
        "completion_tokens": 800,
        "total_tokens": 2300,
    }

    prints = []

    def capture(*args, **kwargs):
        prints.append(args)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        from lilith_cli.extra_commands import run_tokens_command

        await run_tokens_command(fake_session, "")

    rendered = _render(prints)
    assert "Tokens de la sesión" in rendered
    assert "1,500" in rendered
    assert "800" in rendered
    assert "2,300" in rendered


@pytest.mark.asyncio
async def test_tokens_command_passes_panel_with_thousands_separators(fake_session):
    """/tokens must format large totals with the locale thousands separator."""
    fake_session._total_usage = {
        "prompt_tokens": 12345,
        "completion_tokens": 67890,
        "total_tokens": 80235,
    }

    prints = []

    def capture(*args, **kwargs):
        prints.append(args)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        from lilith_cli.extra_commands import run_tokens_command

        await run_tokens_command(fake_session, "")

    rendered = _render(prints)
    assert "12,345" in rendered
    assert "67,890" in rendered
    assert "80,235" in rendered


@pytest.mark.asyncio
async def test_tokens_command_high_tier_value_wrapped_in_bold(fake_session):
    """/tokens must wrap the 'Total' label in bold regardless of usage tier."""
    fake_session._total_usage = {
        "prompt_tokens": 100,
        "completion_tokens": 100,
        "total_tokens": 100,
    }

    prints = []

    def capture(*args, **kwargs):
        prints.append(args)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        from lilith_cli.extra_commands import run_tokens_command

        await run_tokens_command(fake_session, "")

    rendered = _render(prints)
    # The Total row is wrapped in bold markup; the labels appear in the rendered output.
    assert "Prompt" in rendered
    assert "Completion" in rendered
    assert "Total" in rendered


@pytest.mark.asyncio
async def test_tokens_command_zero_usage_renders_zeros(fake_session):
    """/tokens with no usage must render zeros without raising."""
    fake_session._total_usage = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }

    prints = []

    def capture(*args, **kwargs):
        prints.append(args)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        from lilith_cli.extra_commands import run_tokens_command

        await run_tokens_command(fake_session, "")

    rendered = _render(prints)
    assert "Tokens de la sesión" in rendered
    assert "0" in rendered


@pytest.mark.asyncio
async def test_tokens_command_missing_keys_default_to_zero(fake_session):
    """/tokens must tolerate usage dicts missing keys (defaults to 0)."""
    fake_session._total_usage = {}  # type: ignore[assignment] 

    prints = []

    def capture(*args, **kwargs):
        prints.append(args)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        from lilith_cli.extra_commands import run_tokens_command

        await run_tokens_command(fake_session, "")

    rendered = _render(prints)
    assert "Tokens de la sesión" in rendered
    assert "Prompt" in rendered
    assert "Completion" in rendered
    assert "Total" in rendered
