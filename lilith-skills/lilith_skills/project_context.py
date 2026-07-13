""".ygg project context convention.

Inspired by Eter-Agents' .eter and Aether-Agents' .aether patterns.
Each project gets a .ygg/ directory with structured context files:

    .ygg/
        CURRENT.md    — Current state of the project (auto-updated)
        LOG.md        — Append-only activity log
        TASKS.md      — Task list with status
        DESIGN.md     — Architecture/design decisions
        RESEARCH.md   — Research findings and sources
        CONTEXT.json  — Machine-readable context (agents, session, metadata)

The ProjectContext class manages this directory:
    - init: Create .ygg/ with template files
    - read: Load context for injection into agent prompts
    - update: Modify context files
    - log: Append to the activity log
    - snapshot: Save a checkpoint of the current state
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


# ── Context file templates ───────────────────────────────────────────────────

CURRENT_TEMPLATE = """\
# Current State

> Auto-updated by the Yggdrasil ecosystem.
> Do not edit manually — use `ygg context update` instead.

**Project:** {project_name}
**Last updated:** {timestamp}
**Status:** {status}
**Active agents:** {agents}

## Summary

{summary}

## Current Focus

{focus}
"""

LOG_TEMPLATE = """\
# Activity Log

> Append-only log of all activity in this project.
> Each entry is timestamped with the agent that performed it.

"""

TASKS_TEMPLATE = """\
# Tasks

> Task list for this project.
> Status: [ ] pending, [~] in progress, [x] done, [!] blocked

## Tasks

- [ ] _No tasks yet_

## Completed

_None yet_
"""

DESIGN_TEMPLATE = """\
# Design

> Architecture decisions and design rationale.

## Components

_None yet_

## Decisions

_None yet_
"""

RESEARCH_TEMPLATE = """\
# Research

> Findings, sources, and references.

## Findings

_None yet_

## Sources

_None yet_
"""

CONTEXT_JSON_TEMPLATE = {
    "project_name": "",
    "created_at": "",
    "last_updated": "",
    "status": "active",
    "active_agents": [],
    "current_phase": "",
    "session_id": "",
    "metadata": {},
}

# ── Context file names ───────────────────────────────────────────────────────

CURRENT_FILE = "CURRENT.md"
LOG_FILE = "LOG.md"
TASKS_FILE = "TASKS.md"
DESIGN_FILE = "DESIGN.md"
RESEARCH_FILE = "RESEARCH.md"
CONTEXT_FILE = "CONTEXT.json"

ALL_FILES = [CURRENT_FILE, LOG_FILE, TASKS_FILE, DESIGN_FILE, RESEARCH_FILE, CONTEXT_FILE]


# ── ProjectContext ───────────────────────────────────────────────────────────


@dataclass
class LogEntry:
    """A single activity log entry.

    Attributes:
        timestamp: When the entry was created.
        agent: Name of the agent that performed the action.
        action: What was done (e.g., "fixed bug", "created file").
        detail: Additional detail about the action.
    """

    agent: str
    action: str
    detail: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_markdown(self) -> str:
        """Format as a markdown log line."""
        line = f"- **{self.timestamp}** [{self.agent}] {self.action}"
        if self.detail:
            line += f": {self.detail}"
        return line


@dataclass
class Task:
    """A single task in the task list.

    Attributes:
        title: Task title/description.
        status: Task status (pending, in_progress, done, blocked).
        priority: Task priority (low, normal, high, urgent).
        assignee: Agent assigned to this task.
    """

    title: str
    status: str = "pending"  # pending, in_progress, done, blocked
    priority: str = "normal"  # low, normal, high, urgent
    assignee: str = ""

    @property
    def checkbox(self) -> str:
        """Markdown checkbox for this task."""
        marks = {
            "pending": "[ ]",
            "in_progress": "[~]",
            "done": "[x]",
            "blocked": "[!]",
        }
        return marks.get(self.status, "[ ]")

    def to_markdown(self) -> str:
        """Format as a markdown task line."""
        line = f"- {self.checkbox} {self.title}"
        if self.assignee:
            line += f" _({self.assignee})_"
        if self.priority != "normal":
            line += f" **[{self.priority}]**"
        return line


class ProjectContext:
    """Manages a .ygg/ project context directory.

    Usage::

        ctx = ProjectContext(Path("/my/project"))
        ctx.init("My Project")

        ctx.log("odin", "analyzed codebase", "found 3 potential improvements")
        ctx.add_task("Implement feature X", assignee="adan")
        ctx.update_current(
            summary="Project is in early development",
            focus="Setting up the API layer",
        )

        # Read context for agent injection
        context_text = ctx.read_all()

    Args:
        project_root: Root directory of the project (where .ygg/ will live).
    """

    def __init__(self, project_root: Path | str) -> None:
        self.project_root = Path(project_root)
        self.ygg_dir = self.project_root / ".ygg"

    @property
    def exists(self) -> bool:
        """Whether the .ygg/ directory exists."""
        return self.ygg_dir.exists()

    # ── Initialization ──────────────────────────────────────────────────────

    def init(self, project_name: str = "") -> None:
        """Initialize the .ygg/ directory with template files.

        Args:
            project_name: Name of the project (defaults to directory name).
        """
        if self.exists:
            raise FileExistsError(f".ygg/ already exists at {self.ygg_dir}")

        name = project_name or self.project_root.name
        now = datetime.now().isoformat()

        self.ygg_dir.mkdir(parents=True)

        # Write template files
        (self.ygg_dir / CURRENT_FILE).write_text(
            CURRENT_TEMPLATE.format(
                project_name=name,
                timestamp=now,
                status="active",
                agents="none",
                summary="Project initialized.",
                focus="Getting started.",
            ),
            encoding="utf-8",
        )

        (self.ygg_dir / LOG_FILE).write_text(LOG_TEMPLATE, encoding="utf-8")
        (self.ygg_dir / TASKS_FILE).write_text(TASKS_TEMPLATE, encoding="utf-8")
        (self.ygg_dir / DESIGN_FILE).write_text(DESIGN_TEMPLATE, encoding="utf-8")
        (self.ygg_dir / RESEARCH_FILE).write_text(RESEARCH_TEMPLATE, encoding="utf-8")

        # Write CONTEXT.json
        context_data = dict(CONTEXT_JSON_TEMPLATE)
        context_data["project_name"] = name
        context_data["created_at"] = now
        context_data["last_updated"] = now
        (self.ygg_dir / CONTEXT_FILE).write_text(
            json.dumps(context_data, indent=2), encoding="utf-8"
        )

    def ensure_init(self, project_name: str = "") -> None:
        """Initialize .ygg/ if it doesn't exist. No-op if already exists."""
        if not self.exists:
            self.init(project_name)

    # ── Reading context ─────────────────────────────────────────────────────

    def read_file(self, filename: str) -> str:
        """Read a specific context file. Returns empty string if not found."""
        path = self.ygg_dir / filename
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def read_all(self) -> str:
        """Read all context files and combine them into a single text block.

        This is the main method for injecting project context into agent prompts.

        Returns:
            Combined text of all context files, or empty string if .ygg/ doesn't exist.
        """
        if not self.exists:
            return ""

        parts = [f"# Project Context: {self.project_root.name}\n"]
        for filename in [CURRENT_FILE, TASKS_FILE, DESIGN_FILE, RESEARCH_FILE, LOG_FILE]:
            content = self.read_file(filename)
            if content.strip():
                parts.append(content)
                parts.append("\n---\n")

        return "\n".join(parts)

    def read_context_json(self) -> dict[str, Any]:
        """Read the machine-readable CONTEXT.json file."""
        path = self.ygg_dir / CONTEXT_FILE
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    # ── Updating context ────────────────────────────────────────────────────

    def update_current(
        self,
        summary: str | None = None,
        focus: str | None = None,
        status: str | None = None,
        agents: list[str] | None = None,
    ) -> None:
        """Update the CURRENT.md file.

        Args:
            summary: New summary text.
            focus: New current focus text.
            status: New project status.
            agents: List of active agents.
        """
        self.ensure_init()
        ctx = self.read_context_json()
        now = datetime.now().isoformat()

        if summary is not None:
            ctx["summary"] = summary
        if focus is not None:
            ctx["focus"] = focus
        if status is not None:
            ctx["status"] = status
        if agents is not None:
            ctx["active_agents"] = agents
        ctx["last_updated"] = now

        # Write CONTEXT.json
        (self.ygg_dir / CONTEXT_FILE).write_text(
            json.dumps(ctx, indent=2), encoding="utf-8"
        )

        # Write CURRENT.md
        (self.ygg_dir / CURRENT_FILE).write_text(
            CURRENT_TEMPLATE.format(
                project_name=ctx.get("project_name", self.project_root.name),
                timestamp=now,
                status=ctx.get("status", "active"),
                agents=", ".join(ctx.get("active_agents", [])) or "none",
                summary=ctx.get("summary", "Not set."),
                focus=ctx.get("focus", "Not set."),
            ),
            encoding="utf-8",
        )

    def log(self, agent: str, action: str, detail: str = "") -> None:
        """Append an entry to the activity log.

        Args:
            agent: Name of the agent performing the action.
            action: What was done.
            detail: Additional detail.
        """
        self.ensure_init()
        entry = LogEntry(agent=agent, action=action, detail=detail)
        path = self.ygg_dir / LOG_FILE
        with path.open("a", encoding="utf-8") as f:
            f.write(entry.to_markdown() + "\n")

        # Update last_updated in CONTEXT.json
        ctx = self.read_context_json()
        ctx["last_updated"] = datetime.now().isoformat()
        (self.ygg_dir / CONTEXT_FILE).write_text(
            json.dumps(ctx, indent=2), encoding="utf-8"
        )

    def add_task(
        self,
        title: str,
        status: str = "pending",
        priority: str = "normal",
        assignee: str = "",
    ) -> None:
        """Add a task to the task list.

        Args:
            title: Task title/description.
            status: Task status (pending, in_progress, done, blocked).
            priority: Task priority (low, normal, high, urgent).
            assignee: Agent assigned to this task.
        """
        self.ensure_init()
        task = Task(title=title, status=status, priority=priority, assignee=assignee)

        # Read current tasks file and insert before "_No tasks yet_"
        path = self.ygg_dir / TASKS_FILE
        content = path.read_text(encoding="utf-8")

        task_line = task.to_markdown()
        if "_No tasks yet_" in content:
            content = content.replace("- [ ] _No tasks yet_", task_line)
        else:
            # Insert after "## Tasks\n"
            parts = content.split("## Tasks", 1)
            if len(parts) == 2:
                content = parts[0] + "## Tasks\n\n" + task_line + "\n" + parts[1]
            else:
                content += "\n" + task_line + "\n"

        path.write_text(content, encoding="utf-8")

    def update_task_status(self, title: str, new_status: str) -> bool:
        """Update the status of a task by title (partial match).

        Args:
            title: Task title to find (partial match).
            new_status: New status (pending, in_progress, done, blocked).

        Returns:
            True if the task was found and updated, False otherwise.
        """
        if not self.exists:
            return False

        path = self.ygg_dir / TASKS_FILE
        content = path.read_text(encoding="utf-8")

        # Map statuses to checkboxes
        marks = {
            "pending": "[ ]",
            "in_progress": "[~]",
            "done": "[x]",
            "blocked": "[!]",
        }
        new_mark = marks.get(new_status, "[ ]")
        old_marks = ["[ ]", "[~]", "[x]", "[!]"]

        lines = content.split("\n")
        found = False
        for i, line in enumerate(lines):
            if title.lower() in line.lower():
                for old_mark in old_marks:
                    if old_mark in line:
                        lines[i] = line.replace(old_mark, new_mark)
                        found = True
                        break
                if found:
                    break

        if found:
            # If task is done, move it to completed section
            if new_status == "done":
                # Find the line and remove it from tasks
                task_line = None
                new_lines = []
                for line in lines:
                    if title.lower() in line.lower() and new_mark in line:
                        task_line = line
                    else:
                        new_lines.append(line)
                lines = new_lines

                # Add to completed section
                if "_None yet_" in content:
                    content = "\n".join(lines).replace("_None yet_", task_line)
                else:
                    content = "\n".join(lines) + "\n" + task_line + "\n"
            else:
                content = "\n".join(lines)

            path.write_text(content, encoding="utf-8")

        return found

    def add_design_decision(self, decision: str, component: str = "") -> None:
        """Add a design decision to the DESIGN.md file.

        Args:
            decision: The design decision text.
            component: Optional component name this decision relates to.
        """
        self.ensure_init()
        path = self.ygg_dir / DESIGN_FILE
        content = path.read_text(encoding="utf-8")

        if "_None yet_" in content:
            if component:
                content = content.replace("_None yet_", f"- **{component}**: {decision}", 1)
            else:
                content = content.replace("_None yet_", f"- {decision}", 1)
        else:
            # Append to the appropriate section
            if component and "## Components" in content:
                entry = f"- **{component}**: {decision}"
                content = content.replace("## Components\n", f"## Components\n{entry}\n", 1)
            elif "## Decisions" in content:
                entry = f"- {decision}"
                content = content.replace("## Decisions\n", f"## Decisions\n{entry}\n", 1)
            else:
                content += f"\n- {decision}\n"

        path.write_text(content, encoding="utf-8")

    def add_research(self, finding: str, source: str = "") -> None:
        """Add a research finding to the RESEARCH.md file.

        Args:
            finding: The research finding text.
            source: Optional source URL or reference.
        """
        self.ensure_init()
        path = self.ygg_dir / RESEARCH_FILE
        content = path.read_text(encoding="utf-8")

        if "_None yet_" in content:
            content = content.replace("_None yet_", f"- {finding}", 1)
        else:
            if "## Findings" in content:
                content = content.replace("## Findings\n", f"## Findings\n- {finding}\n", 1)
            else:
                content += f"\n- {finding}\n"

        if source:
            if "## Sources" in content:
                content = content.replace("## Sources\n", f"## Sources\n- {source}\n", 1)

        path.write_text(content, encoding="utf-8")

    # ── Snapshot ─────────────────────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        """Return a snapshot of the current project state.

        Returns:
            Dict with all context data (files + CONTEXT.json).
        """
        if not self.exists:
            return {"exists": False}

        return {
            "exists": True,
            "project_root": str(self.project_root),
            "project_name": self.read_context_json().get("project_name", ""),
            "context": self.read_context_json(),
            "current": self.read_file(CURRENT_FILE),
            "tasks": self.read_file(TASKS_FILE),
            "design": self.read_file(DESIGN_FILE),
            "research": self.read_file(RESEARCH_FILE),
            "log": self.read_file(LOG_FILE),
        }

    # ── Utilities ────────────────────────────────────────────────────────────

    def list_files(self) -> list[Path]:
        """List all files in the .ygg/ directory."""
        if not self.exists:
            return []
        return sorted(self.ygg_dir.glob("*"))

    def file_count(self) -> int:
        """Count files in .ygg/."""
        return len(self.list_files())
