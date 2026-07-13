"""Tests for the /alias slash command (set, remove, list subcommands).

The `get` subcommand is covered separately in test_alias_get_command.py.
"""

from __future__ import annotations

import asyncio

import pytest


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def isolated_aliases(tmp_path, monkeypatch):
    """Redirect the alias store to a tmp_path so tests do not touch real config."""
    from lilith_cli import extra_commands as ec
    fake_file = tmp_path / "aliases.json"
    monkeypatch.setattr(ec, "_ALIAS_FILE", fake_file)
    return fake_file


def test_alias_list_empty(fake_session, capsys, isolated_aliases):
    """/alias with no args on an empty store prints the empty-state message."""
    from lilith_cli.extra_commands import run_alias_command

    _run(run_alias_command(fake_session, ""))

    out = capsys.readouterr().out
    assert "No hay alias definidos." in out


def test_alias_list_with_aliases(fake_session, capsys, isolated_aliases):
    """/alias list renders each saved alias in name -> target form."""
    from lilith_cli.extra_commands import _save_aliases, run_alias_command

    _save_aliases({"deploy": "/lint-fix . && /release patch"})

    _run(run_alias_command(fake_session, "list"))

    out = capsys.readouterr().out
    assert "/deploy" in out
    assert "/lint-fix . && /release patch" in out


def test_alias_set_saves_and_lists(fake_session, capsys, isolated_aliases):
    """/alias set persists the alias and /alias list shows it."""
    from lilith_cli.extra_commands import _load_aliases, run_alias_command

    _run(run_alias_command(fake_session, "set deploy /lint ."))

    # Persistence check: alias was written to disk.
    stored = _load_aliases()
    assert stored == {"deploy": "/lint ."}

    # Rendering check: list shows the alias we just saved.
    _run(run_alias_command(fake_session, "list"))
    out = capsys.readouterr().out
    assert "/deploy" in out
    assert "/lint ." in out


def test_alias_set_no_command_arg(fake_session, capsys, isolated_aliases):
    """/alias set <name> without a target command prints a usage error and saves nothing."""
    from lilith_cli.extra_commands import _load_aliases, run_alias_command

    _run(run_alias_command(fake_session, "set foo"))

    out = capsys.readouterr().out
    assert "Uso:" in out

    # Nothing should have been persisted.
    assert _load_aliases() == {}


def test_alias_remove_existing(fake_session, capsys, isolated_aliases):
    """/alias remove deletes a previously saved alias."""
    from lilith_cli.extra_commands import _load_aliases, _save_aliases, run_alias_command

    _save_aliases({"deploy": "/lint ."})

    _run(run_alias_command(fake_session, "remove deploy"))

    assert _load_aliases() == {}

    # /alias list should now show the empty state.
    _run(run_alias_command(fake_session, "list"))
    out = capsys.readouterr().out
    assert "No hay alias definidos." in out


def test_alias_remove_missing(fake_session, capsys, isolated_aliases):
    """/alias remove on an unknown alias prints the not-found error."""
    from lilith_cli.extra_commands import run_alias_command

    _run(run_alias_command(fake_session, "remove no-existe"))

    out = capsys.readouterr().out
    assert "Alias no encontrado" in out


def test_alias_remove_no_name(fake_session, capsys, isolated_aliases):
    """/alias remove without a name prints a usage error."""
    from lilith_cli.extra_commands import run_alias_command

    _run(run_alias_command(fake_session, "remove"))

    out = capsys.readouterr().out
    assert "Uso:" in out


def test_alias_unknown_subcommand(fake_session, capsys, isolated_aliases):
    """/alias <unknown> prints the generic usage line.

    Note: the subcommand list brackets are stripped by Rich's markup parser
    because the source passes the string verbatim to ``render_error``,
    so the rendered output is just ``Uso: /alias``.
    """
    from lilith_cli.extra_commands import run_alias_command

    _run(run_alias_command(fake_session, "foo"))

    out = capsys.readouterr().out
    assert "Uso: /alias" in out
