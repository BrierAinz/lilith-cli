import pytest
from lilith_cli.mission.compute import ComputeBroker


def test_free_resource_handles_read_only_reasoning_first() -> None:
    route = ComputeBroker().route({"analysis", "code_read"})
    assert route.resource.resource_id == "experimental_labs"
    assert route.resource.cost_class == "free"


def test_code_write_prefers_subscription_router_resource() -> None:
    route = ComputeBroker().route({"code_write"})
    assert route.resource.resource_id == "opencode_go"
    assert "minimax_plus" in route.fallbacks


def test_cli_preference_uses_subscription_cli_without_binding_court() -> None:
    route = ComputeBroker().route(
        {"code_write", "review"},
        preferred_transport="cli",
    )
    assert route.resource.resource_id == "claude_pro"
    assert route.resource.adapter == "claude_code"


def test_unhealthy_resources_fall_through_or_fail() -> None:
    broker = ComputeBroker()
    route = broker.route({"code_write"}, healthy={"gpt_plus"})
    assert route.resource.resource_id == "gpt_plus"
    with pytest.raises(LookupError):
        broker.route({"code_write"}, healthy=set())
