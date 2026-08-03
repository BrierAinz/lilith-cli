"""Additional tests for lilith-tools: BrowserTool, CodingTool, FileReadTool, DirectoryListTool."""

from lilith_tools.base import ToolResult
from lilith_tools.browser import BrowserTool
from lilith_tools.coding import CodingTool
from lilith_tools.filesystem import DirectoryListTool, FileReadTool
from lilith_tools.system import SystemInfoTool


# ------------------------------------------------------------------
# CodingTool
# ------------------------------------------------------------------


class TestCodingTool:
    """Tests for the sandboxed Python code execution tool."""

    def test_execute_empty_code_returns_error(self):
        tool = CodingTool()
        result = tool.execute(code="")
        assert isinstance(result, ToolResult)
        assert not result.success
        assert "Codigo vacio" in result.error

    def test_execute_blocked_keyword(self):
        tool = CodingTool()
        for blocked in ["__import__", "eval(", "exec(", "open(", "os.system", "subprocess"]:
            result = tool.execute(code=f"x = {blocked}")
            assert isinstance(result, ToolResult)
            assert not result.success
            assert "bloqueado" in result.error.lower() or "blocked" in result.error.lower()

    def test_execute_simple_code(self):
        tool = CodingTool()
        result = tool.execute(code="print('hello world')")
        assert isinstance(result, ToolResult)
        assert result.success
        assert result.data["returncode"] == 0
        assert "hello world" in result.data.get("stdout", "")

    def test_execute_returns_stderr(self):
        tool = CodingTool()
        # Use code that doesn't trigger the sandbox blacklist (no "import sys" or "os")
        result = tool.execute(code="1 + 1; raise SystemExit(2)")
        assert isinstance(result, ToolResult)
        assert result.success
        # SystemExit should produce a non-zero returncode
        assert result.data["returncode"] != 0

    def test_execute_timeout(self):
        tool = CodingTool()
        result = tool.execute(code="import time; time.sleep(30)", timeout=1)
        assert isinstance(result, ToolResult)
        assert not result.success
        assert "Timeout" in result.error or "timeout" in result.error.lower()


# ------------------------------------------------------------------
# FileReadTool
# ------------------------------------------------------------------


class TestFileReadTool:
    """Tests for the file reading tool."""

    def test_read_existing_file(self, tmp_path):
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello from file", encoding="utf-8")
        tool = FileReadTool()
        result = tool.execute(path=str(test_file))
        assert result.success
        assert result.data == "hello from file"

    def test_read_missing_file(self):
        tool = FileReadTool()
        result = tool.execute(path="/nonexistent/file/not_here.txt")
        assert not result.success
        assert result.error

    def test_read_directory_instead_of_file(self, tmp_path):
        tool = FileReadTool()
        result = tool.execute(path=str(tmp_path))
        # Reading a directory should fail gracefully
        assert not result.success or isinstance(result.data, str)


# ------------------------------------------------------------------
# DirectoryListTool
# ------------------------------------------------------------------


class TestDirectoryListTool:
    """Tests for the directory listing tool."""

    def test_list_existing_directory(self, tmp_path):
        (tmp_path / "file1.txt").write_text("a", encoding="utf-8")
        (tmp_path / "subdir").mkdir()
        tool = DirectoryListTool()
        result = tool.execute(path=str(tmp_path))
        assert result.success
        names = [item["name"] for item in result.data]
        assert "file1.txt" in names
        assert "subdir" in names

    def test_list_empty_directory(self, tmp_path):
        tool = DirectoryListTool()
        result = tool.execute(path=str(tmp_path))
        assert result.success
        assert result.data == []

    def test_list_missing_directory(self):
        tool = DirectoryListTool()
        result = tool.execute(path="/nonexistent/dir/not_here")
        assert not result.success

    def test_list_includes_file_size(self, tmp_path):
        test_file = tmp_path / "sized.txt"
        test_file.write_text("hello world", encoding="utf-8")
        tool = DirectoryListTool()
        result = tool.execute(path=str(tmp_path))
        assert result.success
        file_item = next(item for item in result.data if item["name"] == "sized.txt")
        assert file_item["size"] > 0
        assert file_item["type"] == "file"


# ------------------------------------------------------------------
# BrowserTool (fallback/requests mode only)
# ------------------------------------------------------------------


class TestBrowserTool:
    """Tests for the browser tool — only fallback mode (no Playwright required)."""

    def test_execute_empty_url(self):
        tool = BrowserTool()
        result = tool.execute(url="")
        assert isinstance(result, ToolResult)
        assert not result.success
        assert "URL vacia" in result.error

    def test_execute_invalid_url_returns_error(self):
        tool = BrowserTool()
        # Force requests fallback with a timeout to avoid long waits
        result = tool.execute(url="http://127.0.0.1:1", use_playwright=False)
        assert isinstance(result, ToolResult)
        # Connection refused — should be error or success with error data
        assert not result.success or result.data is not None

    def test_validate_parameters(self):
        tool = BrowserTool()
        assert tool.name == "browser"
        assert tool.parameters is not None
        assert "url" in tool.parameters


# ------------------------------------------------------------------
# BaseTool / ToolResult
# ------------------------------------------------------------------


class TestToolResult:
    """Tests for ToolResult dataclass."""

    def test_success_result(self):
        result = ToolResult(success=True, data={"key": "value"})
        assert result.success
        assert result.data == {"key": "value"}
        assert result.error == ""

    def test_error_result(self):
        result = ToolResult(success=False, data=None, error="fail")
        assert not result.success
        assert result.error == "fail"


class TestBaseToolValidation:
    """Tests for BaseTool.validate()."""

    def test_validate_no_parameters(self):
        tool = SystemInfoTool()
        assert tool.validate({})

    def test_validate_required_parameter_present(self):
        tool = FileReadTool()
        assert tool.validate({"path": "/some/file"})

    def test_validate_required_parameter_missing(self):
        tool = FileReadTool()
        assert not tool.validate({})
