"""Realms / Worlds — persistent project memory for the Lilith IDE.

A Realm captures the long-lived context of a project: important files, coding
standards, architectural decisions and any explicit knowledge the user wants
Lilith to remember across sessions.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclasses.dataclass
class Realm:
    """Persistent memory for a single project (Realm)."""

    name: str
    root: Path
    memories: list[str] = dataclasses.field(default_factory=list)
    important_files: list[str] = dataclasses.field(default_factory=list)
    standards: list[str] = dataclasses.field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(UTC).isoformat()
        if not self.updated_at:
            self.updated_at = self.created_at

    def touch(self) -> None:
        """Update the updated_at timestamp."""
        self.updated_at = datetime.now(UTC).isoformat()

    def remember(self, text: str) -> None:
        """Add a memory if it is not already present."""
        if text not in self.memories:
            self.memories.append(text)
            self.touch()

    def forget(self, text: str) -> bool:
        """Remove a memory by exact match or index."""
        if text in self.memories:
            self.memories.remove(text)
            self.touch()
            return True
        try:
            index = int(text) - 1
            if 0 <= index < len(self.memories):
                self.memories.pop(index)
                self.touch()
                return True
        except ValueError:
            pass
        return False

    def add_important_file(self, rel_path: str) -> None:
        """Mark a project-relative path as important."""
        rel_path = rel_path.replace("\\", "/")
        if rel_path not in self.important_files:
            self.important_files.append(rel_path)
            self.touch()

    def remove_important_file(self, rel_path: str) -> bool:
        """Remove an important file entry."""
        rel_path = rel_path.replace("\\", "/")
        if rel_path in self.important_files:
            self.important_files.remove(rel_path)
            self.touch()
            return True
        return False

    def add_standard(self, text: str) -> None:
        """Add a coding standard / convention."""
        if text not in self.standards:
            self.standards.append(text)
            self.touch()

    def remove_standard(self, text: str) -> bool:
        """Remove a standard by exact match or index."""
        if text in self.standards:
            self.standards.remove(text)
            self.touch()
            return True
        try:
            index = int(text) - 1
            if 0 <= index < len(self.standards):
                self.standards.pop(index)
                self.touch()
                return True
        except ValueError:
            pass
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "root": str(self.root),
            "memories": self.memories,
            "important_files": self.important_files,
            "standards": self.standards,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Realm":
        return cls(
            name=data.get("name", "unknown"),
            root=Path(data.get("root", ".")),
            memories=list(data.get("memories", [])),
            important_files=list(data.get("important_files", [])),
            standards=list(data.get("standards", [])),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )


class RealmManager:
    """Load, save and query Realms for the current project."""

    REALM_DIR = ".yggdrasil"
    REALM_FILE = "realm.json"

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._realm: Realm | None = None

    def realm_path(self) -> Path:
        return self.root / self.REALM_DIR / self.REALM_FILE

    def load(self) -> Realm:
        """Load or create the Realm for the current project."""
        if self._realm is not None:
            return self._realm
        path = self.realm_path()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self._realm = Realm.from_dict(data)
                self._realm.root = self.root
                return self._realm
            except Exception:
                pass
        self._realm = Realm(name=self._default_name(), root=self.root)
        return self._realm

    def save(self) -> None:
        """Persist the current Realm to disk."""
        realm = self.load()
        path = self.realm_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(realm.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    def _default_name(self) -> str:
        return self.root.name or "midgard"

    def build_knowledge_prompt(self) -> str:
        """Return a structured prompt snippet with realm knowledge."""
        realm = self.load()
        sections: list[str] = []
        if realm.standards:
            sections.append(
                "[Estándares del proyecto]\n- " + "\n- ".join(realm.standards) + "\n[/Estándares]"
            )
        if realm.memories:
            sections.append(
                "[Conocimiento del proyecto]\n- " + "\n- ".join(realm.memories) + "\n[/Conocimiento]"
            )
        if realm.important_files:
            files_text = "\n".join(f"- {f}" for f in realm.important_files)
            sections.append(f"[Archivos importantes]\n{files_text}\n[/Archivos]")
        if not sections:
            return ""
        return "\n\n".join(sections)

    def auto_index(self) -> None:
        """Auto-populate important files from common project markers."""
        realm = self.load()
        candidates = [
            "README.md",
            "pyproject.toml",
            "setup.py",
            "setup.cfg",
            "package.json",
            "Cargo.toml",
            "go.mod",
            "Makefile",
            "AGENTS.md",
            ".cursorrules",
        ]
        for candidate in candidates:
            if (self.root / candidate).exists():
                realm.add_important_file(candidate)
        self.save()
