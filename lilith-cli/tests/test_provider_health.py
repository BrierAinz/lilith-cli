from __future__ import annotations

import sqlite3
from pathlib import Path

from lilith_cli.provider_health import ProviderHealthRegistry


def test_circuit_opens_after_threshold_and_success_closes(tmp_path: Path) -> None:
    registry = ProviderHealthRegistry(tmp_path / "health.sqlite3")

    first = registry.record_failure("Kimi", "timeout", threshold=2, cooldown_seconds=60)
    assert first["state"] == "closed"
    assert registry.allow("kimi") is True

    second = registry.record_failure("kimi", "timeout", threshold=2, cooldown_seconds=60)
    assert second["state"] == "open"
    assert registry.allow("kimi") is False

    healthy = registry.record_success("kimi", latency_ms=42)
    assert healthy["state"] == "closed"
    assert healthy["consecutive_failures"] == 0
    assert healthy["last_latency_ms"] == 42


def test_auth_failure_opens_immediately(tmp_path: Path) -> None:
    registry = ProviderHealthRegistry(tmp_path / "health.sqlite3")
    state = registry.record_failure(
        "stale", "401 unauthorized", threshold=10, permanent=True
    )
    assert state["state"] == "open"
    assert state["failures"] == 1


def test_registry_is_shared_between_process_facades(tmp_path: Path) -> None:
    path = tmp_path / "health.sqlite3"
    first = ProviderHealthRegistry(path)
    second = ProviderHealthRegistry(path)
    first.record_failure("local", "offline", threshold=1)
    assert second.allow("local") is False


def test_only_one_registry_claims_half_open_probe(tmp_path: Path) -> None:
    path = tmp_path / "health.sqlite3"
    first = ProviderHealthRegistry(path)
    second = ProviderHealthRegistry(path)
    first.record_failure("kimi", "offline", threshold=1, cooldown_seconds=60)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE provider_health SET opened_until=0 WHERE provider='kimi'")

    assert first.allow("kimi") is True
    assert second.allow("kimi") is False
    assert second.get("kimi")["state"] == "half_open"


def test_reset_one_provider_preserves_others(tmp_path: Path) -> None:
    registry = ProviderHealthRegistry(tmp_path / "health.sqlite3")
    registry.record_failure("a", "down", threshold=1)
    registry.record_failure("b", "down", threshold=1)
    registry.reset("a")
    assert registry.allow("a") is True
    assert registry.allow("b") is False
