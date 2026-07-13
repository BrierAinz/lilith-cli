"""Tests for the /review slash command."""

from __future__ import annotations

from unittest.mock import patch

import pytest


def _tool_result(success: bool = True, data=None, error: str | None = None):
    from lilith_tools.base import ToolResult

    return ToolResult(success=success, data=data, error=error)


@pytest.fixture
def patched_review_tool(monkeypatch):
    """Patch GitOperationTool at its source module so /review never spawns git.

    /review does ``from lilith_tools.git_tools import GitOperationTool`` inside
    the ImportError fallback, so we must patch the symbol where it lives, not
    on ``lilith_cli.extra_commands``.
    """
    import lilith_tools.git_tools as gt

    captured: list[dict[str, object]] = []
    pending_result: list = []

    class FakeGit:
        def execute(self, **kw):
            captured.append(kw)
            if pending_result:
                return pending_result.pop(0)
            return _tool_result(success=True, data={"output": "diff --git a/x b/x"})

    monkeypatch.setattr(gt, "GitOperationTool", FakeGit)
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