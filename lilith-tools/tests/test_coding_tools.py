"""Tests for the coding workflow tools (run_test, run_linter, format_file)."""

from pathlib import Path

import pytest

from lilith_tools.coding_tools import (
    FormatFileTool,
    RunLinterTool,
    RunTestTool,
    _detect_formatter,
    _detect_linter,
    _detect_test_command,
    _parse_linter_output,
)
from lilith_tools.registry import ToolRegistry
from lilith_tools.undo import UndoManager


# ── Registration / smoke ───────────────────────────────────────────


def _ensure_registered() -> None:
    """Re-register the new tools if another test cleared the registry."""
    for name, cls in {
        "run_test": RunTestTool,
        "run_linter": RunLinterTool,
        "format_file": FormatFileTool,
    }.items():
        ToolRegistry._tools.setdefault(name, cls)


_ensure_registered()


def test_run_test_registered():
    _ensure_registered()
    assert "run_test" in ToolRegistry.list_tools()
    assert "run_linter" in ToolRegistry.list_tools()
    assert "format_file" in ToolRegistry.list_tools()


# ── Auto-detection helpers ───────────────────────────────────────────


def test_detect_test_command_python(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\n")
    assert _detect_test_command(str(tmp_path), None) is not None


def test_detect_linter_python(tmp_path: Path):
    (tmp_path / "foo.py").write_text("x = 1\n")
    detected = _detect_linter(str(tmp_path), None)
    assert detected is not None
    assert "ruff" in detected or "py_compile" in detected


def test_detect_formatter_js(tmp_path: Path):
    f = tmp_path / "app.js"
    f.write_text("const x=1\n")
    detected = _detect_formatter(str(f), None)
    assert detected is not None


def test_detect_formatter_py(tmp_path: Path):
    f = tmp_path / "app.py"
    f.write_text("x = 1\n")
    detected = _detect_formatter(str(f), None)
    assert detected is not None
    assert "black" in detected


# ── Parser ───────────────────────────────────────────────────────────


def test_parse_ruff_output():
    output = "src/app.py:10:5: E501 Line too long\n"
    issues = _parse_linter_output(output, "ruff check .")
    assert len(issues) == 1
    assert issues[0]["file"] == "src/app.py"
    assert issues[0]["line"] == 10
    assert issues[0]["column"] == 5
    assert "E501" in issues[0]["message"]


def test_parse_eslint_output():
    output = "src/app.js:3:2: Missing semicolon [semi]\n"
    issues = _parse_linter_output(output, "eslint .")
    assert len(issues) == 1
    assert issues[0]["rule"] == "semi"


# ── Tool execution ───────────────────────────────────────────────────


def test_run_test_tool_with_no_command(tmp_path: Path, monkeypatch):
    """Auto-detection should fall back to a Python test command in a temp project."""
    (tmp_path / "pyproject.toml").write_text("[project]\n")
    tool = RunTestTool()
    result = tool.execute(path=str(tmp_path), timeout=15)
    # If pytest is not installed or the subprocess hangs, we still want to
    # at least confirm the tool returned something. Skip the strict checks
    # when the environment doesn't have pytest available.
    if not result.success and "timeout" in (result.error or "").lower():
        pytest.skip("pytest not installed or environment slow")
    assert result.data is not None
    assert "command" in result.data


def test_run_linter_tool_with_no_linter(tmp_path: Path):
    (tmp_path / "foo.py").write_text("x = 1\n")
    tool = RunLinterTool()
    result = tool.execute(path=str(tmp_path), timeout=5)
    assert result.data is not None
    assert "command" in result.data
    assert isinstance(result.data.get("issues"), list)


def test_format_file_tool_formats(tmp_path: Path, monkeypatch):
    """Format a Python file using an explicit mock formatter command."""
    f = tmp_path / "bad.py"
    f.write_text("x=1\ny=2\n")

    # Use a dedicated undo root for the test so it doesn't pollute the user's.
    undo = UndoManager(root_dir=tmp_path / "undo")
    monkeypatch.setattr("lilith_tools.coding_tools.UndoManager", lambda **_: undo)

    # Provide a tiny cross-platform formatter script that just rewrites the file.
    fmt_script = tmp_path / "formatter.py"
    fmt_script.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "for p in sys.argv[1:]:\n"
        "    Path(p).write_text(Path(p).read_text().replace('=1', ' = 1'))\n"
    )

    tool = FormatFileTool()
    result = tool.execute(
        path=str(f),
        formatter=f"python {fmt_script}",
        timeout=10,
    )
    assert result.success
    assert result.data["path"] == str(f.resolve())
    assert result.data.get("formatted") is True
    assert " = 1" in f.read_text()


def test_format_file_tool_requires_path():
    tool = FormatFileTool()
    result = tool.execute()
    assert not result.success
    assert "path es requerido" in result.error
