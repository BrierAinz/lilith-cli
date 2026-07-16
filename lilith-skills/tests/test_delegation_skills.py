"""Tests for reusable delegation skill templates."""

from __future__ import annotations

from pathlib import Path


def test_registry_seeds_three_real_preset_skills(tmp_path: Path) -> None:
    from lilith_skills.delegation_skills import DelegationSkillRegistry

    registry = DelegationSkillRegistry(tmp_path)
    names = registry.names()
    assert names == ["batch-docs", "implementar-feature", "recon-repo"]
    assert registry.get("recon-repo").preset == "investigador-minimax"
    assert registry.get("recon-repo").structured is True
    assert registry.get("batch-docs").preset == "batch-deepseek"
    assert registry.get("implementar-feature").preset == "ejecutor-kimi"
    assert registry.get("implementar-feature").agentic is True
    assert (tmp_path / "recon-repo.yaml").exists()


def test_registry_round_trip_save_show_delete(tmp_path: Path) -> None:
    from lilith_skills.delegation_skills import DelegationSkill, DelegationSkillRegistry

    registry = DelegationSkillRegistry(tmp_path, seed_defaults=False)
    skill = DelegationSkill(
        name="review",
        description="Review code",
        preset="investigador-minimax",
        prompt_template="Review {TASK} in {PROJECT}. Context: {CONTEXT}",
        structured=True,
        max_tokens=1234,
    )
    registry.save(skill)

    loaded = DelegationSkillRegistry(tmp_path, seed_defaults=False).get("review")
    assert loaded == skill
    assert registry.delete("review") is True
    assert registry.get("review") is None
