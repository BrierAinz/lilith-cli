"""Focused tests for DiffCommand.

Audit items 15-16 (deleg_d9685cd6): DiffCommand had no focused tests
covering the shlex.split fix in /diff edit (commit 6213ebb). Without
these, a future refactor could silently break the ability to pass
strings with whitespace — filenames, code, JSON literals — to the
preview command.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from lilith_cli.commands import DiffCommand


class _Cfg:
    model = "t"
    provider = "t"


class _Sess:
    config = _Cfg()


def _ok_result(diff: str = "@@ ... @@\n-old\n+new\n", path: str = "src/foo.py"):
    """Build a fake tool result that DiffCommand treats as success."""
    return MagicMock(success=True, error=None, data={"diff": diff, "path": path})


def _err_result(error: str):
    return MagicMock(success=False, error=error, data={})


# ── /diff with no args ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_diff_no_args_renders_usage(capsys):
    """/diff with no args must print the usage hint, not crash."""
    cmd = DiffCommand(_Sess())
    assert cmd.name == "diff"
    assert "preview" in cmd.aliases

    await cmd.execute("")

    out = capsys.readouterr().out
    assert "Uso:" in out
    assert "write" in out
    assert "edit" in out


@pytest.mark.asyncio
async def test_diff_unknown_subcmd_errors(capsys):
    """/diff foo must report unknown subcommand and not invoke any tool."""
    cmd = DiffCommand(_Sess())
    await cmd.execute("foo")

    out = capsys.readouterr().out
    assert "Subcomando desconocido" in out


# ── /diff write ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_diff_write_passes_path_and_content_to_tool():
    """/diff write <path> <content> hands the first whitespace token as
    path and the rest as content to FileWriteTool."""
    fake_tool = MagicMock()
    fake_tool.execute = MagicMock(return_value=_ok_result(path="hello.txt"))

    with patch("lilith_tools.filesystem.FileWriteTool", return_value=fake_tool):
        await DiffCommand(_Sess()).execute("write hello.txt esto es contenido")

    fake_tool.execute.assert_called_once()
    kwargs = fake_tool.execute.call_args.kwargs
    assert kwargs["path"] == "hello.txt"
    assert kwargs["content"] == "esto es contenido"
    assert kwargs["show_diff"] is True


@pytest.mark.asyncio
async def test_diff_write_without_content_errors(capsys):
    """/diff write with only the path must error, not crash."""
    cmd = DiffCommand(_Sess())
    await cmd.execute("write solo.txt")

    out = capsys.readouterr().out
    assert "Uso:" in out
    assert "write" in out


# ── /diff edit (the shlex fix) ───────────────────────────────────────


@pytest.mark.asyncio
async def test_diff_edit_with_quoted_strings_preserves_whitespace():
    """The regression that motivated the shlex fix: an old/new string
    containing whitespace (e.g. multi-line code) used to be shredded
    by .split(). With shlex.split, quoted tokens survive."""
    fake_tool = MagicMock()
    fake_tool.execute = MagicMock(return_value=_ok_result())

    with patch("lilith_tools.filesystem.FileEditTool", return_value=fake_tool):
        # old and new each have embedded whitespace; quotes preserve it.
        await DiffCommand(_Sess()).execute(
            'edit src/foo.py "def foo():\n    pass" "def foo():\n    return 42"'
        )

    kwargs = fake_tool.execute.call_args.kwargs
    assert kwargs["path"] == "src/foo.py"
    assert kwargs["old_string"] == "def foo():\n    pass"
    assert kwargs["new_string"] == "def foo():\n    return 42"
    assert kwargs["replace_all"] is False
    assert kwargs["show_diff"] is True


@pytest.mark.asyncio
async def test_diff_edit_with_replace_all_flag():
    """/diff edit ... --all must pass replace_all=True."""
    fake_tool = MagicMock()
    fake_tool.execute = MagicMock(return_value=_ok_result())

    with patch("lilith_tools.filesystem.FileEditTool", return_value=fake_tool):
        await DiffCommand(_Sess()).execute('edit a.py "x" "y" --all')

    kwargs = fake_tool.execute.call_args.kwargs
    assert kwargs["replace_all"] is True


@pytest.mark.asyncio
async def test_diff_edit_unbalanced_quotes_errors_gracefully(capsys):
    """/diff edit with unbalanced quotes must render a clear error,
    not blow up with a raw ValueError traceback."""
    cmd = DiffCommand(_Sess())
    await cmd.execute('edit a.py "old new')  # missing closing quote

    out = capsys.readouterr().out
    assert "comillas" in out.lower() or "balanceados" in out.lower()


@pytest.mark.asyncio
async def test_diff_edit_without_enough_tokens_errors(capsys):
    """/diff edit with fewer than 3 tokens (path, old, new) errors."""
    cmd = DiffCommand(_Sess())
    await cmd.execute("edit a.py")

    out = capsys.readouterr().out
    assert "Uso:" in out
    assert "edit" in out


# ── /diff result handling ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_diff_tool_failure_renders_error(capsys):
    """When the tool returns success=False, the error message is rendered."""
    fake_tool = MagicMock()
    fake_tool.execute = MagicMock(return_value=_err_result("file not found"))

    with patch("lilith_tools.filesystem.FileEditTool", return_value=fake_tool):
        await DiffCommand(_Sess()).execute('edit a.py "x" "y"')

    out = capsys.readouterr().out
    assert "file not found" in out


@pytest.mark.asyncio
async def test_diff_no_diff_in_result_renders_dim_message(capsys):
    """A successful tool result with empty 'diff' shows a dim 'no changes'
    message rather than an empty diff preview."""
    fake_tool = MagicMock()
    fake_tool.execute = MagicMock(return_value=_ok_result(diff=""))

    with patch("lilith_tools.filesystem.FileEditTool", return_value=fake_tool):
        await DiffCommand(_Sess()).execute('edit a.py "x" "y"')

    out = capsys.readouterr().out
    assert "Sin cambios" in out
