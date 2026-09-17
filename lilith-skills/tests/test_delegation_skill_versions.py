from pathlib import Path

from lilith_skills.delegation_skills import DelegationSkill, DelegationSkillRegistry


def _skill(description: str) -> DelegationSkill:
    return DelegationSkill(
        name="versioned",
        description=description,
        preset="cocytus",
        prompt_template="Task={TASK}\nProject={PROJECT}\nContext={CONTEXT}",
        agentic=True,
    )


def test_versioned_save_and_rollback(tmp_path: Path) -> None:
    registry = DelegationSkillRegistry(tmp_path, seed_defaults=False)
    first = registry.save_versioned(_skill("v1"), source="fixture")
    second = registry.save_versioned(_skill("v2"), source="fixture")
    assert first.version_id != second.version_id
    versions = registry.versions("versioned")
    assert len(versions) == 2
    assert sum(row.active for row in versions) == 1

    restored = registry.rollback("versioned", first.version_id)
    assert registry.get("versioned").description == "v1"
    assert restored.source == f"rollback:{first.version_id}"
    after = registry.versions("versioned")
    assert len(after) == 3
    assert sum(row.active for row in after) == 1
    assert after[0].source == f"rollback:{first.version_id}"
