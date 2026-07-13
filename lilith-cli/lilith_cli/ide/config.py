"""Persistent configuration for the Lilith IDE."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Self

import yaml

from ..config import CONFIG_DIR


@dataclasses.dataclass
class IDEConfig:
    """Persistent settings for the Lilith IDE."""

    theme: str = "textual-dark"
    terminal_height: int = 8
    auto_reload: bool = True
    auto_reload_interval: float = 2.0
    auto_save: bool = True
    markdown_preview: bool = True
    run_on_save: str = ""  # Shell command to run after saving a file.
    open_files: list[str] = dataclasses.field(default_factory=list)  # Relative paths to reopen.
    active_file: str = ""  # Relative path of the last active tab.
    cursor_positions: dict[str, tuple[int, int]] = dataclasses.field(
        default_factory=dict
    )  # rel-path -> (row, col) zero-based.
    sidebar_width: int | None = None
    terminal_fullscreen: bool = False
    zen_mode: bool = False

    def __post_init__(self) -> None:
        """Normalise cursor positions loaded from YAML lists into tuples."""
        normalized: dict[str, tuple[int, int]] = {}
        for rel, pos in self.cursor_positions.items():
            if isinstance(pos, (list, tuple)) and len(pos) == 2:
                normalized[str(rel)] = (int(pos[0]), int(pos[1]))
        self.cursor_positions = normalized

    @classmethod
    def load(cls, path: Path | None = None) -> Self:
        file = path or (CONFIG_DIR / "ide.yaml")
        if not file.exists():
            return cls()
        try:
            data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
            return cls(**{k: v for k, v in data.items() if k in {f.name for f in dataclasses.fields(cls)}})
        except Exception:
            return cls()

    def save(self, path: Path | None = None) -> None:
        file = path or (CONFIG_DIR / "ide.yaml")
        file.parent.mkdir(parents=True, exist_ok=True)
        data = dataclasses.asdict(self)
        # YAML safe-load reads tuples as lists; store lists for portability.
        data["cursor_positions"] = {
            k: list(v) for k, v in data.get("cursor_positions", {}).items()
        }
        file.write_text(yaml.dump(data, default_flow_style=False), encoding="utf-8")
