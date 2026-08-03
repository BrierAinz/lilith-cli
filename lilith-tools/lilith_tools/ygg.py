"""Yggdrasil Project Context Convention (.ygg).

Inspired by Eter-Agents' .eter and Aether's .aether patterns.

This module provides the .ygg/ directory convention for per-project
structured context. When a project has a .ygg/ directory, Lilith agents
automatically load context from it at session start.

Directory structure:
    .ygg/
        CURRENT.md    # Current task/state (loaded at session start)
        LOG.md        # Activity log (appended on each agent turn)
        TASKS.md      # Pending tasks list
        DESIGN.md     # Architecture/design notes (optional)
        RESEARCH.md   # Research findings (optional)

Usage:
    from lilith_tools.ygg import YggContext

    # Auto-detect and load project context
    ctx = YggContext.discover()
    if ctx:
        current = ctx.read_current()
        ctx.log("Agent performed X")
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


# Default filenames in .ygg directory
CURRENT_FILE = "CURRENT.md"
LOG_FILE = "LOG.md"
TASKS_FILE = "TASKS.md"
DESIGN_FILE = "DESIGN.md"
RESEARCH_FILE = "RESEARCH.md"

DEFAULT_FILES = [
    CURRENT_FILE,
    LOG_FILE,
    TASKS_FILE,
    DESIGN_FILE,
    RESEARCH_FILE,
]


@dataclass
class YggFile:
    """A single file in the .ygg directory."""

    name: str
    path: Path
    content: str = ""
    exists: bool = False

    def read(self) -> str:
        """Read the file content."""
        if self.path.exists():
            self.exists = True
            self.content = self.path.read_text(encoding="utf-8")
        return self.content

    def write(self, content: str) -> None:
        """Write content to the file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(content, encoding="utf-8")
        self.content = content
        self.exists = True


@dataclass
class YggContext:
    """Project context loaded from a .ygg/ directory.

    This provides structured context injection for Lilith agents,
    similar to Eter's .eter and Aether's .aether conventions.

    Attributes:
        root: Path to the .ygg directory.
        project_name: Name of the project (derived from parent directory).
        current: CURRENT.md content.
        log: LOG.md content.
        tasks: TASKS.md content.
        design: DESIGN.md content (may be empty if not present).
        research: RESEARCH.md content (may be empty if not present).
    """

    root: Path
    project_name: str
    current: YggFile = field(init=False)
    log: YggFile = field(init=False)
    tasks: YggFile = field(init=False)
    design: YggFile = field(init=False)
    research: YggFile = field(init=False)
    _files: dict[str, YggFile] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        """Initialize YggFile instances."""
        self.current = YggFile(CURRENT_FILE, self.root / CURRENT_FILE)
        self.log = YggFile(LOG_FILE, self.root / LOG_FILE)
        self.tasks = YggFile(TASKS_FILE, self.root / TASKS_FILE)
        self.design = YggFile(DESIGN_FILE, self.root / DESIGN_FILE)
        self.research = YggFile(RESEARCH_FILE, self.root / RESEARCH_FILE)

        self._files = {
            CURRENT_FILE: self.current,
            LOG_FILE: self.log,
            TASKS_FILE: self.tasks,
            DESIGN_FILE: self.design,
            RESEARCH_FILE: self.research,
        }

    @classmethod
    def discover(
        cls, start_path: Path | None = None, stop_at: Path | None = None
    ) -> YggContext | None:
        """Discover and load a .ygg directory.

        Searches from start_path (defaults to cwd) upward until a .ygg/
        directory is found.

        Args:
            start_path: Directory to start searching from.
            stop_at: Boundary directory; the search checks it but never climbs
                above it. Pass the search root in tests to stay hermetic (so an
                auto-created ``~/.ygg`` above a temp dir is not picked up).

        Returns:
            YggContext if .ygg/ found, None otherwise.
        """
        if start_path is None:
            start_path = Path.cwd()

        # Walk upward looking for .ygg/
        current = start_path.resolve()
        if stop_at is not None:
            stop_at = stop_at.resolve()
        while True:
            ygg_dir = current / ".ygg"
            if ygg_dir.is_dir():
                return cls(
                    root=ygg_dir,
                    project_name=current.name,
                )

            if stop_at is not None and current == stop_at:
                break
            parent = current.parent
            if parent == current:
                # Reached filesystem root
                break
            current = parent

        return None

    def load_all(self) -> dict[str, str]:
        """Load all .ygg files.

        Returns:
            Dict mapping filename to content.
        """
        results = {}
        for name, file in self._files.items():
            file.read()
            results[name] = file.content
        return results

    def read_current(self) -> str:
        """Read CURRENT.md content."""
        return self.current.read()

    def write_current(self, content: str) -> None:
        """Write CURRENT.md content."""
        self.current.write(content)

    def log_entry(self, message: str, level: str = "INFO") -> None:
        """Append a timestamped entry to LOG.md.

        Args:
            message: Log message.
            level: Log level (INFO, WARN, ERROR, etc.).
        """
        timestamp = datetime.now().isoformat()
        entry = f"[{timestamp}] [{level}] {message}\n"

        # Read existing log
        existing = self.log.read()

        # Append new entry
        self.log.write(existing + entry)

    def get_tasks(self) -> list[str]:
        """Parse TASKS.md and return list of pending tasks."""
        content = self.tasks.read()
        if not content:
            return []

        tasks = []
        for line in content.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                # Remove common prefixes like - [ ], - [x], *, etc.
                cleaned = line.lstrip("-*[] ").strip()
                if cleaned:
                    tasks.append(cleaned)

        return tasks

    def add_task(self, task: str) -> None:
        """Add a task to TASKS.md."""
        existing = self.tasks.read()
        self.tasks.write(existing + f"- [ ] {task}\n")

    def complete_task(self, task: str) -> None:
        """Mark a task as complete in TASKS.md."""
        content = self.tasks.read()
        # Find and mark the task
        lines = content.splitlines()
        new_lines = []
        for line in lines:
            if task in line and "[ ]" in line:
                new_lines.append(line.replace("[ ]", "[x]"))
            else:
                new_lines.append(line)
        self.tasks.write("\n".join(new_lines))

    def get_context_summary(self) -> str:
        """Get a summary of all context for agent injection.

        This formats the .ygg content into a prompt-friendly summary
        that can be injected at session start.
        """
        parts = [f"# Project Context: {self.project_name}"]

        # Current task
        current = self.current.read()
        if current:
            parts.append(f"\n## Current Task\n{current}")

        # Pending tasks
        tasks = self.get_tasks()
        if tasks:
            parts.append(f"\n## Pending Tasks\n" + "\n".join(f"- {t}" for t in tasks))

        # Design notes
        design = self.design.read()
        if design:
            parts.append(f"\n## Design Notes\n{design}")

        # Research notes
        research = self.research.read()
        if research:
            parts.append(f"\n## Research Notes\n{research}")

        return "\n".join(parts)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for API responses."""
        return {
            "project_name": self.project_name,
            "root": str(self.root),
            "current": self.current.content,
            "tasks": self.get_tasks(),
            "design": self.design.content,
            "research": self.research.content,
            "log_entry_count": len(self.log.content.splitlines()) if self.log.content else 0,
        }


# ── Context Loader for Lilith ─────────────────────────────────────────────────


class YggLoader:
    """Loader that integrates .ygg context into Lilith sessions.

    This class can be used by lilith-orchestrator or lilith-agent
    to automatically inject .ygg context at session start.

    Usage:
        loader = YggLoader()
        context = loader.load_for_session()
        if context:
            # Inject into agent context
            pass
    """

    def __init__(
        self,
        search_paths: list[Path] | None = None,
        stop_at: Path | None = None,
    ):
        """Initialize loader.

        Args:
            search_paths: Additional paths to search for .ygg/. Defaults to cwd.
            stop_at: Boundary directory passed through to ``YggContext.discover``
                so the upward walk can be bounded (keeps tests hermetic).
        """
        self.search_paths = search_paths or [Path.cwd()]
        self.stop_at = stop_at

    def discover(self) -> YggContext | None:
        """Discover .ygg context from search paths."""
        for path in self.search_paths:
            ctx = YggContext.discover(path, stop_at=self.stop_at)
            if ctx:
                return ctx
        return None

    def load_for_session(self) -> dict[str, Any] | None:
        """Load context for a new Lilith session.

        Returns:
            Dict with context summary, or None if no .ygg found.
        """
        ctx = self.discover()
        if ctx is None:
            return None

        ctx.load_all()
        return {
            "summary": ctx.get_context_summary(),
            "details": ctx.to_dict(),
        }
