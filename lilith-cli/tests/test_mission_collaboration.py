from pathlib import Path

from lilith_cli.mission.collaboration import MissionCollaboration
from lilith_skills.delegation_skills import DelegationSkill, DelegationSkillRegistry
from lilith_tools.base import ToolResult


class FakeDispatcher:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def dispatch(self, agent, prompt, **kwargs):
        self.calls.append({"agent": agent, "prompt": prompt, **kwargs})
        return ToolResult(True, {"content": f"{agent}:{prompt}", "usage": {}}, "")


def test_conclave_uses_court_roles_and_returns_unsynthesized_rows() -> None:
    dispatcher = FakeDispatcher()
    result = MissionCollaboration(dispatcher).conclave(
        "Review this architecture.",
        agents=["demiurge", "aura", "shalltear"],
        mission_id="m1",
    )
    assert result.success
    assert result.data["agents"] == ["demiurge", "aura", "shalltear"]
    assert result.data["synthesis_required"] is True
    assert {call["agent"] for call in dispatcher.calls} == {
        "demiurge", "aura", "shalltear"
    }


def test_skill_legacy_preset_resolves_to_new_court_role(tmp_path: Path, monkeypatch) -> None:
    skills = tmp_path / "skills"
    registry = DelegationSkillRegistry(skills, seed_defaults=False)
    registry.save(DelegationSkill(
        name="legacy-batch",
        description="legacy",
        preset="batch-deepseek",
        prompt_template="Task={TASK}\nProject={PROJECT}\nContext={CONTEXT}",
    ))
    import lilith_cli.mission.collaboration as module

    monkeypatch.setattr(module, "DelegationSkillRegistry", lambda: registry)
    dispatcher = FakeDispatcher()
    result = MissionCollaboration(dispatcher).run_skill(
        "legacy-batch",
        "Normalize docs",
        project=str(tmp_path),
        context="bounded",
        mission_id="m2",
    )
    assert result.success
    assert dispatcher.calls[0]["agent"] == "mare"
    assert result.data["skill"] == "legacy-batch"
    assert result.data["skill_agent"] == "mare"


def test_versioned_skill_provenance_is_passed_to_dispatcher(tmp_path: Path, monkeypatch) -> None:
    skills = tmp_path / "skills"
    registry = DelegationSkillRegistry(skills, seed_defaults=False)
    version = registry.save_versioned(DelegationSkill(
        name="versioned-skill",
        description="versioned",
        preset="cocytus",
        prompt_template="Task={TASK}\nProject={PROJECT}\nContext={CONTEXT}",
        agentic=True,
    ), source="fixture")
    import lilith_cli.mission.collaboration as module

    monkeypatch.setattr(module, "DelegationSkillRegistry", lambda: registry)
    dispatcher = FakeDispatcher()
    result = MissionCollaboration(dispatcher).run_skill(
        "versioned-skill", "Implement change", project=str(tmp_path), mission_id="m3"
    )
    assert result.success
    assert dispatcher.calls[0]["provenance"] == {
        "skill_name": "versioned-skill",
        "skill_version_id": version.version_id,
    }
    assert result.data["skill_version_id"] == version.version_id
