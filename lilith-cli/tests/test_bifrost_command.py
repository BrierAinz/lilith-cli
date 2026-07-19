"""Tests for the BifrostCommand error paths."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lilith_cli.commands import BifrostCommand


class _DummyConfig:
    model = "test"
    provider = "test"
    providers: dict = {}
    api_key = ""
    system_prompt = ""


class _DummySession:
    config = _DummyConfig()
    memory = None
    history: list = []
    provider = None
    system_prompt = ""


@pytest.mark.asyncio
async def test_bifrost_outside_monorepo_prints_warning(capsys):
    """When _resolve_yggdrasil_root raises, BifrostCommand should explain
    that the command only works inside the Yggdrasil monorepo, not crash
    or print a generic traceback."""
    cmd = BifrostCommand(_DummySession())
    assert cmd.name == "bifrost"

    with patch(
        "lilith_cli.main._resolve_yggdrasil_root",
        side_effect=RuntimeError("not in a yggdrasil checkout"),
    ):
        await cmd.execute("")

    out = capsys.readouterr().out
    assert "monorepo" in out.lower()
    # The RuntimeError message may wrap across lines in the rendered
    # output, so check substring-by-substring.
    assert "not in a yggdrasil" in out


@pytest.mark.asyncio
async def test_bifrost_root_none_prints_warning(capsys):
    """When _resolve_yggdrasil_root returns None (cwd has no Ygg root),
    BifrostCommand should say so with the actual cwd."""
    cmd = BifrostCommand(_DummySession())

    with patch("lilith_cli.main._resolve_yggdrasil_root", return_value=None):
        await cmd.execute("")

    out = capsys.readouterr().out
    assert "monorepo" in out.lower()
    assert str(Path.cwd()) in out


@pytest.mark.asyncio
async def test_bifrost_submodule_missing_prints_warning(capsys, tmp_path):
    """When the bifrost submodule isn't checked out, the warning names
    the missing path so the user knows what to clone."""
    fake_root = tmp_path  # exists, but has no Vanaheim/bifrost inside
    cmd = BifrostCommand(_DummySession())

    with patch("lilith_cli.main._resolve_yggdrasil_root", return_value=fake_root):
        await cmd.execute("")

    out = capsys.readouterr().out
    assert "bifrost" in out.lower()
    assert "submódulo" in out or "no encontrado" in out


@pytest.mark.asyncio
async def test_bifrost_command_aliases():
    """The aliases should match what /help advertises."""
    cmd = BifrostCommand(_DummySession())
    assert "bifrost" in cmd.aliases
    assert "ipc" in cmd.aliases
