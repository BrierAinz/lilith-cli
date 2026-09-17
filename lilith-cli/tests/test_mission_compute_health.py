from pathlib import Path

from lilith_cli.mission.compute_health import ComputeHealthResolver
from lilith_cli.provider_health import ProviderHealthRegistry


def _resolver(tmp_path: Path) -> ComputeHealthResolver:
    return ComputeHealthResolver(
        provider_health=ProviderHealthRegistry(tmp_path / "health.sqlite3"),
        state_path=tmp_path / "state.sqlite3",
    )


def test_closed_fabric_and_present_cli_adapters_are_healthy(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("YGGDRASIL_FABRIC_TOKEN", "fixture-token")
    monkeypatch.setattr(
        ComputeHealthResolver,
        "_wrapper_available",
        staticmethod(lambda adapter: (True, f"{adapter}-wrapper")),
    )
    resolver = _resolver(tmp_path)
    rows = {row.resource_id: row for row in resolver.snapshot()}
    assert rows["experimental_labs"].available
    assert rows["opencode_go"].available
    assert rows["minimax_plus"].available
    assert rows["claude_pro"].available
    assert rows["gpt_plus"].available


def test_open_fabric_circuit_removes_all_fabric_resources(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("YGGDRASIL_FABRIC_TOKEN", "fixture-token")
    monkeypatch.setattr(
        ComputeHealthResolver,
        "_wrapper_available",
        staticmethod(lambda adapter: (True, f"{adapter}-wrapper")),
    )
    resolver = _resolver(tmp_path)
    resolver.provider_health.record_failure(
        "fabric", "connection refused", permanent=True, cooldown_seconds=600
    )
    healthy = resolver.healthy_ids()
    assert "experimental_labs" not in healthy
    assert "opencode_go" not in healthy
    assert "minimax_plus" not in healthy
    assert {"claude_pro", "gpt_plus"} <= healthy


def test_missing_fabric_token_is_observable_without_probe(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("YGGDRASIL_FABRIC_TOKEN", raising=False)
    monkeypatch.setattr(
        ComputeHealthResolver,
        "_wrapper_available",
        staticmethod(lambda adapter: (True, f"{adapter}-wrapper")),
    )
    rows = {row.resource_id: row for row in _resolver(tmp_path).snapshot()}
    assert rows["experimental_labs"].available is False
    assert rows["experimental_labs"].source == "environment"
    assert rows["claude_pro"].available is True


def test_role_learning_can_remove_one_resource_without_global_outage(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("YGGDRASIL_FABRIC_TOKEN", "fixture-token")
    monkeypatch.setattr(
        ComputeHealthResolver,
        "_wrapper_available",
        staticmethod(lambda adapter: (True, f"{adapter}-wrapper")),
    )
    resolver = _resolver(tmp_path)
    monkeypatch.setattr(
        resolver.learning,
        "avoided_resources",
        lambda agent: {"opencode_go"} if agent == "cocytus" else set(),
    )
    rows = {
        row.resource_id: row
        for row in resolver.snapshot(court_agent="cocytus")
    }
    assert rows["opencode_go"].available is False
    assert rows["opencode_go"].state == "learned_open"
    assert rows["minimax_plus"].available is True


def test_missing_cli_wrapper_marks_only_that_cli_resource_unavailable(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("YGGDRASIL_FABRIC_TOKEN", "fixture-token")
    monkeypatch.setattr(
        ComputeHealthResolver,
        "_wrapper_available",
        staticmethod(
            lambda adapter: (
                (False, "claude wrapper missing")
                if adapter == "claude_code"
                else (True, "codex wrapper")
            )
        ),
    )
    rows = {row.resource_id: row for row in _resolver(tmp_path).snapshot()}
    assert rows["claude_pro"].available is False
    assert rows["gpt_plus"].available is True


def test_observed_fabric_accounts_come_from_passive_health_registry(tmp_path: Path) -> None:
    resolver = _resolver(tmp_path)
    resolver.provider_health.record_success(
        "fabric-account:experiential:luna", latency_ms=17
    )
    resolver.provider_health.record_failure(
        "fabric-account:opencode:go-primary",
        "quota exhausted",
        permanent=True,
        cooldown_seconds=600,
    )
    rows = resolver.observed_fabric_accounts()
    assert rows[0]["account_id"] == "luna"
    assert rows[0]["provider"] == "experiential"
    assert rows[0]["successes"] == 1
    assert rows[0]["last_latency_ms"] == 17
    assert rows[1]["account_id"] == "go-primary"
    assert rows[1]["state"] == "open"
    assert rows[1]["failures"] == 1
