from pathlib import Path

from lilith_cli.mission.compute import ComputeBroker
from lilith_cli.mission.dispatch import CourtDispatcher
from lilith_cli.mission.learning import LearningEngine
from lilith_tools.base import ToolResult
from lilith_tools.orchestration_state import OrchestrationStateStore


class _AllHealthy:
    def healthy_ids(self, *, court_agent=None, probe_fabric=False):
        return {
            "experimental_labs",
            "opencode_go",
            "minimax_plus",
            "claude_pro",
            "gpt_plus",
        }


def _failed_route(store: OrchestrationStateStore, *, cause: str) -> None:
    store.append_post_mortem({
        "task_id": "fixture",
        "preset": "cocytus",
        "provider": "opencode_go",
        "success": False,
        "cause": cause,
    })


def test_repeated_same_failure_avoids_compute_resource(tmp_path: Path) -> None:
    state = tmp_path / "state.sqlite3"
    store = OrchestrationStateStore(state)
    _failed_route(store, cause="HTTP 500 while editing D:\\Work\\a.py")
    _failed_route(store, cause="HTTP 501 while editing D:\\Work\\b.py")
    learned = LearningEngine(state_path=state)
    assert learned.avoided_resources("cocytus") == {"opencode_go"}


def test_dispatcher_uses_learned_fallback(tmp_path: Path, monkeypatch) -> None:
    state = tmp_path / "state.sqlite3"
    store = OrchestrationStateStore(state)
    _failed_route(store, cause="provider timeout 1")
    _failed_route(store, cause="provider timeout 2")
    dispatcher = CourtDispatcher(state_path=state, compute=ComputeBroker(), health=_AllHealthy())

    def fake_execute(*args, **kwargs):
        return ToolResult(True, {"content": "ok", "usage": {}}, "")

    monkeypatch.setattr(dispatcher, "_execute_route", fake_execute)
    result = dispatcher.dispatch(
        "cocytus",
        "Implementa un cambio pequeno",
        mission_id="fixture-mission",
        workdir=str(tmp_path),
    )
    assert result.success
    assert result.data["compute_resource"] == "minimax_plus"
    assert result.data["court_agent"] == "cocytus"


def test_verified_patterns_auto_promote_declarative_skill(tmp_path: Path) -> None:
    state = tmp_path / "state.sqlite3"
    skills = tmp_path / "skills"
    store = OrchestrationStateStore(state)
    for index, description in enumerate(("fix parser", "add endpoint", "repair tests"), 1):
        task = store.add_task(
            f"Task {index}", description, task_id=f"t{index}", preset="cocytus"
        )
        store.append_post_mortem({
            "task_id": task["id"],
            "preset": "cocytus",
            "provider": "gpt_plus",
            "success": True,
            "quality": 1.0,
        })
    engine = LearningEngine(state_path=state, skills_root=skills)
    promoted = engine.promote_safe_patterns()
    assert len(promoted) == 1
    assert promoted[0].promoted is True
    assert promoted[0].court_agent == "cocytus"
    skill_path = Path(promoted[0].path)
    assert skill_path.is_file()
    assert "preset: cocytus" in skill_path.read_text(encoding="utf-8")


def test_skill_metrics_compare_version_success_latency_and_cost(tmp_path: Path) -> None:
    from lilith_skills.delegation_skills import DelegationSkill, DelegationSkillRegistry

    state = tmp_path / "state.sqlite3"
    skills = tmp_path / "skills"
    registry = DelegationSkillRegistry(skills, seed_defaults=False)
    base = {
        "name": "measured-skill",
        "preset": "cocytus",
        "prompt_template": "Task={TASK}\nProject={PROJECT}\nContext={CONTEXT}",
        "agentic": True,
    }
    v1 = registry.save_versioned(
        DelegationSkill(description="v1", **base), source="fixture"
    )
    v2 = registry.save_versioned(
        DelegationSkill(description="v2", **base), source="fixture"
    )
    store = OrchestrationStateStore(state)
    for success, latency, tokens, cost in (
        (True, 100, 1000, 0.10),
        (True, 300, 2000, 0.20),
    ):
        store.append_post_mortem({
            "task_id": "v1",
            "preset": "cocytus",
            "provider": "opencode_go",
            "success": success,
            "latency_ms": latency,
            "usage": {"total_tokens": tokens, "cost_usd": cost},
            "skill_name": "measured-skill",
            "skill_version_id": v1.version_id,
        })
    for success in (True, False):
        store.append_post_mortem({
            "task_id": "v2",
            "preset": "cocytus",
            "provider": "opencode_go",
            "success": success,
            "latency_ms": 400,
            "usage": {"total_tokens": 500, "cost_usd": 0.05},
            "skill_name": "measured-skill",
            "skill_version_id": v2.version_id,
        })
    rows = LearningEngine(state_path=state, skills_root=skills).skill_metrics(
        "measured-skill"
    )
    assert [row.version_id for row in rows] == [v1.version_id, v2.version_id]
    first, second = rows
    assert first.success_rate == 1.0
    assert first.avg_latency_ms == 200.0
    assert first.total_tokens == 3000
    assert first.total_cost_usd == 0.3
    assert first.active is False
    assert second.success_rate == 0.5
    assert second.success_delta == -0.5
    assert second.regression is True
    assert second.active is True


def test_skill_regression_guard_rolls_back_only_with_sufficient_evidence(tmp_path: Path) -> None:
    from lilith_skills.delegation_skills import DelegationSkill, DelegationSkillRegistry

    state = tmp_path / "state.sqlite3"
    skills = tmp_path / "skills"
    registry = DelegationSkillRegistry(skills, seed_defaults=False)
    base = {
        "name": "guarded-skill",
        "preset": "cocytus",
        "prompt_template": "Task={TASK}\nProject={PROJECT}\nContext={CONTEXT}",
        "agentic": True,
    }
    stable = registry.save_versioned(
        DelegationSkill(description="stable", **base), source="fixture"
    )
    regressed = registry.save_versioned(
        DelegationSkill(description="regressed", **base), source="fixture"
    )
    store = OrchestrationStateStore(state)
    for success in (True, True, True):
        store.append_post_mortem({
            "task_id": "stable-run",
            "preset": "cocytus",
            "provider": "opencode_go",
            "success": success,
            "skill_name": "guarded-skill",
            "skill_version_id": stable.version_id,
        })
    for success in (True, False, False):
        store.append_post_mortem({
            "task_id": "regressed-run",
            "preset": "cocytus",
            "provider": "opencode_go",
            "success": success,
            "skill_name": "guarded-skill",
            "skill_version_id": regressed.version_id,
        })
    engine = LearningEngine(state_path=state, skills_root=skills)
    actions = engine.auto_rollback_regressions()
    assert len(actions) == 1
    action = actions[0]
    assert action.rolled_back is True
    assert action.current_version == regressed.version_id
    assert action.target_version == stable.version_id
    assert action.success_drop >= 0.2
    active = next(row for row in registry.versions("guarded-skill") if row.active)
    assert active.source.startswith("rollback:auto-regression:")
    assert registry.get("guarded-skill").description == "stable"


def test_skill_regression_guard_does_not_rollback_small_samples(tmp_path: Path) -> None:
    from lilith_skills.delegation_skills import DelegationSkill, DelegationSkillRegistry

    state = tmp_path / "state.sqlite3"
    skills = tmp_path / "skills"
    registry = DelegationSkillRegistry(skills, seed_defaults=False)
    base = {
        "name": "small-sample",
        "preset": "cocytus",
        "prompt_template": "Task={TASK}\nProject={PROJECT}\nContext={CONTEXT}",
        "agentic": True,
    }
    old = registry.save_versioned(
        DelegationSkill(description="old", **base), source="fixture"
    )
    current = registry.save_versioned(
        DelegationSkill(description="current", **base), source="fixture"
    )
    store = OrchestrationStateStore(state)
    store.append_post_mortem({
        "task_id": "old-run",
        "preset": "cocytus",
        "provider": "opencode_go",
        "success": True,
        "skill_name": "small-sample",
        "skill_version_id": old.version_id,
    })
    store.append_post_mortem({
        "task_id": "current-run",
        "preset": "cocytus",
        "provider": "opencode_go",
        "success": False,
        "skill_name": "small-sample",
        "skill_version_id": current.version_id,
    })
    actions = LearningEngine(
        state_path=state, skills_root=skills
    ).auto_rollback_regressions()
    assert actions == []
    assert registry.get("small-sample").description == "current"
