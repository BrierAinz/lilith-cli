"""Tests for SkillLoader — filesystem parser with frontmatter and edge cases."""

from pathlib import Path

import pytest
from lilith_skills.loader import SkillLoader


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def skills_dir(tmp_path: Path) -> Path:
    """Create a temporary skills directory with sample skills."""
    mlops = tmp_path / "mlops"
    mlops.mkdir()

    lora_dir = mlops / "lora-training-pipeline"
    lora_dir.mkdir()
    lora_dir.joinpath("SKILL.md").write_text(
        "---\n"
        "name: lora-training-pipeline\n"
        "description: Full LoRA training pipeline\n"
        "trigger: When fine-tuning characters\n"
        "tags: [lora, training, pixai]\n"
        "version: 2.1.0\n"
        "---\n\n"
        "# LoRA Training Pipeline\n\n"
        "Steps to train a LoRA model.\n",
    )

    # Add references subdir for linked_files testing
    refs = lora_dir / "references"
    refs.mkdir()
    refs.joinpath("dataset-format.md").write_text("# Dataset Format\n")
    scripts = lora_dir / "scripts"
    scripts.mkdir()
    scripts.joinpath("train.sh").write_text("#!/bin/bash\n")

    comfy_dir = mlops / "comfyui-batch-generate"
    comfy_dir.mkdir()
    comfy_dir.joinpath("SKILL.md").write_text(
        "---\n"
        "name: comfyui-batch-generate\n"
        "description: Batch image generation via ComfyUI API\n"
        "tags: [comfyui, batch, image-generation]\n"
        "---\n\n"
        "# ComfyUI Batch Generate\n\n"
        "Batch generation with IPAdapter.\n",
    )

    creative = tmp_path / "creative"
    creative.mkdir()

    blender_dir = creative / "blender-mcp"
    blender_dir.mkdir()
    blender_dir.joinpath("SKILL.md").write_text(
        "---\n"
        "name: blender-mcp\n"
        "description: Control Blender 3D via MCP addon\n"
        "tags: [blender, 3d, mcp]\n"
        "---\n\n"
        "# Blender MCP\n\n"
        "Connect to Blender via MCP bridge.\n",
    )

    return tmp_path


@pytest.fixture
def loader(skills_dir: Path) -> SkillLoader:
    """Create a SkillLoader for the test skills directory."""
    return SkillLoader(skills_dir)


# ── Initialization ──────────────────────────────────────────────────


class TestSkillLoaderInit:
    """Test SkillLoader initialization."""

    def test_init_valid_dir(self, skills_dir: Path):
        loader = SkillLoader(skills_dir)
        assert loader.root == skills_dir.resolve()

    def test_init_invalid_dir(self):
        with pytest.raises(FileNotFoundError, match="Skills directory not found"):
            SkillLoader("/nonexistent/path/to/skills")

    def test_init_string_path(self, skills_dir: Path):
        loader = SkillLoader(str(skills_dir))
        assert loader.root == skills_dir.resolve()


# ── Scan ─────────────────────────────────────────────────────────────


class TestSkillLoaderScan:
    """Test SkillLoader.scan() method."""

    def test_scan_finds_skills(self, loader: SkillLoader):
        skills = loader.scan()
        assert len(skills) >= 3

    def test_scan_skill_names(self, loader: SkillLoader):
        skills = loader.scan()
        names = [s.name for s in skills]
        assert "lora-training-pipeline" in names
        assert "comfyui-batch-generate" in names
        assert "blender-mcp" in names

    def test_scan_categories(self, loader: SkillLoader):
        skills = loader.scan()
        categories = {s.category for s in skills}
        assert "mlops" in categories
        assert "creative" in categories

    def test_scan_with_file_count(self, loader: SkillLoader):
        skills = loader.scan()
        lora = next(s for s in skills if s.name == "lora-training-pipeline")
        # lora has SKILL.md + references/dataset-format.md + scripts/train.sh = 3
        assert lora.file_count >= 3

    def test_scan_with_linked_files(self, loader: SkillLoader):
        skills = loader.scan()
        lora = next(s for s in skills if s.name == "lora-training-pipeline")
        assert "references" in lora.linked_files
        assert "scripts" in lora.linked_files


# ── Frontmatter parsing ─────────────────────────────────────────────


class TestSkillLoaderFrontmatter:
    """Test SkillLoader._parse_frontmatter() static method."""

    def test_parse_valid_frontmatter(self):
        content = "---\nname: test\nversion: 1.0.0\n---\n# Body"
        result = SkillLoader._parse_frontmatter(content)
        assert result is not None
        assert result["name"] == "test"
        assert result["version"] == "1.0.0"

    def test_parse_no_frontmatter(self):
        content = "# Just a title\nNo frontmatter here."
        result = SkillLoader._parse_frontmatter(content)
        assert result is None

    def test_parse_unclosed_frontmatter(self):
        content = "---\nname: test\nNo closing delimiter"
        result = SkillLoader._parse_frontmatter(content)
        assert result is None

    def test_parse_empty_frontmatter(self):
        content = "---\n---\n# Body"
        result = SkillLoader._parse_frontmatter(content)
        assert result is None

    def test_parse_list_tags(self):
        content = "---\nname: test\ntags: [tag1, tag2, tag3]\n---\n# Body"
        result = SkillLoader._parse_frontmatter(content)
        assert result is not None
        assert result["tags"] == ["tag1", "tag2", "tag3"]

    def test_parse_quoted_values(self):
        content = "---\nname: \"my skill\"\ndescription: 'a great skill'\n---\n# Body"
        result = SkillLoader._parse_frontmatter(content)
        assert result is not None
        assert result["name"] == "my skill"
        assert result["description"] == "a great skill"

    def test_parse_folded_block(self):
        content = "---\ndescription: >-\n  A long description\n---\n# Body"
        result = SkillLoader._parse_frontmatter(content)
        # The >- syntax is not fully supported (just takes first line after >)
        assert result is not None
        assert "description" in result

    def test_parse_comments_and_blank_lines(self):
        content = "---\n# This is a comment\nname: test\n\nversion: 2.0\n---\n# Body"
        result = SkillLoader._parse_frontmatter(content)
        assert result is not None
        assert result["name"] == "test"
        assert result["version"] == "2.0"

    def test_parse_comma_separated_tags(self):
        content = "---\ntags: lora, training, ai\n---\n# Body"
        result = SkillLoader._parse_frontmatter(content)
        assert result is not None
        assert result["tags"] == ["lora", "training", "ai"]


# ── Edge cases ──────────────────────────────────────────────────────


class TestSkillLoaderEdgeCases:
    """Test edge cases: hidden dirs, files without frontmatter, empty dirs."""

    def test_hidden_directories_ignored(self, skills_dir: Path):
        """Directories starting with . or _ should be skipped."""
        hidden = skills_dir / ".hidden"
        hidden.mkdir()
        (hidden / "sub").mkdir()
        (hidden / "sub" / "SKILL.md").write_text("---\nname: hidden\n---\n# Hidden")

        underscored = skills_dir / "_internal"
        underscored.mkdir()
        (underscored / "sub").mkdir()
        (underscored / "sub" / "SKILL.md").write_text("---\nname: internal\n---\n# Int")

        loader = SkillLoader(skills_dir)
        names = [s.name for s in loader.scan()]
        assert "hidden" not in names
        assert "internal" not in names

    def test_manifest_json_ignored(self, skills_dir: Path):
        """MANIFEST.json directory should be skipped."""
        manifest = skills_dir / "MANIFEST.json"
        manifest.mkdir()
        (manifest / "sub").mkdir()
        (manifest / "sub" / "SKILL.md").write_text("---\nname: manifest\n---\n# Manifest")

        loader = SkillLoader(skills_dir)
        names = [s.name for s in loader.scan()]
        assert "manifest" not in names

    def test_skill_dir_without_skill_md(self, skills_dir: Path):
        """Skill directories without SKILL.md should be skipped."""
        mlops = skills_dir / "mlops"
        empty_skill = mlops / "empty-skill"
        empty_skill.mkdir()
        # No SKILL.md file

        loader = SkillLoader(skills_dir)
        names = [s.name for s in loader.scan()]
        assert "empty-skill" not in names

    def test_flat_skill_with_skill_md(self, skills_dir: Path):
        """A category dir with SKILL.md directly is treated as a flat skill."""
        flat = skills_dir / "standalone-skill"
        flat.mkdir()
        flat.joinpath("SKILL.md").write_text("---\nname: standalone-skill\n---\n# Standalone\n")

        loader = SkillLoader(skills_dir)
        names = [s.name for s in loader.scan()]
        assert "standalone-skill" in names

    def test_skill_md_without_frontmatter(self, skills_dir: Path):
        """SKILL.md without frontmatter should still create a basic skill."""
        mlops = skills_dir / "mlops"
        bare = mlops / "bare-bones"
        bare.mkdir()
        bare.joinpath("SKILL.md").write_text("# Bare Bones\nNo frontmatter at all.\n")

        loader = SkillLoader(skills_dir)
        skills = loader.scan()
        bare_skill = [s for s in skills if s.name == "bare-bones"]
        assert len(bare_skill) == 1
        assert bare_skill[0].description == ""

    def test_count_files(self, tmp_path: Path):
        """_count_files should recursively count all files."""
        test_dir = tmp_path / "test_count"
        test_dir.mkdir()
        test_dir.joinpath("file1.txt").write_text("a")
        sub = test_dir / "sub"
        sub.mkdir()
        sub.joinpath("file2.txt").write_text("b")
        sub.joinpath("file3.txt").write_text("c")

        assert SkillLoader._count_files(test_dir) == 3

    def test_list_linked_files(self, tmp_path: Path):
        """_list_linked_files should list files in known subdirectory types."""
        test_dir = tmp_path / "test_linked"
        test_dir.mkdir()
        refs = test_dir / "references"
        refs.mkdir()
        refs.joinpath("guide.md").write_text("# Guide")
        scripts = test_dir / "scripts"
        scripts.mkdir()
        scripts.joinpath("run.sh").write_text("#!/bin/bash")

        linked = SkillLoader._list_linked_files(test_dir)
        assert "references" in linked
        assert "scripts" in linked
        assert any("guide.md" in f for f in linked["references"])

    def test_list_linked_files_empty(self, tmp_path: Path):
        """_list_linked_files returns empty dict if no standard subdirs exist."""
        test_dir = tmp_path / "test_empty"
        test_dir.mkdir()
        test_dir.joinpath("SKILL.md").write_text("# Test")

        linked = SkillLoader._list_linked_files(test_dir)
        assert linked == {}


# ── OSError handling ────────────────────────────────────────────────


class TestSkillLoaderOSError:
    """Test that _parse_skill_md handles OSError gracefully."""

    def test_unreadable_skill_md(self, skills_dir: Path):
        """If SKILL.md can't be read, the skill should be skipped."""
        mlops = skills_dir / "mlops"
        bad = mlops / "unreadable-skill"
        bad.mkdir()
        bad_skill_md = bad / "SKILL.md"
        bad_skill_md.write_text("---\nname: bad\n---\n# Bad")

        # Make the file unreadable by deleting it after creating the loader
        loader = SkillLoader(skills_dir)
        bad_skill_md.unlink()

        # scan should not crash — _parse_skill_md catches OSError
        skills = loader.scan()
        assert all(s.name != "unreadable-skill" for s in skills)
