from pathlib import Path

import pytest
from lilith_cli.mission.compiler import MissionCompiler
from lilith_cli.mission.compute_health import ComputeHealthResolver
from lilith_cli.mission.kernel import MissionKernel
from lilith_cli.mission.model import MissionQuestion, MissionRoute


@pytest.fixture(autouse=True)
def _all_compute_healthy(monkeypatch):
    monkeypatch.setattr(
        ComputeHealthResolver,
        "healthy_ids",
        lambda self, **kwargs: {
            "experimental_labs", "opencode_go", "minimax_plus",
            "claude_pro", "gpt_plus",
        },
    )


def _compiled(root: Path):
    return MissionCompiler().compile(
        objective="Repair and verify the project",
        project_root=str(root),
        success_criteria=["tests pass", "changes are documented"],
        routes=[
            MissionRoute(
                "repair",
                "Repair in place",
                "Smallest reversible route with verification.",
            )
        ],
        recommended_route="repair",
    )


def test_ready_mission_uses_existing_orchestration_and_longrun(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    state = tmp_path / "state.sqlite3"
    runs = tmp_path / "long-runs"
    kernel = MissionKernel(state_path=state, longrun_root=runs)

    registered = kernel.register(
        _compiled(project),
        required_capabilities={"code", "security"},
        verify="pytest -q",
    )
    assert registered.status == "pendiente"
    assert registered.run_id
    assert Path(registered.run_directory).is_dir()
    assert (Path(registered.run_directory) / "contract.json").is_file()
    assert "cocytus" in registered.team
    assert "shalltear" in registered.team

    task = kernel.status(registered.mission_id)
    assert task is not None
    assert task["routing"]["kernel"] == "mission-v1"
    assert task["routing"]["longrun"]["run_id"] == registered.run_id
    assert task["routing"]["compute"]["cocytus"]["resource_id"] == "opencode_go"


def test_material_question_persists_blocked_without_starting_longrun(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    compiler = MissionCompiler()
    compiled = compiler.compile(
        objective="Migrate architecture",
        project_root=str(project),
        success_criteria=["one canonical implementation"],
        questions=[MissionQuestion(
            "¿Qué compatibilidad debe conservarse?",
            ("Total", "Gradual", "Ninguna"),
            "La respuesta cambia la migración y el riesgo.",
            recommended_option=1,
        )],
        routes=[MissionRoute("gradual", "Gradual", "Preserva reversibilidad.")],
        recommended_route="gradual",
    )
    kernel = MissionKernel(
        state_path=tmp_path / "state.sqlite3",
        longrun_root=tmp_path / "long-runs",
    )
    registered = kernel.register(compiled, required_capabilities={"architecture"})
    assert registered.status == "bloqueada"
    assert registered.run_id is None
    assert not (tmp_path / "long-runs").exists()


def test_completion_records_post_mortem_for_learning(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    kernel = MissionKernel(
        state_path=tmp_path / "state.sqlite3",
        longrun_root=tmp_path / "long-runs",
    )
    registered = kernel.register(_compiled(project), required_capabilities={"code"})
    result = kernel.complete(
        registered.task_id,
        success=True,
        summary="Repaired and verified.",
        verification={"verified": True, "tests": "pass"},
        usage={"total_tokens": 1200},
        lessons=["Inspect failing test before editing."],
    )
    assert result["task"]["status"] == "completada"
    assert result["post_mortem"]["success"] is True
    state = kernel._store().get()
    assert state["post_mortems"][-1]["lessons"] == [
        "Inspect failing test before editing."
    ]


def test_deterministic_verifier_controls_success(tmp_path: Path) -> None:
    import sys

    project = tmp_path / "project-verify-pass"
    project.mkdir()
    (project / "marker.txt").write_text("OK", encoding="utf-8")
    kernel = MissionKernel(
        state_path=tmp_path / "state-pass.sqlite3",
        longrun_root=tmp_path / "long-runs-pass",
    )
    verify = (
        f'"{sys.executable}" -c '
        '"from pathlib import Path; '
        "raise SystemExit(0 if Path('marker.txt').read_text() == 'OK' else 1)"
        '"'
    )
    registered = kernel.register(_compiled(project), verify=verify)
    result = kernel.complete(
        registered.task_id,
        success=True,
        summary="claimed success",
        verification={"claimed": True},
        campaign_mode=False,
    )
    assert result["task"]["status"] == "completada"
    deterministic = result["task"]["verification"]["deterministic"]
    assert deterministic["verified"] is True
    assert deterministic["exit_code"] == 0


def test_failed_deterministic_verifier_overrides_model_claim(tmp_path: Path) -> None:
    import sys

    project = tmp_path / "project-verify-fail"
    project.mkdir()
    (project / "marker.txt").write_text("WRONG", encoding="utf-8")
    kernel = MissionKernel(
        state_path=tmp_path / "state-fail.sqlite3",
        longrun_root=tmp_path / "long-runs-fail",
    )
    verify = (
        f'"{sys.executable}" -c '
        '"from pathlib import Path; '
        "raise SystemExit(0 if Path('marker.txt').read_text() == 'OK' else 1)"
        '"'
    )
    registered = kernel.register(_compiled(project), verify=verify)
    import pytest

    with pytest.raises(ValueError, match="deterministic verification failed"):
        kernel.complete(
            registered.task_id,
            success=True,
            summary="model claimed success",
            verification={"exact_match": True},
            campaign_mode=False,
        )
    task = kernel.status(registered.mission_id)
    assert task is not None
    assert task["status"] == "pendiente"
    deterministic = task["verification"]["deterministic"]
    assert deterministic["verified"] is False
    assert deterministic["exit_code"] == 1


def test_completion_persists_journal_compaction_outcome(tmp_path: Path, monkeypatch) -> None:
    from lilith_cli.longrun.journal import Journal

    project = tmp_path / "project-journal"
    project.mkdir()
    kernel = MissionKernel(
        state_path=tmp_path / "state-journal.sqlite3",
        longrun_root=tmp_path / "long-runs-journal",
    )
    registered = kernel.register(_compiled(project))
    journal = Journal(Path(registered.run_directory) / "journal.jsonl")
    journal.append("goal", "keep mission evidence")
    monkeypatch.setattr(
        Journal,
        "compact_storage",
        lambda self, **kwargs: {
            "compacted": True,
            "archive": "fixture-archive.jsonl",
            "original_entries": 501,
            "retained_entries": 101,
        },
    )
    result = kernel.complete(
        registered.task_id,
        success=True,
        summary="done",
        verification={"verified": True},
        campaign_mode=False,
    )
    assert result["journal_compaction"]["compacted"] is True
    task = kernel.status(registered.mission_id)
    assert task is not None
    assert (
        task["routing"]["journal_compaction"]["archive"]
        == "fixture-archive.jsonl"
    )
