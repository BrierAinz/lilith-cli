"""Tests for /format slash command.

The /format command wraps ``lilith_tools.coding_tools.FormatFileTool`` but
adds two safety rails on top of it:

1. An **explicit-path** policy: ``.``, ``./`` and absolute paths are rejected
   so the slash command can never sweep the working tree by accident.
2. A **--check** default mode: the user has to opt-in to mutating the file,
   either by passing ``--check`` (read-only audit) or ``--yes`` (apply).

These tests cover the safety rails with mocks — the actual ``FormatFileTool``
and ``_detect_formatter`` calls are patched at the import site because the
slash command imports them lazily inside the function.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from unittest.mock import patch

import pytest


def _run(coro):
    return asyncio.run(coro)


# ── /format safety: path & flag parsing ────────────────────────────────


def test_format_rejects_dot_target(fake_session, capsys, tmp_path, monkeypatch):
    """``/format .`` must be rejected before any formatter runs."""
    from lilith_cli.extra_commands import run_format_command

    monkeypatch.chdir(tmp_path)
    with patch("lilith_cli.extra_commands.subprocess.run") as run, patch(
        "lilith_cli.extra_commands.shutil.which"
    ) as which:
        _run(run_format_command(fake_session, "."))
    out = capsys.readouterr().out.lower()
    assert "explícita" in out
    run.assert_not_called()
    which.assert_not_called()


def test_format_rejects_absolute_path(fake_session, capsys, tmp_path, monkeypatch):
    """An absolute path is rejected even when the file exists."""
    from lilith_cli.extra_commands import run_format_command

    target = tmp_path / "outside.py"
    target.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    with patch("lilith_cli.extra_commands.subprocess.run") as run:
        _run(run_format_command(fake_session, str(target)))
    out = capsys.readouterr().out.lower()
    assert "explícita" in out or "absoluta" in out
    run.assert_not_called()


def test_format_rejects_out_of_tree_relative(fake_session, capsys, tmp_path, monkeypatch):
    """A relative path that resolves outside the cwd must be rejected."""
    from lilith_cli.extra_commands import run_format_command

    monkeypatch.chdir(tmp_path)
    # Sibling directory of tmp_path; the slash command must refuse to audit it.
    with patch("lilith_cli.extra_commands.subprocess.run") as run:
        _run(run_format_command(fake_session, "../outside.py"))
    out = capsys.readouterr().out.lower()
    assert "dentro del repositorio" in out or "explícita" in out
    run.assert_not_called()


def test_format_missing_path_reports_error(fake_session, capsys, tmp_path, monkeypatch):
    """A non-existent explicit path is reported and no subprocess runs."""
    from lilith_cli.extra_commands import run_format_command

    monkeypatch.chdir(tmp_path)
    with patch("lilith_cli.extra_commands.subprocess.run") as run:
        _run(run_format_command(fake_session, "does_not_exist.py"))
    out = capsys.readouterr().out.lower()
    assert "no encontrada" in out or "ruta no encontrada" in out
    run.assert_not_called()


def test_format_no_args_prints_usage(fake_session, capsys):
    """``/format`` with no args prints usage and runs no formatter."""
    from lilith_cli.extra_commands import run_format_command

    with patch("lilith_cli.extra_commands.subprocess.run") as run:
        _run(run_format_command(fake_session, ""))
    out = capsys.readouterr().out.lower()
    assert "uso" in out
    assert "--check" in out
    run.assert_not_called()


def test_format_help_subcommand_prints_summary(fake_session, capsys):
    """``/format --help`` prints the short summary and exits."""
    from lilith_cli.extra_commands import run_format_command

    with patch("lilith_cli.extra_commands.subprocess.run") as run:
        _run(run_format_command(fake_session, "--help"))
    out = capsys.readouterr().out.lower()
    assert "/format" in out
    assert "--check" in out
    run.assert_not_called()


# ── /format --check (report-only path) ────────────────────────────────


def test_format_check_runs_ruff_check_without_writing(
    fake_session, capsys, tmp_path, monkeypatch
):
    """``/format --check`` must invoke ``ruff check`` (or ``black --check``)
    and never call the application path of ``FormatFileTool``."""
    from lilith_cli import extra_commands as ec
    from lilith_cli.extra_commands import run_format_command

    target = tmp_path / "module.py"
    target.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    fake_proc = type(
        "P", (), {"stdout": "All checks passed!", "stderr": "", "returncode": 0}
    )()

    # Patch at the source module — run_format_command imports
    # _detect_formatter lazily from lilith_tools.coding_tools inside the
    # function, so a patch on lilith_cli.extra_commands.* would never fire.
    import lilith_tools.coding_tools as coding

    with patch.object(ec, "FormatFileTool") as format_tool, patch.object(
        coding, "_detect_formatter", return_value="ruff check"
    ), patch(
        "lilith_cli.extra_commands.subprocess.run", return_value=fake_proc
    ) as run:
        _run(run_format_command(fake_session, f"{target.name} --check"))

    # The application path must NOT have been called.
    format_tool.assert_not_called()
    assert run.call_count == 1
    cmd = run.call_args.args[0]
    assert "ruff" in cmd.lower()
    assert "--check" in cmd
    out = capsys.readouterr().out.lower()
    # Rich may soft-wrap the long success line; collapse whitespace before
    # the substring assertion so the test isn't fragile to terminal width.
    out_flat = " ".join(out.split())
    assert "auditor" in out or "reporte" in out or "reporte completado" in out
    assert "no se modificaron archivos" in out_flat


def test_format_check_reports_no_formatter(
    fake_session, capsys, tmp_path, monkeypatch
):
    """When no formatter is available, --check prints a clear error and
    does not invoke the application path."""
    from lilith_cli import extra_commands as ec
    from lilith_cli.extra_commands import run_format_command

    target = tmp_path / "module.py"
    target.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    import lilith_tools.coding_tools as coding

    with patch.object(ec, "FormatFileTool") as format_tool, patch.object(
        coding, "_detect_formatter", return_value=None
    ), patch("lilith_cli.extra_commands.subprocess.run") as run:
        _run(run_format_command(fake_session, f"{target.name} --check"))

    format_tool.assert_not_called()
    run.assert_not_called()
    out = capsys.readouterr().out.lower()
    assert "formatter" in out or "instalado" in out


# ── /format application path ──────────────────────────────────────────


def test_format_apply_requires_confirm_when_confirm_write_on(
    fake_session, capsys, tmp_path, monkeypatch
):
    """When confirm_write is on (default) the slash command must prompt
    the user. We simulate a "no" answer and verify the application path
    is not called."""
    from lilith_cli import extra_commands as ec
    from lilith_cli.extra_commands import run_format_command

    target = tmp_path / "module.py"
    target.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    fake_session.config.confirm_write = True

    with patch.object(ec, "FormatFileTool") as format_tool, patch(
        "builtins.input", return_value="n"
    ):
        _run(run_format_command(fake_session, target.name))

    format_tool.assert_not_called()
    out = capsys.readouterr().out.lower()
    assert "cancelado" in out or "vas a formatear" in out


def test_format_apply_with_yes_delegates_to_format_file_tool(
    fake_session, capsys, tmp_path, monkeypatch
):
    """``/format <path> --yes`` must call ``FormatFileTool().execute``
    with the resolved path and the timeout we pass."""
    from lilith_cli import extra_commands as ec
    from lilith_cli.extra_commands import run_format_command
    from lilith_tools.base import ToolResult

    target = tmp_path / "module.py"
    target.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    fake_session.config.confirm_write = True

    captured: dict = {}

    class _FakeFormat:
        def execute(self, **kwargs):
            captured.update(kwargs)
            return ToolResult(
                success=True,
                data={
                    "path": str(target.resolve()),
                    "formatted": True,
                    "command": "ruff format " + str(target.resolve()),
                    "stdout": "",
                    "stderr": "",
                    "returncode": 0,
                },
            )

    with patch.object(ec, "FormatFileTool", _FakeFormat):
        _run(run_format_command(fake_session, f"{target.name} --yes"))

    assert captured.get("path") == str(target.resolve())
    assert captured.get("timeout") == 60
    out = capsys.readouterr().out
    assert "formateado" in out.lower()
    assert "undo" in out.lower()  # hint about /undo pop


def test_format_apply_propagates_tool_error(
    fake_session, capsys, tmp_path, monkeypatch
):
    """If FormatFileTool returns success=False, /format must surface
    the error and not claim success."""
    from lilith_cli import extra_commands as ec
    from lilith_cli.extra_commands import run_format_command
    from lilith_tools.base import ToolResult

    target = tmp_path / "module.py"
    target.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    fake_session.config.confirm_write = True

    class _FakeFormat:
        def execute(self, **kwargs):
            return ToolResult(
                success=False,
                data=None,
                error="ruff no está instalado",
            )

    with patch.object(ec, "FormatFileTool", _FakeFormat):
        _run(run_format_command(fake_session, f"{target.name} --yes"))

    out = capsys.readouterr().out.lower()
    assert "ruff no está instalado" in out or "no se pudo formatear" in out
    assert "archivo formateado" not in out
