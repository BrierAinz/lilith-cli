"""Tests for filesystem tools diff-preview safety feature."""

from pathlib import Path

from lilith_tools.base import ToolResult
from lilith_tools.filesystem import FileEditTool, FileWriteTool


class TestFileWriteDiffPreview:
    """Tests for file_write show_diff mode."""

    def test_write_without_show_diff(self, tmp_path):
        target = tmp_path / "out.txt"
        tool = FileWriteTool()
        result = tool.execute(path=str(target), content="hello")
        assert result.success
        assert target.read_text(encoding="utf-8") == "hello"

    def test_show_diff_for_new_file(self, tmp_path):
        target = tmp_path / "new.txt"
        tool = FileWriteTool()
        result = tool.execute(path=str(target), content="hello\nworld", show_diff=True)
        assert result.success
        assert not target.exists()
        data = result.data
        assert data["show_diff"] is True
        assert data["bytes"] == 11
        assert "hello" in data["diff"]
        assert "+hello" in data["diff"]

    def test_show_diff_for_existing_file(self, tmp_path):
        target = tmp_path / "existing.txt"
        target.write_text("alpha\n", encoding="utf-8")
        tool = FileWriteTool()
        result = tool.execute(path=str(target), content="beta\n", show_diff=True)
        assert result.success
        assert target.read_text(encoding="utf-8") == "alpha\n"
        diff = result.data["diff"]
        assert "-alpha" in diff
        assert "+beta" in diff


class TestFileEditDiffPreview:
    """Tests for file_edit show_diff mode."""

    def test_edit_without_show_diff(self, tmp_path):
        target = tmp_path / "edit.txt"
        target.write_text("foo bar baz", encoding="utf-8")
        tool = FileEditTool()
        result = tool.execute(path=str(target), old_string="bar", new_string="qux")
        assert result.success
        assert target.read_text(encoding="utf-8") == "foo qux baz"

    def test_show_diff_does_not_write(self, tmp_path):
        target = tmp_path / "edit.txt"
        target.write_text("foo bar baz\n", encoding="utf-8")
        tool = FileEditTool()
        result = tool.execute(
            path=str(target),
            old_string="bar",
            new_string="qux",
            show_diff=True,
        )
        assert result.success
        assert target.read_text(encoding="utf-8") == "foo bar baz\n"
        diff = result.data["diff"]
        assert "-foo bar" in diff or "-bar" in diff
        assert "+foo qux" in diff or "+qux" in diff
        assert result.data["replacements"] == 1

    def test_show_diff_replace_all(self, tmp_path):
        target = tmp_path / "edit.txt"
        target.write_text("a\nb\na\n", encoding="utf-8")
        tool = FileEditTool()
        result = tool.execute(
            path=str(target),
            old_string="a",
            new_string="x",
            replace_all=True,
            show_diff=True,
        )
        assert result.success
        assert target.read_text(encoding="utf-8") == "a\nb\na\n"
        assert result.data["replacements"] == 2

    def test_show_diff_error_when_old_string_missing(self, tmp_path):
        target = tmp_path / "edit.txt"
        target.write_text("foo", encoding="utf-8")
        tool = FileEditTool()
        result = tool.execute(
            path=str(target),
            old_string="missing",
            new_string="x",
            show_diff=True,
        )
        assert not result.success
        assert "no encontrado" in result.error


class TestUnifiedDiffHelper:
    """Tests for the _unified_diff helper."""

    def test_empty_original_diff(self, tmp_path):
        from lilith_tools.filesystem import _unified_diff

        diff = _unified_diff("", "hello", tmp_path / "a.txt")
        assert "+hello" in diff

    def test_no_newline_at_end(self, tmp_path):
        from lilith_tools.filesystem import _unified_diff

        diff = _unified_diff("old", "new", tmp_path / "a.txt")
        assert "-old" in diff
        assert "+new" in diff


# ── ITEM 2 (tanda 6): file_append tool ────────────────────────────────


class TestFileAppendTool:
    """Tests for file_append (chunked-write companion to file_write)."""

    def test_append_to_existing_file(self, tmp_path):
        target = tmp_path / "log.txt"
        target.write_text("line1\n", encoding="utf-8")
        from lilith_tools.filesystem import FileAppendTool
        result = FileAppendTool().execute(path=str(target), content="line2\n")
        assert result.success
        assert target.read_text(encoding="utf-8") == "line1\nline2\n"
        assert result.data["appended"] is True
        assert result.data["bytes"] == len("line2\n".encode("utf-8"))

    def test_append_creates_file_when_missing(self, tmp_path):
        target = tmp_path / "fresh.txt"
        assert not target.exists()
        from lilith_tools.filesystem import FileAppendTool
        result = FileAppendTool().execute(path=str(target), content="hello")
        assert result.success
        assert target.read_text(encoding="utf-8") == "hello"
        assert result.data["appended"] is False
        assert target.exists()

    def test_append_creates_parent_dirs(self, tmp_path):
        target = tmp_path / "sub" / "deep" / "out.txt"
        from lilith_tools.filesystem import FileAppendTool
        result = FileAppendTool().execute(path=str(target), content="x")
        assert result.success
        assert target.read_text(encoding="utf-8") == "x"

    def test_append_does_not_implicit_newline(self, tmp_path):
        # file_append must NOT add a newline the caller didn't send —
        # that lets model chunk a file by just concatenating chunks.
        target = tmp_path / "bin"
        from lilith_tools.filesystem import FileAppendTool
        FileAppendTool().execute(path=str(target), content="AAA")
        FileAppendTool().execute(path=str(target), content="BBB")
        assert target.read_text(encoding="utf-8") == "AAABBB"

    def test_append_requires_path(self, tmp_path):
        from lilith_tools.filesystem import FileAppendTool
        result = FileAppendTool().execute(path="", content="x")
        assert not result.success
        assert "path es requerido" in result.error

    def test_chunked_write_workflow(self, tmp_path):
        """The whole point of file_append: split a 300-line file into
        a file_write + file_append pair, get exactly the same content."""
        target = tmp_path / "big.txt"
        head = "\n".join(f"head{i}" for i in range(50)) + "\n"
        rest = "\n".join(f"tail{i}" for i in range(250)) + "\n"
        from lilith_tools.filesystem import FileAppendTool, FileWriteTool
        assert FileWriteTool().execute(path=str(target), content=head).success
        assert FileAppendTool().execute(path=str(target), content=rest).success
        lines = target.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 300
        assert lines[0] == "head0"
        assert lines[-1] == "tail249"

    def test_append_registered_in_tool_registry(self):
        from lilith_tools.filesystem import FileAppendTool
        from lilith_tools.registry import ToolRegistry
        assert ToolRegistry.get("file_append") is FileAppendTool
