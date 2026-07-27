"""Tests for the /review slash command."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest


def _tool_result(success: bool = True, data=None, error: str | None = None):
    from lilith_tools.base import ToolResult

    return ToolResult(success=success, data=data, error=error)


@pytest.fixture
def patched_review_tool(monkeypatch):
    """Patch the hardened review runner so tests never spawn git."""
    from lilith_cli import extra_commands as ec

    captured: list[dict[str, object]] = []
    pending_result: list = []

    def fake_run_review_git(**kw):
        captured.append(kw)
        if pending_result:
            return pending_result.pop(0)
        return _tool_result(success=True, data={"output": "diff --git a/x b/x"})

    monkeypatch.setattr(ec, "_run_review_git", fake_run_review_git)
    return {"captured": captured, "pending_result": pending_result}


@pytest.mark.asyncio
async def test_review_default_uses_diff_subcommand(fake_session, patched_review_tool):
    """/review with no args must invoke GitOperationTool with op='diff'."""
    from lilith_cli.extra_commands import run_review_command

    with patch("lilith_cli.extra_commands.console.print"):
        await run_review_command(fake_session, "")

    assert len(patched_review_tool["captured"]) == 1
    call = patched_review_tool["captured"][0]
    assert call.get("op") == "diff"


@pytest.mark.asyncio
async def test_review_with_subcommand_passes_it_through(fake_session, patched_review_tool):
    """/review status must forward 'status' to GitOperationTool.op."""
    from lilith_cli.extra_commands import run_review_command

    with patch("lilith_cli.extra_commands.console.print"):
        await run_review_command(fake_session, "status")

    assert len(patched_review_tool["captured"]) == 1
    call = patched_review_tool["captured"][0]
    assert call.get("op") == "status"


@pytest.mark.asyncio
async def test_review_tool_failure_renders_error(fake_session, patched_review_tool, capsys):
    """/review when the tool returns success=False must surface the error message."""
    patched_review_tool["pending_result"].append(
        _tool_result(success=False, data=None, error="not a git repository")
    )

    from lilith_cli.extra_commands import run_review_command

    await run_review_command(fake_session, "")

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "not a git repository" in combined or "error" in combined.lower()


@pytest.mark.asyncio
async def test_review_tool_output_is_printed(fake_session, patched_review_tool, capsys):
    """/review with a successful tool result must print its 'output' text."""
    patched_review_tool["pending_result"].append(
        _tool_result(success=True, data={"output": "fake diff content"})
    )

    from lilith_cli.extra_commands import run_review_command

    await run_review_command(fake_session, "")

    captured = capsys.readouterr()
    assert "fake diff content" in captured.out


def test_review_git_disables_repo_controlled_execution(monkeypatch):
    """La captura del diff neutraliza fsmonitor, diff externo y textconv."""
    from lilith_cli import extra_commands as ec

    observed = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="safe diff", stderr="")

    monkeypatch.setattr(ec.subprocess, "run", fake_run)

    result = ec._run_review_git(op="diff", args="--cached --name-only")

    command = observed["command"]
    assert result.success is True
    assert command[:7] == [
        "git", "--no-pager", "-c", "core.fsmonitor=false", "-c", "diff.external=", "diff"
    ]
    assert "--no-ext-diff" in command
    assert "--no-textconv" in command
    assert observed["kwargs"]["check"] is False
