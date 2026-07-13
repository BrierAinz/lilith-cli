"""Tests for /tip add and /tip count."""

from __future__ import annotations

import asyncio
from unittest.mock import patch


def _run(coro):
    return asyncio.run(coro)


import pytest


@pytest.fixture
def restore_tips():
    """Snapshot LILITH_TIPS before each test and restore after."""
    from lilith_cli.extra_commands import LILITH_TIPS
    original_len = len(LILITH_TIPS)
    yield
    # Truncate back to original length if test added tips
    while len(LILITH_TIPS) > original_len:
        LILITH_TIPS.pop()


def test_tip_add_appends_to_list(fake_session, capsys, restore_tips):
    """/tip add <text> appends a new tip and reports new count."""
    from lilith_cli.extra_commands import LILITH_TIPS, run_tip_command

    before = len(LILITH_TIPS)
    _run(run_tip_command(fake_session, "add Always check git status before commit"))

    out = capsys.readouterr().out
    # New tip appended
    assert len(LILITH_TIPS) == before + 1
    assert "a\u00f1adido" in out or "added" in out.lower()
    assert "total:" in out.lower() or str(before + 1) in out
    # Last tip is the new one
    assert LILITH_TIPS[-1] == "Always check git status before commit"


def test_tip_add_empty_shows_usage(fake_session, capsys, restore_tips):
    """/tip add with no text shows usage error."""
    from lilith_cli.extra_commands import LILITH_TIPS, run_tip_command

    before = len(LILITH_TIPS)
    _run(run_tip_command(fake_session, "add"))

    out = capsys.readouterr().out
    assert "Uso:" in out
    # No tip added
    assert len(LILITH_TIPS) == before


def test_tip_add_preserves_capitalization(fake_session, capsys, restore_tips):
    """/tip add preserves the original case of the text (not lowercased)."""
    from lilith_cli.extra_commands import LILITH_TIPS, run_tip_command

    _run(run_tip_command(fake_session, "add Use Postman for API Testing"))

    assert "Use Postman for API Testing" in LILITH_TIPS


def test_tip_count_shows_total(fake_session, capsys, restore_tips):
    """/tip count prints the total number of tips."""
    from lilith_cli.extra_commands import LILITH_TIPS, run_tip_command

    _run(run_tip_command(fake_session, "count"))

    out = capsys.readouterr().out
    assert str(len(LILITH_TIPS)) in out
    assert "consejos" in out.lower() or "count" in out.lower()


def test_tip_count_after_add(fake_session, capsys, restore_tips):
    """/tip count reflects added tips."""
    from lilith_cli.extra_commands import LILITH_TIPS, run_tip_command

    before = len(LILITH_TIPS)
    _run(run_tip_command(fake_session, "add Test tip"))
    _run(run_tip_command(fake_session, "count"))

    out = capsys.readouterr().out
    assert str(before + 1) in out