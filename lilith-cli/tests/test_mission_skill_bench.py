from lilith_cli.mission.skill_bench import SkillBench
from lilith_skills.delegation_skills import DelegationSkill


def _skill(**overrides):
    data = {
        "name": "verified-skill",
        "description": "fixture",
        "preset": "cocytus",
        "prompt_template": "Task={TASK}\nProject={PROJECT}\nContext={CONTEXT}",
        "agentic": True,
    }
    data.update(overrides)
    return DelegationSkill(**data)


def test_bench_accepts_executable_court_skill() -> None:
    result = SkillBench().run(_skill())
    assert result.passed is True
    assert result.court_agent == "cocytus"
    assert all(check.passed for check in result.checks)


def test_bench_resolves_legacy_alias_to_court_role() -> None:
    result = SkillBench().run(_skill(preset="batch-deepseek"))
    assert result.passed is True
    assert result.court_agent == "mare"


def test_bench_rejects_agentic_non_executor() -> None:
    result = SkillBench().run(_skill(preset="aura", agentic=True))
    assert result.passed is False
    check = next(row for row in result.checks if row.name == "agentic_authority")
    assert check.passed is False


def test_bench_rejects_unknown_role() -> None:
    result = SkillBench().run(_skill(preset="unknown-agent"))
    assert result.passed is False
    assert result.court_agent is None
