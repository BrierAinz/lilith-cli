from pathlib import Path

from lilith_cli.config import CampaignBudgetConfig
from lilith_cli.mission.budget_guard import AggregateBudgetGuard, campaign_id_for_task
from lilith_tools.orchestration_state import OrchestrationStateStore


def _guard(tmp_path: Path, **kwargs) -> tuple[AggregateBudgetGuard, OrchestrationStateStore]:
    state = tmp_path / "state.sqlite3"
    store = OrchestrationStateStore(state)
    policy = CampaignBudgetConfig(**kwargs)
    return AggregateBudgetGuard(policy=policy, state_path=str(state)), store


def test_daily_campaign_and_provider_usage_are_separated(tmp_path: Path) -> None:
    guard, store = _guard(tmp_path)
    store.record_cost(
        "cocytus", "opencode_go",
        {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "cost_usd": 0.0},
        session_id="campaign-a",
    )
    store.record_cost(
        "aura", "experimental_labs",
        {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10, "cost_usd": 0.0},
        session_id="campaign-b",
    )
    snap = guard.snapshot(campaign_id="campaign-a", provider="opencode_go")
    assert snap["daily"] == {"calls": 2, "tokens": 25, "cost_usd": 0.0}
    assert snap["campaign"] == {"calls": 1, "tokens": 15, "cost_usd": 0.0}
    assert snap["provider_daily"] == {"calls": 1, "tokens": 15, "cost_usd": 0.0}


def test_aggregate_limits_block_at_boundary(tmp_path: Path) -> None:
    guard, store = _guard(
        tmp_path,
        daily_max_calls=2,
        daily_max_tokens=20,
        campaign_max_calls=1,
        provider_daily_max_calls={"opencode_go": 1},
    )
    store.record_cost(
        "cocytus", "opencode_go",
        {"total_tokens": 20, "cost_usd": 0.0},
        session_id="campaign-a",
    )
    decision = guard.check(campaign_id="campaign-a", provider="opencode_go")
    assert decision.allowed is False
    assert decision.reason in {
        "daily token budget exhausted",
        "campaign call budget exhausted",
        "opencode_go daily call budget exhausted",
    }


def test_zero_usd_budget_denies_metered_before_dispatch(tmp_path: Path) -> None:
    guard, _ = _guard(tmp_path, daily_max_usd=0.0)
    metered = guard.check(
        campaign_id="campaign-a",
        provider="metered-api",
        prospective_cost_class="metered",
    )
    subscription = guard.check(
        campaign_id="campaign-a",
        provider="claude_pro",
        prospective_cost_class="subscription",
    )
    assert metered.allowed is False
    assert "metered compute denied" in metered.reason
    assert subscription.allowed is True


def test_zero_usd_budget_blocks_after_observed_spend(tmp_path: Path) -> None:
    guard, store = _guard(tmp_path, daily_max_usd=0.0)
    store.record_cost(
        "lilith", "fabric",
        {"total_tokens": 100, "cost_usd": 0.01},
        session_id="campaign-a",
    )
    decision = guard.check(campaign_id="campaign-b", provider="claude_pro")
    assert decision.allowed is False
    assert decision.reason == "daily USD budget exhausted"


def test_campaign_id_follows_parent_chain_to_root(tmp_path: Path) -> None:
    _, store = _guard(tmp_path)
    root = store.add_task(
        "root", status="completada", preset="lilith-mission",
        task_id="root-task", correlation_id="root-campaign",
        routing={"mission_spec": {"metadata": {}}},
    )
    child = store.add_task(
        "child", status="pendiente", preset="lilith-mission",
        task_id="child-task", correlation_id="child-correlation",
        routing={
            "mission_spec": {
                "metadata": {"parent_task_id": root["id"], "campaign_mode": True}
            }
        },
    )
    assert campaign_id_for_task(store, child) == "root-campaign"
