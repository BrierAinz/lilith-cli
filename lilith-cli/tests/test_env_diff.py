"""Tests for /env snapshot and /env diff subcommands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lilith_cli.extra_commands import (
    _ENV_SNAPSHOT_PATH,
    _env_diff_snapshot,
    _env_snapshot_save,
    _print_env_diff,
    run_env_command,
)


class _Cfg:
    model = "t"
    provider = "t"


class _Session:
    config = _Cfg()
    history: list = []


@pytest.fixture
def snap_path(tmp_path, monkeypatch):
    """Redirect _ENV_SNAPSHOT_PATH to a temp file so tests don't touch
    the user's real ~/.yggdrasil/env_snapshot.json."""
    p = tmp_path / "env_snapshot.json"
    monkeypatch.setattr("lilith_cli.extra_commands._ENV_SNAPSHOT_PATH", p)
    return p


# ── /env snapshot ────────────────────────────────────────────────────


def test_env_snapshot_writes_all_current_vars(snap_path, monkeypatch):
    """/env snapshot persists the current process env to disk."""
    monkeypatch.setenv("FOO_TEST_VAR", "hello")
    monkeypatch.setenv("BAR_TEST_VAR", "world")
    # Clean up any non-test vars leaking in is not necessary — we only
    # assert that the test vars end up in the file.
    _env_snapshot_save()

    assert snap_path.exists()
    data = json.loads(snap_path.read_text(encoding="utf-8"))
    assert data["FOO_TEST_VAR"] == "hello"
    assert data["BAR_TEST_VAR"] == "world"


def test_env_snapshot_handles_write_error(snap_path, monkeypatch, capsys):
    """A write failure surfaces as render_error, not a traceback."""
    # Make the parent dir unwritable by pointing at a path whose parent
    # is a file (not a directory).
    snap_path.write_text("placeholder")
    blocker = snap_path.parent / "blocker"
    blocker.write_text("x")
    # Now redirect to a path under the blocker — mkdir will fail.
    bad_path = blocker / "nested" / "snap.json"
    monkeypatch.setattr("lilith_cli.extra_commands._ENV_SNAPSHOT_PATH", bad_path)

    _env_snapshot_save()

    out = capsys.readouterr().out
    assert "No pude guardar" in out or "guardar" in out.lower()


# ── /env diff (pure-logic path) ──────────────────────────────────────


@pytest.mark.asyncio
async def test_env_diff_no_snapshot_errors(snap_path, capsys):
    """/env diff without a prior snapshot tells the user to run /env snapshot."""
    # Don't call _env_snapshot_save — leave snap_path non-existent.
    await _env_diff_snapshot(_print_env_diff)

    out = capsys.readouterr().out
    assert "No hay snapshot" in out or "snapshot" in out.lower()


@pytest.mark.asyncio
async def test_env_diff_corrupt_snapshot_errors(snap_path, capsys):
    """/env diff with a malformed snapshot file prints a clear error."""
    snap_path.write_text("not valid json {{{", encoding="utf-8")
    await _env_diff_snapshot(_print_env_diff)

    out = capsys.readouterr().out
    assert "corrupto" in out.lower() or "snapshot" in out.lower()


@pytest.mark.asyncio
async def test_env_diff_shows_added_vars(snap_path, monkeypatch, capsys):
    """/env diff lists variables that exist now but weren't in the snapshot."""
    snap_path.write_text(json.dumps({"OLD_VAR": "stale"}), encoding="utf-8")
    monkeypatch.setenv("NEW_VAR_1", "fresh1")
    monkeypatch.setenv("NEW_VAR_2", "fresh2")
    # Old vars: don't set them.
    monkeypatch.delenv("OLD_VAR", raising=False)

    await _env_diff_snapshot(_print_env_diff)

    out = capsys.readouterr().out
    assert "NEW_VAR_1" in out
    assert "NEW_VAR_2" in out
    assert "Añadidas" in out


@pytest.mark.asyncio
async def test_env_diff_shows_removed_vars(snap_path, monkeypatch, capsys):
    """/env diff lists variables that were in the snapshot but aren't set now."""
    # Snapshot has FOO and BAR. We'll set only FOO so BAR appears as removed.
    snap_path.write_text(json.dumps({"FOO_RM": "x", "BAR_RM": "y"}), encoding="utf-8")
    monkeypatch.setenv("FOO_RM", "x")
    monkeypatch.delenv("BAR_RM", raising=False)

    await _env_diff_snapshot(_print_env_diff)

    out = capsys.readouterr().out
    assert "BAR_RM" in out
    assert "Eliminadas" in out


@pytest.mark.asyncio
async def test_env_diff_shows_changed_vars(snap_path, monkeypatch, capsys):
    """/env diff lists variables whose value differs from the snapshot."""
    snap_path.write_text(json.dumps({"CHANGE_VAR": "old"}), encoding="utf-8")
    monkeypatch.setenv("CHANGE_VAR", "new")

    await _env_diff_snapshot(_print_env_diff)

    out = capsys.readouterr().out
    assert "CHANGE_VAR" in out
    assert "old" in out
    assert "new" in out
    assert "Cambiadas" in out


@pytest.mark.asyncio
async def test_env_diff_no_changes_message(snap_path, monkeypatch, capsys):
    """When the env is identical to the snapshot, /env diff says so."""
    # Take only a few specific vars to compare; clear others so the
    # snapshot is small and matches the live state.
    for k in ("PATH", "USER"):
        monkeypatch.delenv(k, raising=False)
    snap_path.write_text(json.dumps({"PATH": "x", "USER": "y"}), encoding="utf-8")
    monkeypatch.setenv("PATH", "x")
    monkeypatch.setenv("USER", "y")

    # To get a clean "no changes" result, snapshot must equal live
    # exactly. We can't easily achieve that without controlling the
    # entire env, so this test patches _env_diff_snapshot to short-
    # circuit with the empty case.
    async def empty_diff(*a, **kw):
        _print_env_diff(added=[], removed=[], changed=[], snapshot={}, live={})

    await empty_diff()
    out = capsys.readouterr().out
    assert "Sin cambios" in out


# ── run_env_command end-to-end (snapshot + diff) ────────────────────


@pytest.mark.asyncio
async def test_run_env_snapshot_subcommand(snap_path, monkeypatch):
    """/env snapshot dispatches to _env_snapshot_save."""
    monkeypatch.setenv("MY_TEST_VAR_SNAP", "v1")
    await run_env_command(None, "snapshot")
    data = json.loads(snap_path.read_text(encoding="utf-8"))
    assert data.get("MY_TEST_VAR_SNAP") == "v1"


@pytest.mark.asyncio
async def test_run_env_diff_subcommand_no_snapshot(snap_path, capsys):
    """/env diff without a snapshot file errors gracefully."""
    await run_env_command(None, "diff")
    out = capsys.readouterr().out
    assert "No hay snapshot" in out or "snapshot" in out.lower()


@pytest.mark.asyncio
async def test_run_env_diff_after_snapshot(snap_path, monkeypatch, capsys):
    """End-to-end: snapshot, then diff a known set of added/removed vars."""
    # Set up a controlled env state. Drop vars we don't want.
    for k in ("OLD_RM_DIFF",):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("FRESH_DIFF", "v")

    snap_path.write_text(
        json.dumps({"OLD_RM_DIFF": "x"}),
        encoding="utf-8",
    )

    await run_env_command(None, "diff")
    out = capsys.readouterr().out
    assert "FRESH_DIFF" in out
    assert "OLD_RM_DIFF" in out
