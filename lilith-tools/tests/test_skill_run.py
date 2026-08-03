"""Tests for skill_run tool rendering and delegation overrides."""

from __future__ import annotations

from pathlib import Path


def test_skill_run_renders_placeholders_and_applies_overrides(monkeypatch, tmp_path: Path) -> None:
    from lilith_skills.delegation_skills import DelegationSkill, DelegationSkillRegistry
    import lilith_tools.skill_run as skill_run_mod
    from lilith_tools.base import ToolResult
    from lilith_tools.skill_run import SkillRunTool

    skills_path = tmp_path / "skills"
    DelegationSkillRegistry(skills_path, seed_defaults=False).save(
        DelegationSkill(
            name="custom",
            description="custom",
            preset="investigador-minimax",
            prompt_template="TASK={TASK}\nPROJECT={PROJECT}\nCONTEXT={CONTEXT}",
            structured=True,
            agentic=False,
            max_tokens=100,
        )
    )
    monkeypatch.setenv("YGGDRASIL_DELEGATION_SKILLS", str(skills_path))
    observed = {}

    class FakeDelegate:
        def execute(self, **kwargs):
            observed.update(kwargs)
            return ToolResult(success=True, data={"content": "ok"})

    monkeypatch.setattr(skill_run_mod, "DelegateSubagentTool", FakeDelegate)
    result = SkillRunTool().execute(
        name="custom",
        task="audit",
        project="Asgard",
        context="no network",
        overrides={"preset": "ejecutor-kimi", "agentic": True, "max_tokens": 777},
    )

    assert result.success
    assert observed == {
        "preset": "ejecutor-kimi",
        "prompt": "TASK=audit\nPROJECT=Asgard\nCONTEXT=no network",
        "structured": True,
        "agentic": True,
        "max_tokens": 777,
    }
    assert result.data["skill"] == "custom"


def test_skill_run_rejects_unknown_override(tmp_path: Path, monkeypatch) -> None:
    from lilith_tools.skill_run import SkillRunTool

    monkeypatch.setenv("YGGDRASIL_DELEGATION_SKILLS", str(tmp_path))
    result = SkillRunTool().execute(
        name="recon-repo", task="x", overrides={"workdir": "outside"}
    )
    assert not result.success
    assert "override" in result.error.lower()
