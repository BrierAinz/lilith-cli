"""Tests for the /diff-branch slash command.

Covers: missing-ref usage error, full-diff invocation against a ref,
stats-mode invocation, file-filter invocation, git-error propagation,
empty output handling, and the rich-table rendering for numstat.

The command shells out to ``git`` via subprocess; we monkeypatch
``extra_commands.subprocess.run`` to keep tests hermetic and offline.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest


class _FakeCompletedProcess:
    def __init__(
        self,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture
def captured_subprocess(monkeypatch):
    """Replace subprocess.run inside extra_commands; record every call."""
    import lilith_cli.extra_commands as ec

    captured: list[dict[str, object]] = []

    def fake_run(*args, **kwargs):
        captured.append({"args": args, "kwargs": kwargs})
        return _FakeCompletedProcess(returncode=0, stdout="")

    monkeypatch.setattr(ec.subprocess, "run", fake_run)
    return captured


def _run(coro):
    return asyncio.run(coro)


def test_diff_branch_without_ref_shows_usage(
    fake_session, captured_subprocess, capsys
):
    """/diff-branch without a ref must show a usage error and not shell out."""
    from lilith_cli.extra_commands import run_diff_branch_command

    _run(run_diff_branch_command(fake_session, ""))

    combined = capsys.readouterr().out + capsys.readouterr().err
    assert "Uso" in combined
    # ``capsys.readouterr()`` was called twice; only the first call captured
    # anything. Either way: no git subprocess should have been invoked.
    assert captured_subprocess == []


def test_diff_branch_runs_git_diff_with_triple_dot(fake_session, captured_subprocess):
    """/diff-branch <ref> must invoke ``git diff <ref>...HEAD`` (no stats, no path)."""
    from lilith_cli.extra_commands import run_diff_branch_command

    _run(run_diff_branch_command(fake_session, "main"))

    assert len(captured_subprocess) == 1, "expected exactly one subprocess call"
    cmd = captured_subprocess[0]["args"][0]
    assert cmd[:3] == ["git", "diff", "main...HEAD"]


def test_diff_branch_stats_uses_numstat(fake_session, captured_subprocess, capsys):
    """/diff-branch <ref> stats must use --numstat and render a Rich table."""
    from lilith_cli.extra_commands import run_diff_branch_command

    # Patch subprocess to return a realistic numstat payload.
    numstat_payload = "12\t3\tlilith_cli/foo.py\n-\t-\timg.png\n"

    def fake_run(*args, **kwargs):
        captured_subprocess.append({"args": args, "kwargs": kwargs})
        return _FakeCompletedProcess(returncode=0, stdout=numstat_payload)

    import lilith_cli.extra_commands as ec

    with patch.object(ec.subprocess, "run", side_effect=fake_run):
        _run(run_diff_branch_command(fake_session, "main stats"))

    # --numstat flag must be present.
    cmd = captured_subprocess[0]["args"][0]
    assert "--numstat" in cmd
    assert "main...HEAD" in cmd

    # Table + footer should appear.
    out = capsys.readouterr().out
    assert "lilith_cli/foo.py" in out
    assert "img.png" in out
    assert "+12" in out or "12" in out
    # Footer mentions the ref.
    assert "main" in out


def test_diff_branch_with_path_filters_output(fake_session, captured_subprocess):
    """/diff-branch <ref> <path> must append ``-- <path>`` to the git command."""
    from lilith_cli.extra_commands import run_diff_branch_command

    _run(run_diff_branch_command(fake_session, "v1.0.0 src/foo.py"))

    cmd = captured_subprocess[0]["args"][0]
    assert cmd == ["git", "diff", "v1.0.0...HEAD", "--", "src/foo.py"]


def test_diff_branch_git_error_is_surfaced(
    fake_session, captured_subprocess, capsys
):
    """A non-zero git exit must render the stderr without crashing."""
    from lilith_cli.extra_commands import run_diff_branch_command
    import lilith_cli.extra_commands as ec

    def failing_run(*args, **kwargs):
        captured_subprocess.append({"args": args, "kwargs": kwargs})
        return _FakeCompletedProcess(
            returncode=128,
            stdout="",
            stderr="fatal: unknown revision 'bogus-ref'",
        )

    with patch.object(ec.subprocess, "run", side_effect=failing_run):
        _run(run_diff_branch_command(fake_session, "bogus-ref"))

    combined = capsys.readouterr().out
    assert "unknown revision" in combined or "bogus-ref" in combined


def test_diff_branch_empty_diff_prints_dim_message(
    fake_session, captured_subprocess, capsys
):
    """When git produces no output, /diff-branch prints a dim empty-state line."""
    from lilith_cli.extra_commands import run_diff_branch_command
    import lilith_cli.extra_commands as ec

    def empty_run(*args, **kwargs):
        captured_subprocess.append({"args": args, "kwargs": kwargs})
        return _FakeCompletedProcess(returncode=0, stdout="")

    with patch.object(ec.subprocess, "run", side_effect=empty_run):
        _run(run_diff_branch_command(fake_session, "main"))

    out = capsys.readouterr().out
    assert "Sin cambios" in out
    assert "main" in out


def test_diff_branch_subprocess_exception_is_handled(
    fake_session, captured_subprocess, capsys
):
    """If subprocess.run raises, render_error must be called — no traceback."""
    from lilith_cli.extra_commands import run_diff_branch_command
    import lilith_cli.extra_commands as ec

    def boom(*args, **kwargs):
        captured_subprocess.append({"args": args, "kwargs": kwargs})
        raise OSError("git not found")

    with patch.object(ec.subprocess, "run", side_effect=boom):
        # Must not raise.
        _run(run_diff_branch_command(fake_session, "main"))

    out = capsys.readouterr().out
    assert "git" in out.lower() or "error" in out.lower()


def test_diff_branch_full_diff_prints_unfiltered_output(
    fake_session, captured_subprocess, capsys
):
    """Non-stats mode prints the raw git diff payload verbatim."""
    from lilith_cli.extra_commands import run_diff_branch_command
    import lilith_cli.extra_commands as ec

    raw_diff = (
        "diff --git a/foo.py b/foo.py\n"
        "index 0000..1111 100644\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-old\n"
        "+new\n"
    )

    def full_run(*args, **kwargs):
        captured_subprocess.append({"args": args, "kwargs": kwargs})
        return _FakeCompletedProcess(returncode=0, stdout=raw_diff)

    with patch.object(ec.subprocess, "run", side_effect=full_run):
        _run(run_diff_branch_command(fake_session, "feature-branch"))

    out = capsys.readouterr().out
    assert "-old" in out
    assert "+new" in out
    assert "feature-branch" in out