"""Undo backup manager for file_write/file_edit tool operations.

Stores backups under ``~/.yggdrasil/undo/`` with a JSON index so the CLI
can restore the most recent change (or list pending backups) via the
``/undo`` slash command.
"""

from __future__ import annotations

import contextlib
import json
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class BackupEntry:
    """A single undo backup record."""

    original_path: str
    backup_path: str
    timestamp: float
    tool: str
    operation: str = "backup"

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_path": self.original_path,
            "backup_path": self.backup_path,
            "timestamp": self.timestamp,
            "tool": self.tool,
            "operation": self.operation,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BackupEntry":
        return cls(
            original_path=data.get("original_path", ""),
            backup_path=data.get("backup_path", ""),
            timestamp=data.get("timestamp", 0.0),
            tool=data.get("tool", ""),
            operation=data.get("operation", "backup"),
        )


class UndoManager:
    """Manages a stack of file backups for undoing destructive operations.

    Backups are stored under ``root_dir`` (default ``~/.yggdrasil/undo``).
    Each backup gets a unique subdirectory; the order is tracked in an
    append-only JSONL index file (``index.jsonl``). The most recent backup
    is restored by ``pop``.
    """

    def __init__(self, root_dir: str | Path | None = None) -> None:
        if root_dir is None:
            root_dir = Path("~/.yggdrasil/undo").expanduser()
        self.root = Path(root_dir).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_file = self.root / "index.jsonl"

    def _load_index(self) -> list[BackupEntry]:
        """Load all backup entries from the JSONL index."""
        entries: list[BackupEntry] = []
        if not self.index_file.exists():
            return entries
        try:
            for line in self.index_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                entries.append(BackupEntry.from_dict(data))
        except Exception:
            # Corrupt index: start fresh.
            entries = []
        return entries

    def _save_index(self, entries: list[BackupEntry]) -> None:
        """Persist the entries to the JSONL index."""
        lines = [json.dumps(e.to_dict(), ensure_ascii=False) for e in entries]
        self.index_file.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    def backup(self, original_path: str | Path, tool: str = "file_write") -> BackupEntry | None:
        """Back up the file at *original_path* if it exists.

        Returns the created :class:`BackupEntry`, or ``None`` if the file
        does not exist (e.g. a newly created file via ``file_write``).
        """
        src = Path(original_path).expanduser().resolve()
        if not src.exists() or not src.is_file():
            return None

        ts = time.time()
        # El nombre lleva el timestamp en ms para que el directorio siga
        # siendo legible y ordenable, pero lo crea mkdtemp: con
        # `self.root / f"{int(ts*1000)}"` dos backups del MISMO archivo
        # dentro del mismo milisegundo caian en el mismo directorio Y el
        # mismo nombre de archivo, asi que el segundo pisaba al primero.
        # Como pop() borra el directorio padre entero al restaurar, esa
        # colision destruia tambien el backup anterior y el stack de
        # /undo perdia un nivel en silencio. No se veia en Windows (las
        # operaciones de disco tardan mas de 1ms) pero si en CI Linux.
        backup_dir = Path(tempfile.mkdtemp(prefix=f"{int(ts * 1000)}-", dir=self.root))
        backup_file = backup_dir / src.name

        shutil.copy2(str(src), str(backup_file))

        entry = BackupEntry(
            original_path=str(src),
            backup_path=str(backup_file),
            timestamp=ts,
            tool=tool,
        )
        entries = self._load_index()
        entries.append(entry)
        self._save_index(entries)
        return entry

    def list(self) -> list[BackupEntry]:
        """Return all pending backups, oldest first."""
        return self._load_index()

    def pop(self) -> BackupEntry | None:
        """Restore the most recent backup and return it.

        Returns ``None`` if no backups are available.
        """
        entries = self._load_index()
        if not entries:
            return None

        entry = entries[-1]
        backup_path = Path(entry.backup_path)
        original_path = Path(entry.original_path)

        if backup_path.exists():
            original_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(backup_path), str(original_path))

        # Remove the entry and the backup dir on success.
        entries = entries[:-1]
        self._save_index(entries)
        if backup_path.exists():
            with contextlib.suppress(Exception):
                shutil.rmtree(backup_path.parent, ignore_errors=True)
        return entry

    def clear(self) -> None:
        """Remove all backups and reset the index."""
        if self.index_file.exists():
            self.index_file.unlink()
        for child in self.root.iterdir():
            if child.name == "index.jsonl":
                continue
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            elif child.is_file():
                with contextlib.suppress(Exception):
                    child.unlink()


__all__ = ["BackupEntry", "UndoManager"]
