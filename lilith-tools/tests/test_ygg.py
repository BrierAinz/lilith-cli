"""Tests for the .ygg project context module."""

import tempfile
from pathlib import Path

import pytest

from lilith_tools.ygg import (
    YggContext,
    YggLoader,
    CURRENT_FILE,
    LOG_FILE,
    TASKS_FILE,
    DESIGN_FILE,
    RESEARCH_FILE,
)


class TestYggContext:
    """Test YggContext functionality."""

    def test_create_context(self, tmp_path):
        """Test creating a YggContext manually."""
        ygg_dir = tmp_path / ".ygg"
        ygg_dir.mkdir()

        ctx = YggContext(root=ygg_dir, project_name="test-project")

        assert ctx.root == ygg_dir
        assert ctx.project_name == "test-project"
        assert ctx.current.path == ygg_dir / CURRENT_FILE
        assert ctx.log.path == ygg_dir / LOG_FILE

    def test_discover_finds_existing(self, tmp_path):
        """Test that discover() finds an existing .ygg directory."""
        ygg_dir = tmp_path / ".ygg"
        ygg_dir.mkdir()

        # Create CURRENT.md
        (ygg_dir / CURRENT_FILE).write_text("Current task: testing")

        # Discover from parent directory
        ctx = YggContext.discover(tmp_path)

        assert ctx is not None
        assert ctx.project_name == tmp_path.name

    def test_discover_returns_none_when_missing(self, tmp_path):
        """Test that discover() returns None when no .ygg exists."""
        # stop_at keeps the upward walk hermetic: without it, a temp dir under
        # the user's home would find an auto-created ~/.ygg and this would fail.
        ctx = YggContext.discover(tmp_path, stop_at=tmp_path)
        assert ctx is None

    def test_read_write_current(self, tmp_path):
        """Test reading and writing CURRENT.md."""
        ygg_dir = tmp_path / ".ygg"
        ygg_dir.mkdir()

        ctx = YggContext(root=ygg_dir, project_name="test")

        # Initially empty
        assert ctx.read_current() == ""

        # Write content
        ctx.write_current("Build a new feature")

        # Read it back
        assert ctx.read_current() == "Build a new feature"

    def test_log_append(self, tmp_path):
        """Test logging entries to LOG.md."""
        ygg_dir = tmp_path / ".ygg"
        ygg_dir.mkdir()

        ctx = YggContext(root=ygg_dir, project_name="test")

        # Add log entries
        ctx.log_entry("Task started")
        ctx.log_entry("Task completed", level="INFO")

        log_content = ctx.log.read()
        assert "Task started" in log_content
        assert "Task completed" in log_content

    def test_tasks(self, tmp_path):
        """Test task management."""
        ygg_dir = tmp_path / ".ygg"
        ygg_dir.mkdir()

        ctx = YggContext(root=ygg_dir, project_name="test")

        # Initially empty
        assert ctx.get_tasks() == []

        # Add tasks
        ctx.add_task("Write tests")
        ctx.add_task("Implement feature")

        tasks = ctx.get_tasks()
        assert len(tasks) == 2

        # Complete a task
        ctx.complete_task("Write tests")
        tasks = ctx.get_tasks()
        # Task should be marked complete
        content = ctx.tasks.read()
        assert "[x] Write tests" in content

    def test_context_summary(self, tmp_path):
        """Test getting context summary."""
        ygg_dir = tmp_path / ".ygg"
        ygg_dir.mkdir()

        ctx = YggContext(root=ygg_dir, project_name="my-project")
        ctx.write_current("Fix bug #123")
        ctx.add_task("Reproduce issue")
        ctx.add_task("Find root cause")

        summary = ctx.get_context_summary()

        assert "my-project" in summary
        assert "Fix bug #123" in summary
        assert "Reproduce issue" in summary

    def test_to_dict(self, tmp_path):
        """Test serialization to dict."""
        ygg_dir = tmp_path / ".ygg"
        ygg_dir.mkdir()

        ctx = YggContext(root=ygg_dir, project_name="test")
        ctx.write_current("Test task")
        ctx.add_task("Test 1")

        data = ctx.to_dict()

        assert data["project_name"] == "test"
        assert data["current"] == "Test task"
        assert "Test 1" in data["tasks"]

    def test_search_upward(self, tmp_path):
        """Test that discovery searches upward through directories."""
        # Create nested structure
        nested = tmp_path / "a" / "b" / "c"
        nested.mkdir(parents=True)

        ygg_dir = tmp_path / ".ygg"
        ygg_dir.mkdir()

        (ygg_dir / CURRENT_FILE).write_text("Root context")

        # Discover from nested directory should find root .ygg
        ctx = YggContext.discover(nested)

        assert ctx is not None
        assert ctx.project_name == tmp_path.name


class TestYggLoader:
    """Test YggLoader integration."""

    def test_discover_with_search_paths(self, tmp_path):
        """Test loader with custom search paths."""
        ygg_dir = tmp_path / ".ygg"
        ygg_dir.mkdir()
        (ygg_dir / CURRENT_FILE).write_text("Found it!")

        loader = YggLoader(search_paths=[tmp_path])
        ctx = loader.discover()

        assert ctx is not None

    def test_load_for_session(self, tmp_path):
        """Test loading context for a session."""
        ygg_dir = tmp_path / ".ygg"
        ygg_dir.mkdir()
        (ygg_dir / CURRENT_FILE).write_text("Session task")

        loader = YggLoader(search_paths=[tmp_path])
        result = loader.load_for_session()

        assert result is not None
        assert "summary" in result
        assert "details" in result
        assert "Session task" in result["summary"]

    def test_load_returns_none_when_no_context(self, tmp_path):
        """Test that load returns None when no .ygg found."""
        # stop_at bounds the search to tmp_path so a real ~/.ygg above it
        # (auto-created by the runtime) does not leak into this test.
        loader = YggLoader(search_paths=[tmp_path], stop_at=tmp_path)
        result = loader.load_for_session()
        assert result is None
