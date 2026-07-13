"""Tests for ygg_context module."""

import tempfile
from pathlib import Path

import pytest

from lilith_cli.ygg_context import (
    YGG_DIR,
    CONFIG_FILE,
    CURRENT_FILE,
    LOG_FILE,
    TASKS_FILE,
    MEMORY_DIR,
    YggConfig,
    YggContext,
    find_ygg_dir,
    load_ygg_context,
    create_ygg_context,
    update_current,
    append_log,
)


class TestYggConfig:
    """Tests for YggConfig dataclass."""

    def test_default_config(self):
        """Test default config values."""
        config = YggConfig()
        assert config.name == ""
        assert config.description == ""
        assert config.goals == []
        assert config.constraints == []
        assert config.model is None

    def test_from_dict(self):
        """Test config creation from dictionary."""
        data = {
            "name": "Test Project",
            "description": "A test project",
            "goals": ["goal1", "goal2"],
            "constraints": ["constraint1"],
            "model": "glm-5.2",
        }
        config = YggConfig.from_dict(data)
        assert config.name == "Test Project"
        assert config.description == "A test project"
        assert config.goals == ["goal1", "goal2"]
        assert config.constraints == ["constraint1"]
        assert config.model == "glm-5.2"

    def test_to_dict(self):
        """Test config serialization."""
        config = YggConfig(
            name="Test",
            goals=["goal1"],
            constraints=["const1"],
            model="test-model",
        )
        data = config.to_dict()
        assert data["name"] == "Test"
        assert data["goals"] == ["goal1"]
        assert data["constraints"] == ["const1"]
        assert data["model"] == "test-model"


class TestYggContext:
    """Tests for YggContext."""

    def test_empty_context(self):
        """Test empty context defaults."""
        ctx = YggContext(path=Path("/fake/.ygg"))
        assert ctx.path == Path("/fake/.ygg")
        assert ctx.exists is False
        assert ctx.is_valid() is False
        assert ctx.to_prompt_context() == ""

    def test_to_prompt_context(self):
        """Test prompt context generation."""
        ctx = YggContext(
            path=Path("/fake/.ygg"),
            config=YggConfig(
                name="My Project",
                description="A test project",
                goals=["Build something", "Test it"],
                constraints=["No bugs"],
            ),
            current="Working on feature X",
            tasks="- Fix bug\n- Add tests",
        )
        prompt = ctx.to_prompt_context()
        assert "My Project" in prompt
        assert "A test project" in prompt
        assert "Build something" in prompt
        assert "No bugs" in prompt
        assert "Working on feature X" in prompt


class TestFindYggDir:
    """Tests for find_ygg_dir function."""

    def test_find_in_subdirectory(self, tmp_path):
        """Test finding .ygg in a subdirectory."""
        ygg = tmp_path / ".ygg"
        ygg.mkdir()

        subdir = tmp_path / "subdir"
        subdir.mkdir()

        result = find_ygg_dir(subdir)
        assert result == ygg

    def test_not_found(self, tmp_path):
        """Test when .ygg doesn't exist."""
        result = find_ygg_dir(tmp_path)
        assert result is None


class TestLoadYggContext:
    """Tests for load_ygg_context function."""

    def test_load_empty_dir(self, tmp_path):
        """Test loading from empty directory."""
        ygg = tmp_path / ".ygg"
        ygg.mkdir()

        ctx = load_ygg_context(ygg)
        assert ctx.path == ygg
        assert ctx.config.name == ""
        assert ctx.current == ""

    def test_load_with_config(self, tmp_path):
        """Test loading config.yaml."""
        ygg = tmp_path / ".ygg"
        ygg.mkdir()

        config_content = """
name: Loaded Project
description: Loaded via test
goals:
  - Test goal
model: test-model
"""
        (ygg / CONFIG_FILE).write_text(config_content)

        ctx = load_ygg_context(ygg)
        assert ctx.config.name == "Loaded Project"
        assert ctx.config.goals == ["Test goal"]
        assert ctx.config.model == "test-model"

    def test_load_current_md(self, tmp_path):
        """Test loading current.md."""
        ygg = tmp_path / ".ygg"
        ygg.mkdir()

        (ygg / CURRENT_FILE).write_text("Current task: testing")

        ctx = load_ygg_context(ygg)
        assert ctx.current == "Current task: testing"

    def test_load_log_md(self, tmp_path):
        """Test loading log.md."""
        ygg = tmp_path / ".ygg"
        ygg.mkdir()

        (ygg / LOG_FILE).write_text("## 2026-01-01\nTest log entry")

        ctx = load_ygg_context(ygg)
        assert "Test log entry" in ctx.log

    def test_load_memory_snippets(self, tmp_path):
        """Test loading memory snippets."""
        ygg = tmp_path / ".ygg"
        ygg.mkdir()

        memory_dir = ygg / MEMORY_DIR
        memory_dir.mkdir()

        (memory_dir / "snippet1.md").write_text("Memory 1")
        (memory_dir / "snippet2.md").write_text("Memory 2")

        ctx = load_ygg_context(ygg)
        assert len(ctx.memory) == 2
        assert "Memory 1" in ctx.memory[0] or ctx.memory[0] == "Memory 1"


class TestCreateYggContext:
    """Tests for create_ygg_context function."""

    def test_create_basic(self, tmp_path):
        """Test creating basic .ygg structure."""
        ctx = create_ygg_context(
            tmp_path,
            name="New Project",
            description="A new project",
            goals=["Goal 1", "Goal 2"],
        )

        assert ctx.exists
        assert ctx.is_valid()
        assert ctx.config.name == "New Project"
        assert ctx.config.description == "A new project"
        assert ctx.config.goals == ["Goal 1", "Goal 2"]

    def test_create_creates_files(self, tmp_path):
        """Test that create_ygg_context creates all files."""
        create_ygg_context(tmp_path, name="Test")

        ygg = tmp_path / YGG_DIR
        assert (ygg / CONFIG_FILE).exists()
        assert (ygg / CURRENT_FILE).exists()
        assert (ygg / LOG_FILE).exists()
        assert (ygg / TASKS_FILE).exists()
        assert (ygg / MEMORY_DIR).is_dir()


class TestUpdateFunctions:
    """Tests for update_current and append_log functions."""

    def test_update_current(self, tmp_path):
        """Test updating current.md."""
        ygg = tmp_path / ".ygg"
        ygg.mkdir()
        (ygg / CURRENT_FILE).write_text("old content")

        update_current(ygg, "new content")

        assert (ygg / CURRENT_FILE).read_text() == "new content"

    def test_append_log(self, tmp_path):
        """Test appending to log.md."""
        ygg = tmp_path / ".ygg"
        ygg.mkdir()

        append_log(ygg, "First entry")

        content = (ygg / LOG_FILE).read_text()
        assert "First entry" in content
        assert "2026" in content or "20" in content  # Has timestamp

    def test_append_multiple_logs(self, tmp_path):
        """Test multiple log entries."""
        ygg = tmp_path / ".ygg"
        ygg.mkdir()

        append_log(ygg, "Entry 1")
        append_log(ygg, "Entry 2")

        content = (ygg / LOG_FILE).read_text()
        assert "Entry 1" in content
        assert "Entry 2" in content


class TestIntegration:
    """Integration tests for the full workflow."""

    def test_full_workflow(self, tmp_path):
        """Test creating, loading, updating workflow."""
        # Create context
        ctx = create_ygg_context(
            tmp_path,
            name="Workflow Test",
            description="Testing full workflow",
            goals=["Complete workflow"],
        )
        assert ctx.is_valid()

        # Update current
        update_current(tmp_path, "Working on task 1")

        # Reload and verify
        ctx2 = load_ygg_context(tmp_path)
        assert ctx2.config.name == "Workflow Test"
        assert ctx2.current == "Working on task 1"

        # Append log
        append_log(tmp_path, "Completed task 1")

        # Reload log
        ctx3 = load_ygg_context(tmp_path)
        assert "Completed task 1" in ctx3.log

    def test_prompt_context_includes_all(self, tmp_path):
        """Test that prompt context includes all fields."""
        create_ygg_context(
            tmp_path,
            name="Prompt Test",
            description="Testing prompt generation",
            goals=["Goal A", "Goal B"],
        )

        update_current(tmp_path, "Current: Feature X")
        append_log(tmp_path, "Session started")

        ctx = load_ygg_context(tmp_path)
        prompt = ctx.to_prompt_context()

        assert "Prompt Test" in prompt
        assert "Testing prompt generation" in prompt
        assert "Goal A" in prompt
        assert "Goal B" in prompt
        assert "Current: Feature X" in prompt
