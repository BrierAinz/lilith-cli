from pathlib import Path

from lilith_cli.mission.campaign import CampaignEngine, CampaignRecommendation
from lilith_cli.mission.compiler import MissionCompiler
from lilith_cli.mission.kernel import MissionKernel


def _parent(tmp_path: Path):
    state = tmp_path / "state.sqlite3"
    runs = tmp_path / "runs"
    compiled = MissionCompiler().compile(
        objective="Stabilize project",
        project_root=str(tmp_path),
        success_criteria=("Tests pass",),
    )
    kernel = MissionKernel(state_path=state, longrun_root=runs)
    registration = kernel.register(compiled, required_capabilities={"code_write"})
    return state, runs, kernel, registration


def test_campaign_chains_and_deduplicates_child_mission(tmp_path: Path) -> None:
    state, runs, _, parent = _parent(tmp_path)
    recommendation = CampaignRecommendation(
        "Improve diagnostics",
        ("Diagnostic tests pass",),
        "Useful follow-up",
        ("code_write", "review"),
        90,
    )
    engine = CampaignEngine(state_path=state, longrun_root=runs)
    first = engine.chain(parent.task_id, [recommendation])
    second = engine.chain(parent.task_id, [recommendation])
    assert len(first["created"]) == 1
    assert first["created"][0]["deduplicated"] is False
    assert second["created"][0]["deduplicated"] is True
    child = first["created"][0]["registration"]
    child_task = MissionKernel(state_path=state, longrun_root=runs).status(child["mission_id"])
    metadata = child_task["routing"]["mission_spec"]["metadata"]
    assert metadata["parent_task_id"] == parent.task_id
    assert metadata["campaign_mode"] is True


def test_campaign_external_action_stays_operator_gated(tmp_path: Path) -> None:
    state, runs, _, parent = _parent(tmp_path)
    rec = CampaignRecommendation(
        "Publish release",
        ("Release is public",),
        "Suggested next step",
        ("review",),
        100,
        "publish",
    )
    result = CampaignEngine(state_path=state, longrun_root=runs).chain(parent.task_id, [rec])
    assert result["created"] == []
    assert result["skipped"][0]["requires_operator"] is True


def test_failed_mission_auto_repairs_but_stops_at_depth_two(tmp_path: Path) -> None:
    state, runs, kernel, parent = _parent(tmp_path)
    closed = kernel.complete(
        parent.task_id,
        success=False,
        summary="Parser still fails",
        verification={"verified": False, "recoverable": True},
    )
    first = closed["campaign"]["created"][0]["registration"]
    engine = CampaignEngine(state_path=state, longrun_root=runs)
    second_result = engine.repair_failed(first["task_id"], summary="Second failure")
    second = second_result["created"][0]["registration"]
    stopped = engine.repair_failed(second["task_id"], summary="Third failure")
    assert stopped["created"] == []
    assert stopped["skipped"][0]["reason"] == "repair depth limit reached"
    task = MissionKernel(state_path=state, longrun_root=runs).status(second["mission_id"])
    assert task["routing"]["mission_spec"]["metadata"]["repair_depth"] == 2
