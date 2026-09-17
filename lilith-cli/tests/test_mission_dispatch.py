from pathlib import Path

from lilith_cli.mission.dispatch import CourtDispatcher
from lilith_tools.base import ToolResult


class AllHealthy:
    def healthy_ids(self, *, court_agent=None, probe_fabric=False):
        return {
            "experimental_labs",
            "opencode_go",
            "minimax_plus",
            "claude_pro",
            "gpt_plus",
        }


def test_cocytus_routes_through_broker_and_hides_legacy_identity(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    dispatcher = CourtDispatcher(state_path=tmp_path / "state.sqlite3", health=AllHealthy())

    def fake_execute(*args, **kwargs):
        return ToolResult(True, {"agent": "Vor", "content": "done", "usage": {}}, "")

    monkeypatch.setattr(dispatcher, "_execute_route", fake_execute)
    result = dispatcher.dispatch(
        "cocytus",
        "Fix the failing test.",
        mission_id="mission-1",
        workdir=str(project),
        agentic=False,
    )
    assert result.success
    assert result.data["court_agent"] == "cocytus"
    assert result.data["compute_resource"] == "opencode_go"
    assert result.data["backend_legacy_name"] == "Vor"
    assert "agent" not in result.data
    assert result.data["post_mortem"]["preset"] == "cocytus"


def test_sebas_forces_privileged_cli_resource(tmp_path: Path, monkeypatch) -> None:
    dispatcher = CourtDispatcher(state_path=tmp_path / "state.sqlite3", health=AllHealthy())
    seen = {}

    def fake_execute(agent, route, *args, **kwargs):
        seen["agent"] = agent.agent_id
        seen["resource"] = route.resource.resource_id
        seen["adapter"] = route.resource.adapter
        return ToolResult(True, {"content": "ok", "usage": {}}, "")

    monkeypatch.setattr(dispatcher, "_execute_route", fake_execute)
    result = dispatcher.dispatch("sebas", "Inspect system service state.", mission_id="m2")
    assert result.success
    assert seen == {
        "agent": "sebas",
        "resource": "claude_pro",
        "adapter": "claude_code",
    }


def test_completed_dispatch_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    dispatcher = CourtDispatcher(state_path=tmp_path / "state.sqlite3", health=AllHealthy())
    calls = {"count": 0}

    def fake_execute(*args, **kwargs):
        calls["count"] += 1
        return ToolResult(True, {"content": "done", "usage": {}}, "")

    monkeypatch.setattr(dispatcher, "_execute_route", fake_execute)
    first = dispatcher.dispatch("aura", "Map the codebase.", mission_id="m3")
    second = dispatcher.dispatch("aura", "Map the codebase.", mission_id="m3")
    assert first.success and second.success
    assert second.data["deduplicated"] is True
    assert calls["count"] == 1


class OnlyHealthy:
    def __init__(self, ids):
        self.ids = set(ids)

    def healthy_ids(self, *, court_agent=None, probe_fabric=False):
        return set(self.ids)


def test_dispatch_falls_back_when_primary_compute_is_unhealthy(tmp_path: Path, monkeypatch) -> None:
    dispatcher = CourtDispatcher(
        state_path=tmp_path / "state.sqlite3",
        health=OnlyHealthy({"gpt_plus"}),
    )
    seen = {}

    def fake_execute(agent, route, *args, **kwargs):
        seen["agent"] = agent.agent_id
        seen["resource"] = route.resource.resource_id
        seen["adapter"] = route.resource.adapter
        return ToolResult(True, {"content": "fallback ok", "usage": {}}, "")

    monkeypatch.setattr(dispatcher, "_execute_route", fake_execute)
    result = dispatcher.dispatch("cocytus", "Repair parser.", mission_id="health-fallback")
    assert result.success
    assert seen == {
        "agent": "cocytus",
        "resource": "gpt_plus",
        "adapter": "codex_cli",
    }


def test_dispatch_fails_before_execution_when_no_healthy_route(tmp_path: Path, monkeypatch) -> None:
    dispatcher = CourtDispatcher(
        state_path=tmp_path / "state.sqlite3",
        health=OnlyHealthy(set()),
    )
    monkeypatch.setattr(
        dispatcher,
        "_execute_route",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not execute")),
    )
    result = dispatcher.dispatch("cocytus", "Repair parser.", mission_id="no-health")
    assert result.success is False
    assert "no healthy compute route" in result.error


def test_dispatch_budget_guard_blocks_before_adapter(tmp_path: Path, monkeypatch) -> None:
    from lilith_cli.config import CampaignBudgetConfig
    from lilith_cli.mission.budget_guard import AggregateBudgetGuard
    from lilith_tools.orchestration_state import OrchestrationStateStore

    state = tmp_path / "state.sqlite3"
    store = OrchestrationStateStore(state)
    store.record_cost(
        "lilith", "experimental_labs", {"total_tokens": 1, "cost_usd": 0.0},
        session_id="root-campaign",
    )
    dispatcher = CourtDispatcher(state_path=state, health=AllHealthy())
    dispatcher.budget_guard = AggregateBudgetGuard(
        policy=CampaignBudgetConfig(daily_max_calls=1, daily_max_usd=None),
        state_path=str(state),
    )

    def should_not_execute(*args, **kwargs):
        raise AssertionError("adapter must not run after aggregate budget exhaustion")

    monkeypatch.setattr(dispatcher, "_execute_route", should_not_execute)
    result = dispatcher.dispatch("aura", "Inspect repository", mission_id="root-campaign")
    assert result.success is False
    assert result.error == "daily call budget exhausted"
    assert result.data["budget"]["allowed"] is False
    tasks = store.get()["tasks"]
    assert not any(row.get("preset") == "aura" for row in tasks)


def test_dispatch_cost_is_attributed_to_root_campaign(tmp_path: Path, monkeypatch) -> None:
    from lilith_cli.config import CampaignBudgetConfig
    from lilith_cli.mission.budget_guard import AggregateBudgetGuard
    from lilith_tools.orchestration_state import OrchestrationStateStore

    state = tmp_path / "state.sqlite3"
    dispatcher = CourtDispatcher(state_path=state, health=AllHealthy())
    dispatcher.budget_guard = AggregateBudgetGuard(
        policy=CampaignBudgetConfig(daily_max_usd=None, campaign_max_usd=None),
        state_path=str(state),
    )
    monkeypatch.setenv("YGGDRASIL_CAMPAIGN_ID", "root-campaign")
    monkeypatch.setattr(
        dispatcher,
        "_execute_route",
        lambda *args, **kwargs: ToolResult(
            True,
            {"content": "ok", "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}},
            "",
        ),
    )
    result = dispatcher.dispatch("cocytus", "Fix test", mission_id="child-mission")
    assert result.success
    rows = OrchestrationStateStore(state).events(limit=100)
    cost = next(row for row in rows if row["event_type"] == "cost.recorded")
    assert cost["payload"]["session_id"] == "root-campaign"
    assert cost["payload"]["provider"] == result.data["compute_resource"]
