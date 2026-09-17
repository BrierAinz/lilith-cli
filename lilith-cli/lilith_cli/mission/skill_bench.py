from __future__ import annotations

from dataclasses import asdict, dataclass

from lilith_skills.delegation_skills import DelegationSkill

from .court import CourtRegistry


@dataclass(frozen=True)
class SkillBenchCheck:
    name: str
    passed: bool
    detail: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class SkillBenchResult:
    passed: bool
    court_agent: str | None
    checks: tuple[SkillBenchCheck, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "court_agent": self.court_agent,
            "checks": [row.as_dict() for row in self.checks],
        }


class SkillBench:
    """Deterministic promotion gate for declarative delegation skills."""

    def run(self, skill: DelegationSkill) -> SkillBenchResult:
        checks: list[SkillBenchCheck] = []
        try:
            skill.validate()
            checks.append(SkillBenchCheck("schema", True, "skill schema valid"))
        except (TypeError, ValueError) as exc:
            checks.append(SkillBenchCheck("schema", False, str(exc)))
            return SkillBenchResult(False, None, tuple(checks))

        agent = CourtRegistry.get(skill.preset)
        valid_agent = agent is not None and agent.agent_id != "lilith"
        checks.append(SkillBenchCheck(
            "court_role",
            valid_agent,
            agent.agent_id if valid_agent and agent else "unknown or prime role",
        ))
        if not valid_agent or agent is None:
            return SkillBenchResult(False, None, tuple(checks))

        rendered = skill.render("BENCH_TASK", r"D:\BENCH", "BENCH_CONTEXT")
        placeholders_gone = all(
            token not in rendered for token in ("{TASK}", "{PROJECT}", "{CONTEXT}")
        )
        checks.append(SkillBenchCheck(
            "render",
            placeholders_gone and "BENCH_TASK" in rendered,
            "placeholders resolved" if placeholders_gone else "unresolved placeholder",
        ))

        agentic_ok = not skill.agentic or agent.can_execute
        checks.append(SkillBenchCheck(
            "agentic_authority",
            agentic_ok,
            "executor role" if agentic_ok else f"{agent.agent_id} is not an executor",
        ))

        token_ok = skill.max_tokens is None or 1 <= skill.max_tokens <= 262_144
        checks.append(SkillBenchCheck(
            "token_limit",
            token_ok,
            str(skill.max_tokens) if skill.max_tokens is not None else "default",
        ))
        return SkillBenchResult(
            all(row.passed for row in checks),
            agent.agent_id,
            tuple(checks),
        )
