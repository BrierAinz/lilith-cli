"""Tests for MacroCommand.show and MacroCommand.edit.

Audit items 15-16 (deleg_d9685cd6): the macro command had focused tests
for record/play/list/delete but show/edit were uncovered.

These tests use a tmp_path fixture pointing _MACROS_PATH at a private
file so they don't touch the user's real ~/.yggdrasil/macros.json.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from lilith_cli.commands import MacroCommand


class _Cfg:
    model = "t"
    provider = "t"


class _Session:
    config = _Cfg()
    history: list = []


@pytest.fixture
def macros_path(tmp_path, monkeypatch):
    """Redirect macros storage to a temp file for the test."""
    p = tmp_path / "macros.json"
    monkeypatch.setattr("lilith_cli.commands._MACROS_PATH", p)
    return p


def _seed(macros_path: Path, name: str, commands: list[str]) -> None:
    """Write a macro entry to the test's macros file."""
    if macros_path.exists():
        data = json.loads(macros_path.read_text(encoding="utf-8"))
    else:
        data = {}
    data[name] = commands
    macros_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _read(macros_path: Path) -> dict:
    if not macros_path.exists():
        return {}
    return json.loads(macros_path.read_text(encoding="utf-8"))


# ── /macro show ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_macro_show_lists_commands(macros_path, capsys):
    """/macro show <name> prints the recorded commands with line numbers."""
    _seed(macros_path, "deploy", ["/theme nord", "/clear", "/status"])

    cmd = MacroCommand(_Session())
    await cmd.execute("show deploy")

    out = capsys.readouterr().out
    assert "/theme nord" in out
    assert "/clear" in out
    assert "/status" in out
    assert "3 comando(s)" in out


@pytest.mark.asyncio
async def test_macro_show_missing_name_errors(macros_path, capsys):
    """/macro show with no name prints the usage hint."""
    cmd = MacroCommand(_Session())
    await cmd.execute("show")

    out = capsys.readouterr().out
    assert "Uso:" in out


@pytest.mark.asyncio
async def test_macro_show_unknown_macro_errors(macros_path, capsys):
    """/macro show for a non-existent macro renders a clean error."""
    cmd = MacroCommand(_Session())
    await cmd.execute("show fantasma")

    out = capsys.readouterr().out
    assert "fantasma" in out or "no encontrada" in out.lower()


@pytest.mark.asyncio
async def test_macro_show_empty_macro_message(macros_path, capsys):
    """/macro show on a macro with zero commands says so explicitly."""
    _seed(macros_path, "vacia", [])
    cmd = MacroCommand(_Session())
    await cmd.execute("show vacia")

    out = capsys.readouterr().out
    assert "vacía" in out.lower() or "vacia" in out.lower()


# ── /macro edit ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_macro_edit_missing_name_errors(macros_path, capsys):
    """/macro edit with no name prints the usage hint."""
    cmd = MacroCommand(_Session())
    await cmd.execute("edit")

    out = capsys.readouterr().out
    assert "Uso:" in out


@pytest.mark.asyncio
async def test_macro_edit_unknown_macro_errors(macros_path, capsys):
    """/macro edit for a non-existent macro errors without invoking any editor."""
    called = []

    def fake_run(cmd, **kwargs):
        called.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    import os
    old_editor = os.environ.get("EDITOR")
    os.environ["EDITOR"] = "true"  # POSIX command that exits 0 without doing anything
    try:
        cmd = MacroCommand(_Session())
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("subprocess.run", fake_run)
            await cmd.execute("edit fantasma")
        out = capsys.readouterr().out
        assert "fantasma" in out or "no encontrada" in out.lower()
        # No editor invocation when the macro doesn't exist.
        assert called == []
    finally:
        if old_editor is None:
            os.environ.pop("EDITOR", None)
        else:
            os.environ["EDITOR"] = old_editor


@pytest.mark.asyncio
async def test_macro_edit_persists_changes(macros_path, monkeypatch, tmp_path):
    """End-to-end: seed a macro, run /macro edit, verify the editor
    wrote back the modified commands to disk."""
    _seed(macros_path, "deploy", ["/old1", "/old2"])

    # Simulate the editor by replacing the temp file before the
    # subprocess.run() would normally open the editor. We do that by
    # monkeypatching subprocess.run to first wait for the file to
    # exist, then write our edited content, then return success.

    # We need to inject the writeback between the temp file creation
    # and the subprocess.run. Easiest: use a wrapper that writes the
    # file from a callback registered by the test.
    injected_paths: list = []
    edited_content = "# updated header\n/new1\n/new2\n\n# trailing comment\n"

    def fake_run(cmd, **kwargs):
        # Find the temp file in cmd[1].
        injected_paths.append(cmd[1])
        Path(cmd[1]).write_text(edited_content, encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0)

    import os
    old_editor = os.environ.get("EDITOR")
    os.environ["EDITOR"] = "true"
    try:
        cmd = MacroCommand(_Session())
        with monkeypatch.context() as mp:
            mp.setattr("subprocess.run", fake_run)
            await cmd.execute("edit deploy")

        # File was edited by the fake editor and should be read back.
        data = _read(macros_path)
        assert data["deploy"] == ["/new1", "/new2"]
    finally:
        if old_editor is None:
            os.environ.pop("EDITOR", None)
        else:
            os.environ["EDITOR"] = old_editor


@pytest.mark.asyncio
async def test_macro_edit_strips_comments_and_blanks(macros_path, monkeypatch):
    """Comments (lines starting with #) and blank lines must be skipped
    when re-reading the edited file."""
    _seed(macros_path, "test", ["/original"])

    edited = "# this is a comment\n/cmd1\n\n# another comment\n/cmd2\n   \n/cmd3\n"

    def fake_run(cmd, **kwargs):
        Path(cmd[1]).write_text(edited, encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0)

    import os
    old_editor = os.environ.get("EDITOR")
    os.environ["EDITOR"] = "true"
    try:
        cmd = MacroCommand(_Session())
        with monkeypatch.context() as mp:
            mp.setattr("subprocess.run", fake_run)
            await cmd.execute("edit test")

        data = _read(macros_path)
        assert data["test"] == ["/cmd1", "/cmd2", "/cmd3"]
    finally:
        if old_editor is None:
            os.environ.pop("EDITOR", None)
        else:
            os.environ["EDITOR"] = old_editor


@pytest.mark.asyncio
async def test_macro_edit_editor_nonzero_exit_preserves_macro(macros_path, monkeypatch, capsys):
    """When the editor exits non-zero, the macro stays untouched."""
    original = ["/keep", "/these"]
    _seed(macros_path, "preserve", original)

    def fake_run(cmd, **kwargs):
        # Editor modifies the file, but exits with error code.
        Path(cmd[1]).write_text("/changed\n", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 1)  # editor crash

    import os
    old_editor = os.environ.get("EDITOR")
    os.environ["EDITOR"] = "true"
    try:
        cmd = MacroCommand(_Session())
        with monkeypatch.context() as mp:
            mp.setattr("subprocess.run", fake_run)
            await cmd.execute("edit preserve")

        out = capsys.readouterr().out
        assert "sin cambios" in out or "1" in out  # exit code reported
        # The original macro is intact.
        data = _read(macros_path)
        assert data["preserve"] == original
    finally:
        if old_editor is None:
            os.environ.pop("EDITOR", None)
        else:
            os.environ["EDITOR"] = old_editor
