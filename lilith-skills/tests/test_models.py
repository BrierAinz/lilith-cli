"""Tests for lilith_skills.models (Skill, SkillManifest)."""
import pytest
import tempfile
from pathlib import Path

from lilith_skills.models import Skill, SkillManifest


# ── Skill ─────────────────────────────────────────────────────────


def test_skill_minimal():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "my-skill"
        path.mkdir()
        skill = Skill(name="my-skill", category="test", path=path)
        assert skill.name == "my-skill"
        assert skill.category == "test"
        assert skill.tags == []
        assert skill.version == "1.0.0"
        assert skill.file_count == 1
        assert skill.content == ""


def test_skill_full():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "full-skill"
        path.mkdir()
        skill = Skill(
            name="full-skill",
            category="mlops",
            description="A full skill",
            trigger="when training",
            tags=["ml", "training", "lora"],
            version="2.1.0",
            path=path,
            content="# Body content here",
            file_count=3,
            linked_files={"references": ["a.md"]},
        )
        assert skill.version == "2.1.0"
        assert skill.tags == ["ml", "training", "lora"]
        assert skill.file_count == 3


def test_skill_md_path():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "x"
        path.mkdir()
        skill = Skill(name="x", category="c", path=path)
        assert skill.skill_md_path == path / "SKILL.md"


def test_skill_qualified_name():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "x"
        path.mkdir()
        skill = Skill(name="my-name", category="my-cat", path=path)
        assert skill.qualified_name == "my-cat/my-name"


def test_skill_to_prompt_minimal():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "x"
        path.mkdir()
        skill = Skill(name="x", category="c", path=path, content="body text")
        prompt = skill.to_prompt()
        assert "# Skill: c/x" in prompt
        assert "body text" in prompt


def test_skill_to_prompt_with_metadata():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "x"
        path.mkdir()
        skill = Skill(
            name="x",
            category="c",
            path=path,
            description="My description",
            trigger="when testing",
            tags=["test", "qa"],
            content="body",
        )
        prompt = skill.to_prompt()
        assert "Description: My description" in prompt
        assert "Trigger: when testing" in prompt
        assert "Tags: test, qa" in prompt


def test_skill_to_prompt_max_chars_truncates():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "x"
        path.mkdir()
        skill = Skill(
            name="x", category="c", path=path, content="a" * 1000
        )
        prompt = skill.to_prompt(max_chars=100)
        # Truncation marker
        assert "..." in prompt


def test_skill_to_prompt_max_chars_unlimited():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "x"
        path.mkdir()
        skill = Skill(
            name="x", category="c", path=path, content="a" * 1000
        )
        prompt = skill.to_prompt(max_chars=-1)
        # All content included
        assert "a" * 1000 in prompt


def test_skill_to_prompt_max_chars_zero_no_body():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "x"
        path.mkdir()
        skill = Skill(
            name="x", category="c", path=path, content="body content"
        )
        prompt = skill.to_prompt(max_chars=0)
        assert "body content" not in prompt
        assert "# Skill: c/x" in prompt


def test_skill_extract_body_no_frontmatter():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "x"
        path.mkdir()
        skill = Skill(name="x", category="c", path=path, content="plain text")
        assert skill._extract_body() == "plain text"


def test_skill_extract_body_with_frontmatter():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "x"
        path.mkdir()
        content = "---\nname: x\n---\nbody content here"
        skill = Skill(name="x", category="c", path=path, content=content)
        assert skill._extract_body() == "body content here"


# ── SkillManifest ────────────────────────────────────────────────


def test_manifest_defaults():
    m = SkillManifest()
    assert m.schema_version == "yggdrasil-skills-v1"
    assert m.source == "hermes-agent"
    assert m.total_skills == 0
    assert m.total_files == 0
    assert m.categories == {}


def test_manifest_with_categories():
    m = SkillManifest(
        categories={"mlops": [], "creative": []},
        total_skills=5,
        total_files=10,
        source="yggdrasil",
    )
    assert m.total_skills == 5
    assert m.total_files == 10
    assert "mlops" in m.categories


def test_manifest_alias():
    # The 'schema' alias maps to schema_version
    m = SkillManifest.model_validate({"schema": "custom-v2", "total_skills": 1})
    assert m.schema_version == "custom-v2"
