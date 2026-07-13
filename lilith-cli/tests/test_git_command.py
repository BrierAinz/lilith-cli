"""Tests for the /git slash command."""

from __future__ import annotations

from unittest.mock import patch

import pytest


def _tool_result(success: bool = True, data=None, error: str | None = None):
    from lilith_tools.base import ToolResult

    return ToolResult(success=success, data=data, error=error)


@pytest.fixture
def patched_git_tool(monkeypatch):
    """Patch GitOperationTool in extra_commands so no real git subprocess is spawned."""
    import lilith_cli.extra_commands as ec
    from lilith_tools.base import ToolResult

    captured: list[dict[str, object]] = []
    pending_result: list[ToolResult] = []

    class FakeGit:
        def execute(self, **kw):
            captured.append(kw)
            if pending_result:
                return pending_result.pop(0)
            return ToolResult(success=True, data={"message": "On branch main"})

    monkeypatch.setattr(ec, "GitOperationTool", FakeGit)
    return {"captured": captured, "pending_result": pending_result}


@pytest.mark.asyncio
async def test_git_status_invokes_tool(fake_session, patched_git_tool):
    """/git status must invoke GitOperationTool with op='status'."""
    from lilith_cli.extra_commands import run_git_command

    with patch("lilith_cli.extra_commands.console.print"):
        await run_git_command(fake_session, "status")

    assert len(patched_git_tool["captured"]) == 1
    call = patched_git_tool["captured"][0]
    assert call.get("op") == "status"
    assert call.get("args") == ""


@pytest.mark.asyncio
async def test_git_log_with_args(fake_session, patched_git_tool):
    """/git log --oneline -5 must pass the trailing text as the args field."""
    from lilith_cli.extra_commands import run_git_command

    with patch("lilith_cli.extra_commands.console.print"):
        await run_git_command(fake_session, "log --oneline -5")

    assert len(patched_git_tool["captured"]) == 1
    call = patched_git_tool["captured"][0]
    assert call.get("op") == "log"
    assert call.get("args") == "--oneline -5"


@pytest.mark.asyncio
async def test_git_empty_args_shows_usage_error(fake_session, monkeypatch):
    """/git with no args must print a usage error and NOT invoke GitOperationTool."""
    import lilith_cli.extra_commands as ec

    class ShouldNotCall:
        def execute(self, **_kw):  # pragma: no cover - guard
            raise AssertionError("GitOperationTool must not be called for empty args")

    monkeypatch.setattr(ec, "GitOperationTool", ShouldNotCall)

    prints = []

    def capture(*args, **kwargs):
        prints.append(args)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        from lilith_cli.extra_commands import run_git_command

        await run_git_command(fake_session, "")

    combined = "\n".join(str(s) for entry in prints for s in entry if isinstance(s, str))
    assert "Uso:" in combined


@pytest.mark.asyncio
async def test_git_tool_failure_renders_error(fake_session, monkeypatch, patched_git_tool):
    """/git when GitOperationTool returns success=False must print a render_error."""
    patched_git_tool["pending_result"].append(
        _tool_result(success=False, data=None, error="not a git repository")
    )

    prints = []

    def capture(*args, **kwargs):
        prints.append(args)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        from lilith_cli.extra_commands import run_git_command

        await run_git_command(fake_session, "status")

    combined = "\n".join(str(s) for entry in prints for s in entry if isinstance(s, str))
    assert "not a git repository" in combined


@pytest.mark.asyncio
async def test_git_tool_data_output_is_printed(fake_session, monkeypatch, patched_git_tool):
    """/git when GitOperationTool returns a dict with 'output' must print that text."""
    patched_git_tool["pending_result"].append(
        _tool_result(success=True, data={"output": "diff --git a/x b/x\n+new"})
    )

    prints = []

    def capture(*args, **kwargs):
        prints.append(args)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        from lilith_cli.extra_commands import run_git_command

        await run_git_command(fake_session, "diff")

    combined = "\n".join(str(s) for entry in prints for s in entry if isinstance(s, str))
    assert "diff --git" in combined
    assert "+new" in combined
