"""Tests for SkillContext — CLI integration layer."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from lilith_skills.context import SkillContext
from lilith_skills.models import Skill
from lilith_skills.registry import SkillRegistry


# --- Fixtures ---


@pytest.fixture
def mock_registry():
    """Registry with a few hand-crafted skills."""
    skills = {
        "mlops/lora-training-pipeline": Skill(
            name="lora-training-pipeline",
            category="mlops",
            description="Full LoRA training pipeline for character generation.",
            trigger="lora, train, fine-tune, finetune",
            tags=["mlops", "training", "lora", "ai"],
            version="1.0.0",
            path=Path("mlops/lora-training-pipeline/SKILL.md"),
            content="# LoRA Training Pipeline\n\nSteps to train LoRAs...",
            file_count=3,
            linked_files={"references": ["dataset-format.md"], "scripts": ["train.sh"]},
        ),
        "creative/comfyui": Skill(
            name="comfyui",
            category="creative",
            description="Generate images, video, and audio with ComfyUI.",
            trigger="comfyui, image generation, workflow",
            tags=["creative", "comfyui", "image", "generation"],
            version="1.0.0",
            path=Path("creative/comfyui/SKILL.md"),
            content="# ComfyUI\n\nInstall and run ComfyUI workflows...",
            file_count=5,
            linked_files={},
        ),
        "software-development/yggdrasil-ecosystem": Skill(
            name="yggdrasil-ecosystem",
            category="software-development",
            description="Architecture, conventions, and development patterns.",
            trigger="yggdrasil, monorepo, architecture",
            tags=["yggdrasil", "arch", "dev"],
            version="1.0.0",
            path=Path("software-development/yggdrasil-ecosystem/SKILL.md"),
            content="# Yggdrasil Ecosystem\n\n9-realms architecture...",
            file_count=2,
            linked_files={},
        ),
    }
    registry = MagicMock(spec=SkillRegistry)
    registry.search = MagicMock(
        return_value=[
            skills["mlops/lora-training-pipeline"],
            skills["creative/comfyui"],
        ],
    )
    registry.get = MagicMock(side_effect=skills.get)
    registry.stats = MagicMock(
        return_value={
            "total_skills": 3,
            "categories": {
                "mlops": 1,
                "creative": 1,
                "software-development": 1,
            },
        },
    )
    registry.by_category = MagicMock(
        side_effect=lambda cat: [s for s in skills.values() if s.category == cat],
    )
    return registry, skills


@pytest.fixture
def ctx(mock_registry):
    """SkillContext with mocked registry."""
    reg, _ = mock_registry
    with patch.object(SkillRegistry, "__init__", lambda self, path: None):
        ctx = SkillContext.__new__(SkillContext)
        ctx.registry = reg
        ctx.max_skills = 5
        ctx.max_chars = 4000
    return ctx


# --- Tests: build_context ---


def test_build_context_returns_formatted_string(ctx):
    result = ctx.build_context("train a LoRA")
    assert "# Relevant Skills" in result
    assert "LoRA Training Pipeline" in result or "lora-training-pipeline" in result


def test_build_context_includes_multiple_skills(ctx):
    result = ctx.build_context("generate image")
    assert "ComfyUI" in result or "comfyui" in result


def test_build_context_empty_results(ctx, mock_registry):
    reg, _ = mock_registry
    reg.search = MagicMock(return_value=[])
    ctx.registry = reg
    result = ctx.build_context("quantum computing")
    assert result == ""


def test_build_context_respects_char_limit(ctx, mock_registry):
    reg, skills = mock_registry
    reg.search = MagicMock(return_value=list(skills.values()))
    ctx.registry = reg
    # Very small total budget
    result = ctx.build_context("test", max_total_chars=100)
    # Should truncate or stop
    assert len(result) <= 500  # generous allowance for formatting


# --- Tests: load_skills ---


def test_load_skills_specific_names(ctx):
    result = ctx.load_skills(["mlops/lora-training-pipeline"])
    assert "# Loaded Skills" in result
    assert "lora-training-pipeline" in result


def test_load_skills_missing_skill(ctx):
    result = ctx.load_skills(["nonexistent/skill"])
    assert "not found" in result


# --- Tests: get_skill ---


def test_get_skill_found(ctx, mock_registry):
    _reg, _skills = mock_registry
    skill = ctx.get_skill("mlops/lora-training-pipeline")
    assert skill is not None
    assert skill.name == "lora-training-pipeline"


def test_get_skill_not_found(ctx, mock_registry):
    reg, _ = mock_registry
    reg.get = MagicMock(return_value=None)
    ctx.registry = reg
    skill = ctx.get_skill("nonexistent")
    assert skill is None


# --- Tests: list_available ---


def test_list_availableshows_categories(ctx):
    result = ctx.list_available()
    assert "Yggdrasil Knowledge Base" in result
    assert "3 skills" in result
    assert "mlops" in result
    assert "creative" in result
