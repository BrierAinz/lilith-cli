"""Tests for the /hooks slash command."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def isolated_hooks(tmp_path: Path, monkeypatch):
    """Redirect lilith_cli.hooks._HOOKS_DIR to a tmp dir for the test.

    The command imports ``_HOOKS_DIR`` from ``.hooks`` inside the function, so
    we patch the symbol on the source module before the import runs.
    """
    import lilith_cli.hooks as hooks_mod

    fake_dir = tmp_path / "hooks"
    fake_dir.mkdir()
    monkeypatch.setattr(hooks_mod, "_HOOKS_DIR", fake_dir)
    return fake_dir


@pytest.mark.asyncio
async def test_hooks_list_empty(fake_session, isolated_hooks, capsys):
    """/hooks with no args on an empty hooks dir must print the empty-state block."""
    from lilith_cli.extra_commands import run_hooks_command

    await run_hooks_command(fake_session, "")

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "Hooks" in combined or "hooks" in combined
    # Each event should appear with the 'none' marker.
    assert "pre-tool-call" in combined
    assert "post-tool-call" in combined


@pytest.mark.asyncio
async def test_hooks_list_with_installed_script(fake_session, isolated_hooks, capsys):
    """/hooks must enumerate installed hook scripts under their event dir."""
    event_dir = isolated_hooks / "pre-tool-call"
    event_dir.mkdir(parents=True, exist_ok=True)
    (event_dir / "lint.py").write_text("# hook\n", encoding="utf-8")

    from lilith_cli.extra_commands import run_hooks_command

    await run_hooks_command(fake_session, "list")

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "lint.py" in combined
    assert "1 script" in combined or "1" in combined


@pytest.mark.asyncio
async def test_hooks_add_copies_script_into_event_dir(fake_session, isolated_hooks, tmp_path):
    """/hooks add <event> <file> must copy the script under <event>/."""
    from lilith_cli.extra_commands import run_hooks_command

    src = tmp_path / "source.py"
    src.write_text("#!/usr/bin/env python\nprint('hi')\n", encoding="utf-8")

    await run_hooks_command(fake_session, f"add pre-tool-call {src}")

    target = isolated_hooks / "pre-tool-call" / "source.py"
    assert target.exists()
    assert target.read_text(encoding="utf-8") == src.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_hooks_add_unknown_event_reports_error(fake_session, isolated_hooks, capsys):
    """/hooks add <unknown-event> <file> must print an error and not touch the dir."""
    from lilith_cli.extra_commands import run_hooks_command

    # Use any existing file path as the script source; the event check happens first.
    src = isolated_hooks / "whatever.py"
    src.write_text("# x\n", encoding="utf-8")

    await run_hooks_command(fake_session, f"add bogus-event {src}")

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "desconocido" in combined.lower() or "unknown" in combined.lower()
    assert not (isolated_hooks / "bogus-event").exists()


@pytest.mark.asyncio
async def test_hooks_add_missing_script_reports_error(fake_session, isolated_hooks, capsys):
    """/hooks add with a non-existent source must print an error and not crash."""
    from lilith_cli.extra_commands import run_hooks_command

    await run_hooks_command(fake_session, "add pre-tool-call /nonexistent/zzz_hook_xyz.py")

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "no existe" in combined.lower() or "no encontr" in combined.lower()
    assert not any(isolated_hooks.glob("pre-tool-call/*"))


@pytest.mark.asyncio
async def test_hooks_remove_deletes_script(fake_session, isolated_hooks):
    """/hooks remove <event> <name> must delete the script from the event dir."""
    from lilith_cli.extra_commands import run_hooks_command

    event_dir = isolated_hooks / "pre-tool-call"
    event_dir.mkdir(parents=True, exist_ok=True)
    target = event_dir / "junk.py"
    target.write_text("# x\n", encoding="utf-8")

    await run_hooks_command(fake_session, "remove pre-tool-call junk.py")

    assert not target.exists()


@pytest.mark.asyncio
async def test_hooks_remove_unknown_event_reports_error(fake_session, isolated_hooks, capsys):
    """/hooks remove with an unknown event must print an error and not crash."""
    from lilith_cli.extra_commands import run_hooks_command

    await run_hooks_command(fake_session, "remove bogus-event anything.py")

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "desconocido" in combined.lower() or "unknown" in combined.lower()


@pytest.mark.asyncio
async def test_hooks_help_prints_event_list(fake_session, isolated_hooks, capsys):
    """/hooks help must print the catalogue of supported events."""
    from lilith_cli.extra_commands import run_hooks_command

    await run_hooks_command(fake_session, "help")

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "pre-tool-call" in combined
    assert "on-error" in combined