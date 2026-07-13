"""Tests for /alias get subcommand."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def isolated_aliases(tmp_path, monkeypatch):
    """Redirect ALIAS file to a tmp_path so tests don't pollute real config."""
    from lilith_cli import extra_commands as ec
    fake_file = tmp_path / "aliases.json"
    monkeypatch.setattr(ec, "_ALIAS_FILE", fake_file)
    return fake_file


def test_alias_get_existing(fake_session, capsys, isolated_aliases):
    """/alias get <name> shows the target command for an existing alias."""
    from lilith_cli.extra_commands import _save_aliases, run_alias_command

    _save_aliases({"deploy": "/lint-fix . && /release patch"})

    _run(run_alias_command(fake_session, "get deploy"))

    out = capsys.readouterr().out
    assert "/deploy" in out
    assert "/lint-fix" in out
    assert "release patch" in out


def test_alias_get_missing(fake_session, capsys, isolated_aliases):
    """/alias get <unknown> shows error."""
    from lilith_cli.extra_commands import run_alias_command

    _run(run_alias_command(fake_session, "get no-existe"))

    out = capsys.readouterr().out
    assert "no encontrado" in out.lower() or "not found" in out.lower()


def test_alias_get_no_name(fake_session, capsys, isolated_aliases):
    """/alias get with no name shows usage error."""
    from lilith_cli.extra_commands import run_alias_command

    _run(run_alias_command(fake_session, "get"))

    out = capsys.readouterr().out
    assert "Uso:" in out or "uso:" in out.lower()


def test_alias_get_empty_store(fake_session, capsys, isolated_aliases):
    """/alias get <name> on empty store shows 'not found' error (not crash)."""
    from lilith_cli.extra_commands import run_alias_command

    _run(run_alias_command(fake_session, "get whatever"))

    out = capsys.readouterr().out
    assert "no encontrado" in out.lower() or "not found" in out.lower()