"""Tests for lilith_skills.registry (SkillRegistry)."""
import pytest
import tempfile
from pathlib import Path

from lilith_skills.registry import SkillRegistry


# ── Helpers ──────────────────────────────────────────────────────


def _create_skill(skills_root: Path, category: str, name: str, **frontmatter) -> Path:
    """Create a skill directory with SKILL.md frontmatter."""
    cat_dir = skills_root / category
    cat_dir.mkdir(parents=True, exist_ok=True)
    skill_dir = cat_dir / name
    skill_dir.mkdir(exist_ok=True)
    fm_lines = ["---"]
    fm_lines.append(f"name: {name}")
    fm_lines.append(f"description: {frontmatter.get('description', 'Test skill')}")
    if "tags" in frontmatter:
        fm_lines.append(f"tags: {frontmatter['tags']}")
    fm_lines.append("---")
    fm_lines.append(frontmatter.get("body", f"# {name}\n\nBody content."))
    (skill_dir / "SKILL.md").write_text("\n".join(fm_lines), encoding="utf-8")
    return skill_dir


# ── SkillRegistry ────────────────────────────────────────────────


def test_registry_loads_skills():
    with tempfile.TemporaryDirectory() as td:
        skills_root = Path(td) / "skills"
        _create_skill(skills_root, "mlops", "training-pipeline", description="Train models")
        _create_skill(skills_root, "creative", "ascii-art", description="ASCII art")
        registry = SkillRegistry(skills_root)
        assert len(registry.list_skills()) == 2


def test_registry_empty_dir():
    with tempfile.TemporaryDirectory() as td:
        skills_root = Path(td) / "skills"
        skills_root.mkdir()
        registry = SkillRegistry(skills_root)
        assert registry.list_skills() == []


def test_registry_get_by_qualified_name():
    with tempfile.TemporaryDirectory() as td:
        skills_root = Path(td) / "skills"
        _create_skill(skills_root, "mlops", "training-pipeline")
        registry = SkillRegistry(skills_root)
        skill = registry.get("mlops/training-pipeline")
        assert skill is not None
        assert skill.name == "training-pipeline"


def test_registry_get_by_name():
    with tempfile.TemporaryDirectory() as td:
        skills_root = Path(td) / "skills"
        _create_skill(skills_root, "mlops", "training-pipeline")
        registry = SkillRegistry(skills_root)
        skill = registry.get("training-pipeline")
        assert skill is not None


def test_registry_get_missing():
    with tempfile.TemporaryDirectory() as td:
        skills_root = Path(td) / "skills"
        skills_root.mkdir()
        registry = SkillRegistry(skills_root)
        assert registry.get("nonexistent") is None


def test_registry_by_category():
    with tempfile.TemporaryDirectory() as td:
        skills_root = Path(td) / "skills"
        _create_skill(skills_root, "mlops", "a")
        _create_skill(skills_root, "mlops", "b")
        _create_skill(skills_root, "creative", "c")
        registry = SkillRegistry(skills_root)
        assert len(registry.by_category("mlops")) == 2
        assert len(registry.by_category("creative")) == 1
        assert len(registry.by_category("nonexistent")) == 0


def test_registry_by_tag():
    with tempfile.TemporaryDirectory() as td:
        skills_root = Path(td) / "skills"
        _create_skill(skills_root, "a", "x", tags=["python", "ml"])
        _create_skill(skills_root, "a", "y", tags=["ml"])
        _create_skill(skills_root, "a", "z", tags=["rust"])
        registry = SkillRegistry(skills_root)
        assert len(registry.by_tag("ml")) == 2
        assert len(registry.by_tag("python")) == 1
        assert len(registry.by_tag("ML")) == 2  # case-insensitive


def test_registry_search_by_name():
    with tempfile.TemporaryDirectory() as td:
        skills_root = Path(td) / "skills"
        _create_skill(skills_root, "a", "training-pipeline")
        _create_skill(skills_root, "a", "inference-pipeline")
        _create_skill(skills_root, "a", "other")
        registry = SkillRegistry(skills_root)
        results = registry.search("pipeline")
        assert len(results) == 2
        # Both should be in results
        names = [s.name for s in results]
        assert "training-pipeline" in names
        assert "inference-pipeline" in names


def test_registry_search_by_description():
    with tempfile.TemporaryDirectory() as td:
        skills_root = Path(td) / "skills"
        _create_skill(skills_root, "a", "x", description="ComfyUI workflows")
        _create_skill(skills_root, "a", "y", description="Blender scripts")
        registry = SkillRegistry(skills_root)
        results = registry.search("comfyui")
        assert len(results) == 1
        assert results[0].name == "x"


def test_registry_search_by_tag():
    with tempfile.TemporaryDirectory() as td:
        skills_root = Path(td) / "skills"
        _create_skill(skills_root, "a", "x", tags=["blender", "3d"])
        _create_skill(skills_root, "a", "y", tags=["rust"])
        registry = SkillRegistry(skills_root)
        results = registry.search("blender")
        assert len(results) == 1


def test_registry_search_limit():
    with tempfile.TemporaryDirectory() as td:
        skills_root = Path(td) / "skills"
        for i in range(5):
            _create_skill(skills_root, "a", f"skill-{i}", description="common")
        registry = SkillRegistry(skills_root)
        results = registry.search("common", limit=3)
        assert len(results) == 3


def test_registry_search_no_results():
    with tempfile.TemporaryDirectory() as td:
        skills_root = Path(td) / "skills"
        _create_skill(skills_root, "a", "x")
        registry = SkillRegistry(skills_root)
        assert registry.search("nonexistent-xyz") == []


def test_registry_categories():
    with tempfile.TemporaryDirectory() as td:
        skills_root = Path(td) / "skills"
        _create_skill(skills_root, "zeta", "x")
        _create_skill(skills_root, "alpha", "y")
        _create_skill(skills_root, "mu", "z")
        registry = SkillRegistry(skills_root)
        cats = registry.categories()
        # Sorted alphabetically
        assert cats == ["alpha", "mu", "zeta"]


def test_registry_category_counts():
    with tempfile.TemporaryDirectory() as td:
        skills_root = Path(td) / "skills"
        _create_skill(skills_root, "a", "x")
        _create_skill(skills_root, "a", "y")
        _create_skill(skills_root, "b", "z")
        registry = SkillRegistry(skills_root)
        counts = registry.category_counts()
        assert counts == {"a": 2, "b": 1}


def test_registry_stats():
    with tempfile.TemporaryDirectory() as td:
        skills_root = Path(td) / "skills"
        _create_skill(skills_root, "a", "x")
        _create_skill(skills_root, "b", "y")
        registry = SkillRegistry(skills_root)
        s = registry.stats()
        assert s["total_skills"] == 2
        assert s["total_categories"] == 2
        assert s["categories"] == {"a": 1, "b": 1}


def test_registry_to_manifest():
    with tempfile.TemporaryDirectory() as td:
        skills_root = Path(td) / "skills"
        _create_skill(skills_root, "a", "x")
        registry = SkillRegistry(skills_root)
        manifest = registry.to_manifest()
        assert manifest.total_skills == 1
        assert "a" in manifest.categories


def test_registry_to_json():
    import json
    with tempfile.TemporaryDirectory() as td:
        skills_root = Path(td) / "skills"
        _create_skill(skills_root, "a", "x", description="Test")
        registry = SkillRegistry(skills_root)
        s = registry.to_json()
        data = json.loads(s)
        assert "categories" in data
        assert "a" in data["categories"]


def test_registry_from_repo_missing_raises():
    with tempfile.TemporaryDirectory() as td:
        with pytest.raises(FileNotFoundError):
            SkillRegistry.from_repo(td)
