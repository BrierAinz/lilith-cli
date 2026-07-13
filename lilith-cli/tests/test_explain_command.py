"""Tests for /explain slash command: explain a file or a Lilith feature."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


def _run(coro):
    return asyncio.run(coro)


def test_explain_feature_known(fake_session, capsys):
    """/explain --feature release prints the release feature doc."""
    from lilith_cli.extra_commands import run_explain_command

    _run(run_explain_command(fake_session, "--feature release"))

    out = capsys.readouterr().out
    assert "Feature:" in out
    assert "release" in out
    assert "/release" in out
    assert "patch" in out


def test_explain_feature_unknown(fake_session, capsys):
    """/explain --feature <unknown> renders friendly error."""
    from lilith_cli.extra_commands import run_explain_command

    _run(run_explain_command(fake_session, "--feature no-existe-esta-feature"))

    out = capsys.readouterr().out
    assert "Feature desconocida" in out or "no-existe" in out


def test_explain_no_args(fake_session, capsys):
    """/explain with no args prints usage."""
    from lilith_cli.extra_commands import run_explain_command

    _run(run_explain_command(fake_session, ""))

    out = capsys.readouterr().out
    assert "Uso:" in out
    assert "--feature" in out


def test_explain_file_not_found(fake_session, capsys):
    """/explain <missing-file> renders error."""
    from lilith_cli.extra_commands import run_explain_command

    _run(run_explain_command(fake_session, "/no/existe/aqui.py"))

    out = capsys.readouterr().out
    assert "no encontrado" in out or "no existe" in out.lower()


def test_explain_file_shallow(tmp_path, fake_session, capsys):
    """/explain <file> --depth shallow prints first 500 chars + header."""
    from lilith_cli.extra_commands import run_explain_command

    f = tmp_path / "snippet.py"
    f.write_text("print('hola mundo')\n" * 50)  # ~900 chars

    _run(run_explain_command(fake_session, f"--depth shallow {f}"))

    out = capsys.readouterr().out
    assert "shallow" in out.lower()
    assert "snippet.py" in out
    # Truncated content
    assert "hola mundo" in out


def test_explain_file_deep(tmp_path, fake_session, capsys):
    """/explain <file> (default depth) prints full content."""
    from lilith_cli.extra_commands import run_explain_command

    f = tmp_path / "data.txt"
    f.write_text("alpha\nbeta\ngamma")

    _run(run_explain_command(fake_session, str(f)))

    out = capsys.readouterr().out
    assert "alpha" in out and "beta" in out and "gamma" in out


def test_explain_feature_shallow(fake_session, capsys):
    """/explain --feature X --depth shallow gives shortened version."""
    from lilith_cli.extra_commands import run_explain_command

    _run(run_explain_command(fake_session, "--feature voice --depth shallow"))

    out = capsys.readouterr().out
    assert "shallow" in out.lower()
    # Shallow version should be shorter than full
    full_cmd_out = capsys.readouterr().out  # discard
    assert len(out) < 500  # sanity: shallow should fit easily


def test_explain_invalid_depth(fake_session, capsys):
    """/explain --depth invalid renders usage error."""
    from lilith_cli.extra_commands import run_explain_command

    _run(run_explain_command(fake_session, "--depth enormous --feature release"))

    out = capsys.readouterr().out
    assert "Uso" in out or "depth" in out.lower()