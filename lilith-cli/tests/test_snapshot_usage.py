import pytest
from lilith_cli.snapshot_usage import merged_usage, validated_usage


@pytest.mark.parametrize("value", [True, -1, "10", 1.5, None])
def test_invalid_token_counters_rejected(value):
    with pytest.raises(ValueError):
        validated_usage({"total_tokens": value}, {})


def test_merge_is_precomputed_without_mutating_inputs():
    totals = {"total_tokens": 5}
    models = {"local": {"total_tokens": 5, "cost": 0.5}}
    result, per_model = merged_usage(
        totals,
        models,
        {"total_tokens": 7},
        {"local": {"total_tokens": 7, "cost": 0.25}},
    )
    assert result["total_tokens"] == 12
    assert per_model["local"]["cost"] == 0.75
    assert totals == {"total_tokens": 5}
    assert models["local"]["total_tokens"] == 5


def test_overflowing_cost_is_rejected_before_live_assignment():
    with pytest.raises(ValueError):
        merged_usage({}, {"model": {"cost": 1e308}}, {}, {"model": {"cost": 1e308}})


@pytest.mark.parametrize("cost", [float("inf"), float("nan"), True, 10**400, -1])
def test_invalid_costs_are_validation_errors(cost):
    with pytest.raises(ValueError):
        validated_usage({}, {"model": {"cost": cost}})
