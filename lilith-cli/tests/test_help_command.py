"""Tests for /help slash command (categorized command catalog)."""

from __future__ import annotations

import asyncio


def _run(coro):
    return asyncio.run(coro)


def test_help_default_shows_all_categories(fake_session, capsys):
    """/help renders a Rich Table grouped by category."""
    from lilith_cli.extra_commands import run_help_command

    _run(run_help_command(fake_session, ""))

    out = capsys.readouterr().out
    assert "Comandos de Lilith" in out
    # Should show multiple categories
    assert "Session" in out
    assert "Configuration" in out
    assert "Development" in out
    # Should show commands
    assert "/help" in out
    assert "/quit" in out


def test_help_fits_one_screen(fake_session, capsys):
    """Bare /help teaches discovery without dumping the full catalog."""
    from lilith_cli.extra_commands import run_help_command

    _run(run_help_command(fake_session, ""))

    out = capsys.readouterr().out
    assert len(out.splitlines()) <= 24
    assert "/commands" in out
    assert "/help <familia>" in out


def test_help_filter_by_category(fake_session, capsys):
    """/help <category> filters to that category only."""
    from lilith_cli.extra_commands import run_help_command

    _run(run_help_command(fake_session, "session"))

    out = capsys.readouterr().out
    assert "Session" in out
    # Session commands should appear
    assert "/clear" in out
    assert "/compact" in out
    # Other categories should NOT appear
    assert "Development" not in out
    assert "Utilities" not in out


def test_help_filter_partial_match(fake_session, capsys):
    """/help uses partial match for category names."""
    from lilith_cli.extra_commands import run_help_command

    _run(run_help_command(fake_session, "util"))

    out = capsys.readouterr().out
    assert "Utilities" in out
    assert "/hash" in out


def test_help_unknown_category_shows_error(fake_session, capsys):
    """/help with unknown category renders friendly error."""
    from lilith_cli.extra_commands import run_help_command

    _run(run_help_command(fake_session, "nonexistent_category_xyz"))

    out = capsys.readouterr().out
    assert "Categor\u00eda desconocida" in out or "disponibles" in out.lower()


def test_help_aliases_work(fake_session, capsys):
    """Both 'h' and '?' are registered as aliases for /help."""
    # We just verify the aliases are in the dispatcher (can't easily test REPL)
    # Read the source
    import inspect

    from lilith_cli.repl import run_repl
    src = inspect.getsource(run_repl)
    assert 'cmd_name in ("help", "h", "?")' in src


def test_help_includes_recent_commands(fake_session, capsys):
    """/help catalogs recent additions like /doctor, /env, /compact."""
    from lilith_cli.extra_commands import run_help_command

    _run(run_help_command(fake_session, "all"))

    out = capsys.readouterr().out
    # Recent additions should be present in the complete catalog.
    assert "/doctor" in out
    assert "/env" in out
    assert "/compact" in out
    assert "/uuid" in out
    assert "/hash" in out


def test_help_all_shows_counts(fake_session, capsys):
    """/help all reports command and category counts."""
    from lilith_cli.extra_commands import run_help_command

    _run(run_help_command(fake_session, "all"))

    out = capsys.readouterr().out
    assert "comandos en" in out
    assert "categor\u00edas" in out