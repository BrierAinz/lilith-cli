"""Tests for lilith_skills."""

from pathlib import Path

import pytest
from lilith_skills.loader import SkillLoader
from lilith_skills.models import Skill
from lilith_skills.registry import SkillRegistry


# ── Fixtures ──────────────────────────────────────────────────────────


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
        "description: Full LoRA training pipeline for character generation\n"
        "trigger: When fine-tuning character/person generation\n"
        "tags: [lora, training, ai-image-generation, pixai, kohya]\n"
        "version: 2.1.0\n"
        "---\n\n"
        "# LoRA Training Pipeline\n\n"
        "Steps to train a LoRA model.\n",
    )

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

    # refs subdir
    refs = blender_dir / "references"
    refs.mkdir()
    refs.joinpath("api.md").write_text("# Blender MCP API\n")

    return tmp_path


# ── Loader Tests ──────────────────────────────────────────────────────


class TestSkillLoader:
    """Tests for SkillLoader."""

    def test_scan_finds_all_skills(self, skills_dir: Path) -> None:
        loader = SkillLoader(skills_dir)
        skills = loader.scan()
        assert len(skills) == 3

    def test_parse_frontmatter(self, skills_dir: Path) -> None:
        loader = SkillLoader(skills_dir)
        skills = loader.scan()

        lora = next(s for s in skills if s.name == "lora-training-pipeline")
        assert lora.category == "mlops"
        assert "LoRA training" in lora.description
        assert "lora" in lora.tags
        assert lora.version == "2.1.0"

    def test_parse_without_frontmatter(self, tmp_path: Path) -> None:
        cat_dir = tmp_path / "test"
        cat_dir.mkdir()
        skill_dir = cat_dir / "basic"
        skill_dir.mkdir()
        skill_dir.joinpath("SKILL.md").write_text("# Basic Skill\n\nNo frontmatter.\n")

        loader = SkillLoader(tmp_path)
        skills = loader.scan()
        assert len(skills) == 1
        assert skills[0].name == "basic"

    def test_linked_files(self, skills_dir: Path) -> None:
        loader = SkillLoader(skills_dir)
        skills = loader.scan()

        blender = next(s for s in skills if s.name == "blender-mcp")
        assert "references" in blender.linked_files
        assert any("api.md" in f for f in blender.linked_files["references"])


# ── Registry Tests ───────────────────────────────────────────────────


class TestSkillRegistry:
    """Tests for SkillRegistry."""

    def test_list_skills(self, skills_dir: Path) -> None:
        registry = SkillRegistry(skills_dir)
        assert len(registry.list_skills()) == 3

    def test_get_by_qualified_name(self, skills_dir: Path) -> None:
        registry = SkillRegistry(skills_dir)
        skill = registry.get("mlops/lora-training-pipeline")
        assert skill is not None
        assert skill.name == "lora-training-pipeline"

    def test_get_by_plain_name(self, skills_dir: Path) -> None:
        registry = SkillRegistry(skills_dir)
        skill = registry.get("blender-mcp")
        assert skill is not None
        assert skill.category == "creative"

    def test_get_nonexistent(self, skills_dir: Path) -> None:
        registry = SkillRegistry(skills_dir)
        assert registry.get("nonexistent") is None

    def test_by_category(self, skills_dir: Path) -> None:
        registry = SkillRegistry(skills_dir)
        mlops = registry.by_category("mlops")
        assert len(mlops) == 2

    def test_by_tag(self, skills_dir: Path) -> None:
        registry = SkillRegistry(skills_dir)
        blender = registry.by_tag("blender")
        assert len(blender) == 1

    def test_search(self, skills_dir: Path) -> None:
        registry = SkillRegistry(skills_dir)
        results = registry.search("lora")
        assert len(results) >= 1
        assert any(s.name == "lora-training-pipeline" for s in results)

    def test_categories(self, skills_dir: Path) -> None:
        registry = SkillRegistry(skills_dir)
        cats = registry.categories()
        assert "mlops" in cats
        assert "creative" in cats

    def test_stats(self, skills_dir: Path) -> None:
        registry = SkillRegistry(skills_dir)
        stats = registry.stats()
        assert stats["total_skills"] == 3
        assert stats["total_categories"] == 2


# ── Model Tests ───────────────────────────────────────────────────────


class TestSkillModel:
    """Tests for Skill Pydantic model."""

    def test_qualified_name(self) -> None:
        skill = Skill(
            name="test-skill",
            category="mlops",
            path=Path("/tmp/test"),
        )
        assert skill.qualified_name == "mlops/test-skill"

    def test_to_prompt(self) -> None:
        skill = Skill(
            name="comfyui",
            category="creative",
            description="Generate images with ComfyUI",
            tags=["comfyui", "image-generation"],
            path=Path("/tmp/comfyui"),
            content="---\nname: comfyui\n---\n# ComfyUI\n\nUse comfyui skill.",
        )
        prompt = skill.to_prompt()
        assert "creative/comfyui" in prompt
        assert "comfyui" in prompt

    def test_extract_body(self) -> None:
        skill = Skill(
            name="test",
            category="test",
            path=Path("/tmp/test"),
            content="---\nname: test\n---\n# Body Content\n\nThe actual content.",
        )
        body = skill._extract_body()
        assert "Body Content" in body
        assert "name: test" not in body
