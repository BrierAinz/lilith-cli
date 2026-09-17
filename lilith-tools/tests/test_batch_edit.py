"""Tests for the batch_edit multi-file edit coordinator."""

from pathlib import Path

import lilith_tools.filesystem as filesystem
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

    def test_batch_edit_acumula_en_el_mismo_archivo(self, tmp_path):
        path = tmp_path / "markers.txt"
        path.write_text("ALPHA\nBETA\nGAMMA\n", encoding="utf-8")

        result = BatchEditTool().execute(
            edits=[
                {"path": str(path), "old_string": "ALPHA", "new_string": "uno"},
                {"path": str(path), "old_string": "BETA", "new_string": "dos"},
                {"path": str(path), "old_string": "GAMMA", "new_string": "tres"},
            ],
            preview=False,
        )

        assert result.success
        assert path.read_text(encoding="utf-8") == "uno\ndos\ntres\n"

    def test_batch_edit_ediciones_encadenadas(self, tmp_path):
        path = tmp_path / "chain.txt"
        path.write_text("A\n", encoding="utf-8")

        result = BatchEditTool().execute(
            edits=[
                {"path": str(path), "old_string": "A", "new_string": "B"},
                {"path": str(path), "old_string": "B", "new_string": "C"},
            ],
            preview=False,
        )

        assert result.success
        assert path.read_text(encoding="utf-8") == "C\n"

    def test_batch_edit_varios_archivos_no_se_mezclan(self, tmp_path):
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_text("A1 A2\n", encoding="utf-8")
        b.write_text("B1 B2\n", encoding="utf-8")

        result = BatchEditTool().execute(
            edits=[
                {"path": str(a), "old_string": "A1", "new_string": "X1"},
                {"path": str(b), "old_string": "B1", "new_string": "Y1"},
                {"path": str(a), "old_string": "A2", "new_string": "X2"},
                {"path": str(b), "old_string": "B2", "new_string": "Y2"},
            ],
            preview=False,
        )

        assert result.success
        assert a.read_text(encoding="utf-8") == "X1 X2\n"
        assert b.read_text(encoding="utf-8") == "Y1 Y2\n"

    def test_batch_edit_rechaza_archivo_no_utf8(self, tmp_path):
        path = tmp_path / "binary.txt"
        original = b"alpha\xffbeta\n"
        path.write_bytes(original)

        result = BatchEditTool().execute(
            edits=[
                {"path": str(path), "old_string": "alpha", "new_string": "ALPHA"},
            ],
            preview=False,
        )

        assert not result.success
        assert "UTF-8" in result.error
        assert path.read_bytes() == original

    def test_batch_edit_rollback_con_ediciones_repetidas(self, tmp_path, monkeypatch):
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_text("A1 A2\n", encoding="utf-8")
        b.write_text("B1\n", encoding="utf-8")
        original_write = filesystem._write_text_exact

        def fail_second_file(path, data):
            if path.resolve() == b.resolve():
                raise OSError("fallo de escritura simulado")
            return original_write(path, data)

        UndoManager().clear()
        monkeypatch.setattr(filesystem, "_write_text_exact", fail_second_file)
        result = BatchEditTool().execute(
            edits=[
                {"path": str(a), "old_string": "A1", "new_string": "X1"},
                {"path": str(a), "old_string": "A2", "new_string": "X2"},
                {"path": str(b), "old_string": "B1", "new_string": "Y1"},
            ],
            preview=False,
        )

        assert not result.success
        assert a.read_text(encoding="utf-8") == "A1 A2\n"
        assert b.read_text(encoding="utf-8") == "B1\n"
        assert all(not edit["applied"] for edit in result.data["edits"])
