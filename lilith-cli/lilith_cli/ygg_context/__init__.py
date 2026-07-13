"""Yggdrasil project context convention (.ygg/).

Inspired by Eter-Agents' .eter/ and Aether-Agents' .aether/ directories.
Provides structured per-project context that persists across sessions.

.ygg/ directory structure:
    .ygg/
    ├── config.yaml      # Project metadata, goals, constraints
    ├── current.md       # Current task/state (injected on session start)
    ├── log.md          # Session history and decisions
    ├── tasks.md        # Pending tasks and backlog
    └── memory/         # Project-specific memory snippets
        └── *.md

This enables agents to resume from where they left off, with full
context of the project's history and current state.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


# Default context directory name
YGG_DIR = ".ygg"
CONFIG_FILE = "config.yaml"
CURRENT_FILE = "current.md"
LOG_FILE = "log.md"
TASKS_FILE = "tasks.md"
MEMORY_DIR = "memory"


@dataclass
class YggConfig:
    """Project configuration loaded from .ygg/config.yaml."""

    name: str = ""
    description: str = ""
    goals: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    model: str | None = None
    hooks: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "YggConfig":
        """Create config from dictionary."""
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            goals=data.get("goals", []),
            constraints=data.get("constraints", []),
            model=data.get("model"),
            hooks=data.get("hooks", []),
            metadata=data.get("metadata", {}),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        result = {
            "name": self.name,
            "description": self.description,
            "goals": self.goals,
            "constraints": self.constraints,
            "hooks": self.hooks,
            "metadata": self.metadata,
        }
        if self.model:
            result["model"] = self.model
        return result


@dataclass
class YggContext:
    """Complete project context loaded from .ygg/ directory.

    Attributes:
        path: Path to the .ygg/ directory.
        config: Project configuration.
        current: Current task/state (from current.md).
        log: Session history (from log.md).
        tasks: Pending tasks (from tasks.md).
        memory: List of memory snippets from memory/ directory.
    """

    path: Path
    config: YggConfig = field(default_factory=YggConfig)
    current: str = ""
    log: str = ""
    tasks: str = ""
    memory: list[str] = field(default_factory=list)

    @property
    def exists(self) -> bool:
        """Check if .ygg/ directory exists."""
        return self.path.exists() and self.path.is_dir()

    def is_valid(self) -> bool:
        """Check if .ygg/ has at least a config.yaml."""
        if not self.exists:
            return False
        return (self.path / CONFIG_FILE).exists()

    def to_prompt_context(self) -> str:
        """Generate prompt context for agent injection.

        This formats the context as a structured prompt that can be
        injected at the start of a session (inspired by Aether's
        .aether continuity system).
        """
        parts = []

        # Project header
        if self.config.name:
            parts.append(f"# Project: {self.config.name}")
        if self.config.description:
            parts.append(f"\n{self.config.description}")

        # Goals
        if self.config.goals:
            parts.append("\n## Goals")
            for goal in self.config.goals:
                parts.append(f"- {goal}")

        # Constraints
        if self.config.constraints:
            parts.append("\n## Constraints")
            for constraint in self.config.constraints:
                parts.append(f"- {constraint}")

        # Current state
        if self.current:
            parts.append("\n## Current State")
            parts.append(self.current)

        # Pending tasks
        if self.tasks:
            parts.append("\n## Pending Tasks")
            parts.append(self.tasks)

        # Memory snippets (truncated)
        if self.memory:
            parts.append("\n## Project Memory")
            for snippet in self.memory[:3]:  # Max 3 snippets
                parts.append(f"\n{snippet[:200]}...")

        return "\n".join(parts)


# ── Loader Functions ───────────────────────────────────────────────────────────


def find_ygg_dir(start_path: Path | str | None = None) -> Path | None:
    """Find the .ygg/ directory by searching upward from start_path.

    Args:
        start_path: Starting path (default: current working directory).

    Returns:
        Path to .ygg/ if found, None otherwise.
    """
    if start_path is None:
        start_path = Path.cwd()
    else:
        start_path = Path(start_path).resolve()

    # Search upward (max 10 levels)
    current = start_path
    for _ in range(10):
        ygg_path = current / YGG_DIR
        if ygg_path.is_dir():
            return ygg_path
        parent = current.parent
        if parent == current:
            break
        current = parent

    return None


def load_ygg_context(path: Path | str | None = None) -> YggContext:
    """Load project context from .ygg/ directory.

    Args:
        path: Path to .ygg/ directory (auto-detected if None).
              Can also be a parent directory containing .ygg/.

    Returns:
        YggContext with loaded data (may be empty if not found).
    """
    if path is None:
        ygg_path = find_ygg_dir()
    else:
        path = Path(path)
        # Check if path IS the .ygg directory
        if path.name == YGG_DIR and path.is_dir():
            ygg_path = path
        else:
            # Check if .ygg is a subdirectory
            ygg_path = path / YGG_DIR
            if not ygg_path.is_dir():
                # Search upward from path
                ygg_path = find_ygg_dir(path)

    if ygg_path is None or not ygg_path.is_dir():
        return YggContext(path=Path.cwd() / YGG_DIR)

    ctx = YggContext(path=ygg_path)

    # Load config.yaml
    config_path = ygg_path / CONFIG_FILE
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            ctx.config = YggConfig.from_dict(data)
        except Exception:
            pass  # Invalid config, use defaults

    # Load current.md
    current_path = ygg_path / CURRENT_FILE
    if current_path.exists():
        ctx.current = current_path.read_text(encoding="utf-8").strip()

    # Load log.md
    log_path = ygg_path / LOG_FILE
    if log_path.exists():
        ctx.log = log_path.read_text(encoding="utf-8").strip()

    # Load tasks.md
    tasks_path = ygg_path / TASKS_FILE
    if tasks_path.exists():
        ctx.tasks = tasks_path.read_text(encoding="utf-8").strip()

    # Load memory snippets
    memory_path = ygg_path / MEMORY_DIR
    if memory_path.is_dir():
        for md_file in sorted(memory_path.glob("*.md")):
            try:
                content = md_file.read_text(encoding="utf-8").strip()
                if content:
                    ctx.memory.append(content)
            except Exception:
                continue

    return ctx


def create_ygg_context(
    path: Path | str,
    name: str = "",
    description: str = "",
    goals: list[str] | None = None,
) -> YggContext:
    """Create a new .ygg/ directory with default structure.

    Args:
        path: Where to create .ygg/
        name: Project name
        description: Project description
        goals: List of project goals

    Returns:
        YggContext for the newly created context.
    """
    ygg_path = Path(path) / YGG_DIR
    ygg_path.mkdir(parents=True, exist_ok=True)

    # Create config.yaml
    config = YggConfig(
        name=name,
        description=description,
        goals=goals or [],
    )
    config_path = ygg_path / CONFIG_FILE
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config.to_dict(), f, default_flow_style=False)

    # Create empty files
    (ygg_path / CURRENT_FILE).write_text("", encoding="utf-8")
    (ygg_path / LOG_FILE).write_text("", encoding="utf-8")
    (ygg_path / TASKS_FILE).write_text("", encoding="utf-8")

    # Create memory directory
    (ygg_path / MEMORY_DIR).mkdir(exist_ok=True)

    return load_ygg_context(ygg_path)


def update_current(path: Path | str, content: str) -> None:
    """Update the current.md file in .ygg/.

    Args:
        path: Path to .ygg/ directory or any subdirectory.
        content: New content for current.md.
    """
    path = Path(path)
    # First check if path IS the .ygg directory
    if path.name == YGG_DIR:
        ygg_path = path
    else:
        # Search for .ygg starting from this path
        ygg_path = find_ygg_dir(path)
    if ygg_path:
        (ygg_path / CURRENT_FILE).write_text(content, encoding="utf-8")


def append_log(path: Path | str, entry: str) -> None:
    """Append an entry to log.md in .ygg/.

    Args:
        path: Path to .ygg/ directory or any subdirectory.
        entry: Log entry to append (will be timestamped).
    """
    from datetime import datetime

    path = Path(path)
    # First check if path IS the .ygg directory
    if path.name == YGG_DIR:
        ygg_path = path
    else:
        # Search for .ygg starting from this path
        ygg_path = find_ygg_dir(path)
    if ygg_path:
        log_path = ygg_path / LOG_FILE
        timestamp = datetime.now().isoformat()
        new_entry = f"\n## {timestamp}\n{entry}\n"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(new_entry)
