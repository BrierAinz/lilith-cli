"""Tests for the UndoManager backup system.

Verifies that:
- FileWriteTool and FileEditTool create backups before destructive ops
- /undo pop restores the most recent backup (LIFO)
- /undo list shows pending backups
- /undo clear resets state
- Backups of non-existent files are NOT created (only overwrite/edit backs up)
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from lilith_tools.undo import BackupEntry, UndoManager


class TestUndoManager:
    """Unit tests for UndoManager — backup/pop/list/clear."""

    def _make_manager(self, tmp_path: Path) -> tuple[UndoManager, Path]:
        """Build a fresh UndoManager rooted in a temp dir."""
        mgr = UndoManager(root_dir=tmp_path)
        # Clear any pre-existing index so tests are hermetic.
        mgr.clear()
        return mgr, tmp_path

    def test_backup_creates_entry_and_file(self, tmp_path: Path) -> None:
        mgr, _ = self._make_manager(tmp_path)
        target = tmp_path / "victim.txt"
        target.write_text("original content")

        entry = mgr.backup(target, tool="file_write")
        assert entry is not None
        assert entry.original_path == str(target)
        assert entry.tool == "file_write"
        assert Path(entry.backup_path).exists()
        assert Path(entry.backup_path).read_text(encoding="utf-8") == "original content"

    def test_list_returns_pending_backups(self, tmp_path: Path) -> None:
        mgr, _ = self._make_manager(tmp_path)
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_text("a")
        b.write_text("b")

        mgr.backup(a, tool="file_write")
        mgr.backup(b, tool="file_edit")

        entries = mgr.list()
        assert len(entries) == 2
        assert entries[0].original_path == str(a)
        assert entries[1].original_path == str(b)

    def test_pop_restores_most_recent_lifo(self, tmp_path: Path) -> None:
        mgr, _ = self._make_manager(tmp_path)
        a = tmp_path / "file.txt"
        a.write_text("v1")
        mgr.backup(a, tool="file_write")
        a.write_text("v2")
        mgr.backup(a, tool="file_write")
        a.write_text("v3")

        # Pop should restore v2 (most recent backup before the v3 write)
        entry = mgr.pop()
        assert entry is not None
        assert a.read_text(encoding="utf-8") == "v2"

        # Pop again should restore v1
        entry2 = mgr.pop()
        assert entry2 is not None
        assert a.read_text(encoding="utf-8") == "v1"

    def test_two_backups_in_same_millisecond_dont_collide(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Dos backups del mismo archivo en el mismo ms son independientes.

        El directorio de backup se llamaba `int(time.time()*1000)`, asi que
        dos escrituras rapidas al mismo archivo caian en el mismo directorio
        y el mismo nombre: la segunda pisaba a la primera. Como pop() borra
        el directorio padre al restaurar, la colision se llevaba puesto
        tambien el backup anterior y /undo perdia un nivel del stack sin
        avisar. En Windows casi no se reproduce porque las operaciones de
        disco tardan mas de 1ms; congelar el reloj lo hace determinista en
        cualquier plataforma.
        """
        import lilith_tools.undo as undo_mod

        mgr, _ = self._make_manager(tmp_path)
        monkeypatch.setattr(undo_mod.time, "time", lambda: 1_700_000_000.0)

        a = tmp_path / "file.txt"
        a.write_text("v1")
        mgr.backup(a, tool="file_write")
        a.write_text("v2")
        mgr.backup(a, tool="file_write")
        a.write_text("v3")

        entries = mgr.list()
        assert len({e.backup_path for e in entries}) == 2, (
            "los dos backups comparten ruta: el segundo piso al primero"
        )

        assert mgr.pop() is not None
        assert a.read_text(encoding="utf-8") == "v2"
        assert mgr.pop() is not None
        assert a.read_text(encoding="utf-8") == "v1"

    def test_pop_empty_returns_none(self, tmp_path: Path) -> None:
        mgr, _ = self._make_manager(tmp_path)
        assert mgr.pop() is None

    def test_clear_resets_index_and_removes_files(self, tmp_path: Path) -> None:
        mgr, _ = self._make_manager(tmp_path)
        target = tmp_path / "x.txt"
        target.write_text("x")
        mgr.backup(target, tool="file_write")
        assert len(mgr.list()) == 1

        mgr.clear()
        assert len(mgr.list()) == 0
        # The root dir may still exist but should be empty.
        if mgr.root.exists():
            assert list(mgr.root.iterdir()) == []

    def test_backup_entry_roundtrip(self) -> None:
        """BackupEntry.to_dict / from_dict preserves all fields."""
        original = BackupEntry(
            original_path="/some/path",
            backup_path="/tmp/backup",
            timestamp=1234567890.0,
            tool="file_edit",
        )
        d = original.to_dict()
        restored = BackupEntry.from_dict(d)
        assert restored.original_path == original.original_path
        assert restored.backup_path == original.backup_path
        assert restored.timestamp == original.timestamp
        assert restored.tool == original.tool


class TestFileWriteUndoIntegration:
    """Verify FileWriteTool creates backups before overwriting."""

    def test_file_write_creates_backup_when_file_exists(self, tmp_path: Path) -> None:
        from lilith_tools.filesystem import FileWriteTool
        from lilith_tools.undo import UndoManager

        # Reset the global undo index so the test is hermetic.
        UndoManager().clear()

        target = tmp_path / "writable.txt"
        target.write_text("original")

        FileWriteTool().execute(path=str(target), content="new", show_diff=False)
        assert target.read_text(encoding="utf-8") == "new"

        # There should be at least one backup pending.
        entries = UndoManager().list()
        assert any(e.original_path == str(target) for e in entries)

    def test_file_write_no_backup_for_new_file(self, tmp_path: Path) -> None:
        from lilith_tools.filesystem import FileWriteTool
        from lilith_tools.undo import UndoManager

        UndoManager().clear()
        new_file = tmp_path / "fresh.txt"
        assert not new_file.exists()

        FileWriteTool().execute(path=str(new_file), content="created", show_diff=False)
        assert new_file.exists()

        # No backup should be created when the file didn't previously exist.
        entries = UndoManager().list()
        assert not any(e.original_path == str(new_file) for e in entries)


class TestFileEditUndoIntegration:
    """Verify FileEditTool creates backups before mutating."""

    def test_file_edit_creates_backup(self, tmp_path: Path) -> None:
        from lilith_tools.filesystem import FileEditTool
        from lilith_tools.undo import UndoManager

        UndoManager().clear()
        target = tmp_path / "editable.txt"
        target.write_text("hello world")

        FileEditTool().execute(
            path=str(target),
            old_string="hello",
            new_string="goodbye",
        )
        assert target.read_text(encoding="utf-8") == "goodbye world"

        entries = UndoManager().list()
        assert any(e.original_path == str(target) and e.tool == "file_edit" for e in entries)
