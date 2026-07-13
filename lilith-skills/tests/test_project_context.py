"""Tests for .ygg project context convention."""

import json
import pytest
from pathlib import Path

from lilith_skills.project_context import (
    CONTEXT_FILE,
    CURRENT_FILE,
    DESIGN_FILE,
    LOG_FILE,
    RESEARCH_FILE,
    TASKS_FILE,
    LogEntry,
    ProjectContext,
    Task,
)


@pytest.fixture
def ctx(tmp_path):
    """Create a ProjectContext in a temp directory."""
    return ProjectContext(tmp_path / "myproject")


@pytest.fixture
def initialized_ctx(tmp_path):
    """Create and initialize a ProjectContext."""
    c = ProjectContext(tmp_path / "myproject")
    c.init("My Test Project")
    return c


# ── Dataclass tests ──────────────────────────────────────────────────────────


class TestLogEntry:
    """Tests for LogEntry dataclass."""

    def test_defaults(self):
        entry = LogEntry(agent="odin", action="tested")
        assert entry.detail == ""
        assert entry.timestamp  # auto-generated

    def test_to_markdown(self):
        entry = LogEntry(agent="odin", action="fixed bug", detail="issue #42")
        md = entry.to_markdown()
        assert "odin" in md
        assert "fixed bug" in md
        assert "issue #42" in md

    def test_to_markdown_no_detail(self):
        entry = LogEntry(agent="mimir", action="researched")
        md = entry.to_markdown()
        assert "mimir" in md
        assert "researched" in md
        assert ":" not in md.split("researched")[1]


class TestTask:
    """Tests for Task dataclass."""

    def test_defaults(self):
        task = Task(title="Do something")
        assert task.status == "pending"
        assert task.priority == "normal"
        assert task.assignee == ""

    def test_checkbox_pending(self):
        assert Task(title="x", status="pending").checkbox == "[ ]"

    def test_checkbox_in_progress(self):
        assert Task(title="x", status="in_progress").checkbox == "[~]"

    def test_checkbox_done(self):
        assert Task(title="x", status="done").checkbox == "[x]"

    def test_checkbox_blocked(self):
        assert Task(title="x", status="blocked").checkbox == "[!]"

    def test_to_markdown(self):
        task = Task(title="Build API", status="in_progress", assignee="adan", priority="high")
        md = task.to_markdown()
        assert "[~]" in md
        assert "Build API" in md
        assert "adan" in md
        assert "high" in md


# ── Initialization tests ─────────────────────────────────────────────────────


class TestInit:
    """Tests for ProjectContext initialization."""

    def test_init_creates_dir(self, ctx):
        assert not ctx.exists
        ctx.init("Test Project")
        assert ctx.exists
        assert (ctx.ygg_dir).is_dir()

    def test_init_creates_all_files(self, ctx):
        ctx.init("Test Project")
        for filename in [CURRENT_FILE, LOG_FILE, TASKS_FILE, DESIGN_FILE, RESEARCH_FILE, CONTEXT_FILE]:
            assert (ctx.ygg_dir / filename).exists(), f"{filename} not created"

    def test_init_context_json(self, ctx):
        ctx.init("Test Project")
        data = ctx.read_context_json()
        assert data["project_name"] == "Test Project"
        assert data["status"] == "active"
        assert data["created_at"]  # non-empty

    def test_init_uses_dir_name_if_no_name(self, tmp_path):
        ctx = ProjectContext(tmp_path / "auto-named")
        ctx.init()
        data = ctx.read_context_json()
        assert data["project_name"] == "auto-named"

    def test_init_fails_if_exists(self, initialized_ctx):
        with pytest.raises(FileExistsError, match="already exists"):
            initialized_ctx.init("Another Name")

    def test_ensure_init_noop(self, initialized_ctx):
        """ensure_init should not overwrite existing context."""
        original = initialized_ctx.read_context_json()
        initialized_ctx.ensure_init()
        after = initialized_ctx.read_context_json()
        assert original == after

    def test_ensure_init_creates(self, ctx):
        ctx.ensure_init("New Project")
        assert ctx.exists


# ── Reading tests ────────────────────────────────────────────────────────────


class TestReading:
    """Tests for reading context."""

    def test_read_file(self, initialized_ctx):
        content = initialized_ctx.read_file(CURRENT_FILE)
        assert "Current State" in content

    def test_read_file_not_found(self, initialized_ctx):
        assert initialized_ctx.read_file("nonexistent.md") == ""

    def test_read_all(self, initialized_ctx):
        text = initialized_ctx.read_all()
        assert "Project Context" in text
        assert "Current State" in text
        assert "Tasks" in text

    def test_read_all_no_ygg(self, ctx):
        assert ctx.read_all() == ""

    def test_read_context_json(self, initialized_ctx):
        data = initialized_ctx.read_context_json()
        assert isinstance(data, dict)
        assert "project_name" in data

    def test_read_context_json_empty(self, ctx):
        assert ctx.read_context_json() == {}


# ── Logging tests ────────────────────────────────────────────────────────────


class TestLogging:
    """Tests for activity logging."""

    def test_log_appends_entry(self, initialized_ctx):
        initialized_ctx.log("odin", "analyzed code", "found 3 issues")
        log = initialized_ctx.read_file(LOG_FILE)
        assert "odin" in log
        assert "analyzed code" in log
        assert "found 3 issues" in log

    def test_log_multiple_entries(self, initialized_ctx):
        initialized_ctx.log("odin", "first action")
        initialized_ctx.log("mimir", "second action")
        initialized_ctx.log("adan", "third action")
        log = initialized_ctx.read_file(LOG_FILE)
        assert "first action" in log
        assert "second action" in log
        assert "third action" in log

    def test_log_updates_timestamp(self, initialized_ctx):
        import time
        before = initialized_ctx.read_context_json()["last_updated"]
        time.sleep(0.01)
        initialized_ctx.log("odin", "action")
        after = initialized_ctx.read_context_json()["last_updated"]
        assert after >= before


# ── Task management tests ────────────────────────────────────────────────────


class TestTaskManagement:
    """Tests for task management."""

    def test_add_task(self, initialized_ctx):
        initialized_ctx.add_task("Build API", assignee="adan")
        tasks = initialized_ctx.read_file(TASKS_FILE)
        assert "Build API" in tasks
        assert "adan" in tasks
        assert "[ ]" in tasks

    def test_add_task_with_priority(self, initialized_ctx):
        initialized_ctx.add_task("Urgent fix", priority="urgent", assignee="eva")
        tasks = initialized_ctx.read_file(TASKS_FILE)
        assert "urgent" in tasks.lower()

    def test_add_multiple_tasks(self, initialized_ctx):
        initialized_ctx.add_task("Task 1")
        initialized_ctx.add_task("Task 2")
        initialized_ctx.add_task("Task 3")
        tasks = initialized_ctx.read_file(TASKS_FILE)
        assert "Task 1" in tasks
        assert "Task 2" in tasks
        assert "Task 3" in tasks

    def test_update_task_status(self, initialized_ctx):
        initialized_ctx.add_task("Implement feature")
        found = initialized_ctx.update_task_status("Implement feature", "in_progress")
        assert found
        tasks = initialized_ctx.read_file(TASKS_FILE)
        assert "[~]" in tasks

    def test_update_task_status_not_found(self, initialized_ctx):
        found = initialized_ctx.update_task_status("nonexistent", "done")
        assert not found

    def test_update_task_done_moves_to_completed(self, initialized_ctx):
        initialized_ctx.add_task("Quick task")
        initialized_ctx.update_task_status("Quick task", "done")
        tasks = initialized_ctx.read_file(TASKS_FILE)
        assert "[x]" in tasks


# ── Design and research tests ────────────────────────────────────────────────


class TestDesignAndResearch:
    """Tests for design decisions and research findings."""

    def test_add_design_decision(self, initialized_ctx):
        initialized_ctx.add_design_decision("Use FastAPI for REST", "API Layer")
        design = initialized_ctx.read_file(DESIGN_FILE)
        assert "Use FastAPI" in design
        assert "API Layer" in design

    def test_add_design_decision_no_component(self, initialized_ctx):
        initialized_ctx.add_design_decision("Use SQLite for storage")
        design = initialized_ctx.read_file(DESIGN_FILE)
        assert "Use SQLite" in design

    def test_add_research(self, initialized_ctx):
        initialized_ctx.add_research("FastAPI supports async natively", "https://fastapi.tiangolo.com")
        research = initialized_ctx.read_file(RESEARCH_FILE)
        assert "FastAPI supports async" in research
        assert "fastapi.tiangolo.com" in research


# ── Update current tests ─────────────────────────────────────────────────────


class TestUpdateCurrent:
    """Tests for updating the current state."""

    def test_update_summary(self, initialized_ctx):
        initialized_ctx.update_current(summary="Project is 50% complete")
        current = initialized_ctx.read_file(CURRENT_FILE)
        assert "50% complete" in current

    def test_update_focus(self, initialized_ctx):
        initialized_ctx.update_current(focus="Working on the API layer")
        current = initialized_ctx.read_file(CURRENT_FILE)
        assert "API layer" in current

    def test_update_status(self, initialized_ctx):
        initialized_ctx.update_current(status="wip")
        ctx = initialized_ctx.read_context_json()
        assert ctx["status"] == "wip"

    def test_update_agents(self, initialized_ctx):
        initialized_ctx.update_current(agents=["odin", "mimir", "adan"])
        current = initialized_ctx.read_file(CURRENT_FILE)
        assert "odin" in current
        assert "mimir" in current


# ── Snapshot tests ───────────────────────────────────────────────────────────


class TestSnapshot:
    """Tests for the snapshot method."""

    def test_snapshot_initialized(self, initialized_ctx):
        snap = initialized_ctx.snapshot()
        assert snap["exists"] is True
        assert "project_name" in snap
        assert "context" in snap
        assert "current" in snap

    def test_snapshot_not_initialized(self, ctx):
        snap = ctx.snapshot()
        assert snap["exists"] is False

    def test_snapshot_includes_all_files(self, initialized_ctx):
        snap = initialized_ctx.snapshot()
        assert "current" in snap
        assert "tasks" in snap
        assert "design" in snap
        assert "research" in snap
        assert "log" in snap


# ── Utilities tests ──────────────────────────────────────────────────────────


class TestUtilities:
    """Tests for utility methods."""

    def test_list_files(self, initialized_ctx):
        files = initialized_ctx.list_files()
        assert len(files) == 6  # All 6 context files

    def test_file_count(self, initialized_ctx):
        assert initialized_ctx.file_count() == 6

    def test_file_count_not_initialized(self, ctx):
        assert ctx.file_count() == 0
