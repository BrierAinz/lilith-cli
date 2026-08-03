"""Tests for the batch_edit multi-file edit coordinator."""

from pathlib import Path

from lilith_tools.filesystem import BatchEditTool
from lilith_tools.undo import UndoManager


class TestBatchEdit:
    """Tests for the batch_edit tool."""

    def test_preview_combined_diff_does_not_touch_files(self, tmp_path):
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_text("alpha\n", encoding="utf-8")
        b.write_text("beta\n", encoding="utf-8")

        tool = BatchEditTool()
        result = tool.execute(
            edits=[
                {"path": str(a), "old_string": "alpha", "new_string": "ALPHA"},
                {"path": str(b), "old_string": "beta", "new_string": "BETA"},
            ],
            preview=True,
        )

        assert result.success
        data = result.data
        assert data["preview"] is True
        assert a.read_text(encoding="utf-8") == "alpha\n"
        assert b.read_text(encoding="utf-8") == "beta\n"
        assert len(data["edits"]) == 2
        for edit in data["edits"]:
            assert edit["applied"] is False
            assert "diff" in edit
        assert "ALPHA" in data["combined_diff"]
        assert "BETA" in data["combined_diff"]

    def test_atomic_rollback_on_invalid_edit(self, tmp_path):
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_text("alpha\n", encoding="utf-8")
        b.write_text("beta\n", encoding="utf-8")

        # Ensure a clean undo stack before the test.
        UndoManager().clear()

        tool = BatchEditTool()
        result = tool.execute(
            edits=[
                {"path": str(a), "old_string": "alpha", "new_string": "ALPHA"},
                {"path": str(b), "old_string": "missing", "new_string": "BETA"},
            ],
            preview=False,
        )

        assert not result.success
        data = result.data
        assert data["preview"] is False
        assert data["failed_index"] == 1
        # No files should have been modified because the first edit is rolled back.
        assert a.read_text(encoding="utf-8") == "alpha\n"
        assert b.read_text(encoding="utf-8") == "beta\n"
        for edit in data["edits"]:
            assert edit["applied"] is False

    def test_apply_all_valid_edits(self, tmp_path):
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_text("alpha\n", encoding="utf-8")
        b.write_text("beta\n", encoding="utf-8")

        UndoManager().clear()

        tool = BatchEditTool()
        result = tool.execute(
            edits=[
                {"path": str(a), "old_string": "alpha", "new_string": "ALPHA"},
                {"path": str(b), "old_string": "beta", "new_string": "BETA"},
            ],
            preview=False,
        )

        assert result.success
        data = result.data
        assert data["preview"] is False
        assert a.read_text(encoding="utf-8") == "ALPHA\n"
        assert b.read_text(encoding="utf-8") == "BETA\n"
        for edit in data["edits"]:
            assert edit["applied"] is True
        assert "combined_diff" in data
