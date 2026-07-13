"""Tests for /deps slash command."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest


def _run(coro):
    return asyncio.run(coro)


def _write_pyproject(tmp_path: Path, content: str) -> Path:
    (tmp_path / "pyproject.toml").write_text(content, encoding="utf-8", newline="\n")
    return tmp_path


SAMPLE_PYPROJECT = (
    "[project]\n"
    'name = "demo"\n'
    'version = "0.1.0"\n'
    "dependencies = [\n"
    '    "requests>=2.31",\n'
    '    "rich>=13.0",\n'
    "]\n"
    "\n"
    "[dependency-groups]\n"
    "dev = [\n"
    '    "pytest>=8.0",\n'
    "]\n"
)


def test_deps_help_shows_usage(fake_session, capsys):
    from lilith_cli.extra_commands import run_deps_command

    _run(run_deps_command(fake_session, "help"))

    out = capsys.readouterr().out
    # The help header advertises the subcommands; check for each mode keyword
    assert "outdated" in out
    assert "licenses" in out
    assert "pyproject" in out


def test_deps_default_lists_packages_from_pyproject(fake_session, tmp_path, monkeypatch, capsys):
    _write_pyproject(tmp_path, SAMPLE_PYPROJECT)
    monkeypatch.chdir(tmp_path)

    from lilith_cli.extra_commands import run_deps_command

    _run(run_deps_command(fake_session, ""))

    out = capsys.readouterr().out
    # The dependencies should appear in the output (Rich may wrap in ANSI codes)
    assert "requests" in out
    assert "rich" in out
    assert "pytest" in out


def test_deps_with_explicit_path(fake_session, tmp_path, capsys):
    _write_pyproject(tmp_path, SAMPLE_PYPROJECT)

    from lilith_cli.extra_commands import run_deps_command

    _run(run_deps_command(fake_session, str(tmp_path)))

    out = capsys.readouterr().out
    assert "requests" in out
    assert "rich" in out


def test_deps_handles_requirements_txt(fake_session, tmp_path, monkeypatch, capsys):
    req = (
        "# comment\n"
        "flask>=3.0\n"
        "numpy\n"
        "# another\n"
    )
    (tmp_path / "requirements.txt").write_text(req, encoding="utf-8", newline="\n")
    monkeypatch.chdir(tmp_path)

    from lilith_cli.extra_commands import run_deps_command

    _run(run_deps_command(fake_session, ""))

    out = capsys.readouterr().out
    assert "flask" in out
    assert "numpy" in out


def test_deps_handles_package_json(fake_session, tmp_path, monkeypatch, capsys):
    pkg = {
        "name": "demo",
        "version": "1.0.0",
        "dependencies": {"react": "^18.0.0", "lodash": "^4.17.0"},
        "devDependencies": {"typescript": "^5.0.0"},
    }
    (tmp_path / "package.json").write_text(json.dumps(pkg, indent=2), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    from lilith_cli.extra_commands import run_deps_command

    _run(run_deps_command(fake_session, ""))

    out = capsys.readouterr().out
    assert "react" in out
    assert "typescript" in out


def test_deps_no_manifests_shows_message(fake_session, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    from lilith_cli.extra_commands import run_deps_command

    _run(run_deps_command(fake_session, ""))

    out = capsys.readouterr().out
    low = out.lower()
    assert ("no" in low) and ("dependencias" in low)


def test_deps_bad_path_via_outdated_subcommand(fake_session, capsys):
    """An invalid path passed to /deps outdated must not crash."""
    from lilith_cli.extra_commands import run_deps_command

    # outdated with bad path triggers render_error to a channel capsys may capture
    _run(run_deps_command(fake_session, "outdated /nonexistent/zzz/yyy/abc123"))

    # capture both stdout + stderr (render_error typically goes to stdout via console)
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    # Either the error keyword was printed, OR capsys missed it (Rich destination).
    # Use a defensive assertion: the command must not raise.
    assert "Traceback" not in combined


def test_deps_parse_pep508_basic():
    from lilith_cli.extra_commands import _deps_parse_pep508

    # Standard version-pinned spec
    name, ver = _deps_parse_pep508("requests>=2.31")
    assert name == "requests"
    assert "2.31" in ver

    # Env markers stripped before name extraction
    name2, _ = _deps_parse_pep508("foo>=1.0; sys_platform == 'linux'")
    assert name2 == "foo"


def test_deps_collect_finds_packages_from_pyproject(tmp_path):
    from lilith_cli.extra_commands import _deps_collect

    _write_pyproject(tmp_path, SAMPLE_PYPROJECT)
    deps, _ = _deps_collect(tmp_path)

    names = {d[0] for d in deps}
    assert "requests" in names
    assert "rich" in names
    assert "pytest" in names


def test_deps_collect_returns_empty_for_empty_dir(tmp_path):
    from lilith_cli.extra_commands import _deps_collect

    deps, licenses = _deps_collect(tmp_path)
    assert deps == []
    assert licenses == {}
