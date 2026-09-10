from __future__ import annotations

import json
from pathlib import Path

import pytest

from lilith_tools.yggdrasil_gateway import (
    GatewayConfigError,
    GatewayUnavailable,
    ROUTER_CONFIG_ENV,
    ROUTER_HOME_ENV,
    ROUTER_MODE_ENV,
    YggdrasilGateway,
    clear_gateway_cache,
    probe_gateway,
    record_gateway_outcome,
    resolve_env_secret,
    router_mode,
)


def _router_home() -> Path:
    root = Path(__file__).resolve()
    for parent in root.parents:
        candidate = parent / "Vanaheim" / "Core" / "yggdrasil_router"
        if (candidate / "__init__.py").is_file():
            return candidate
    pytest.skip("Yggdrasil Router checkout not present")


def _write_config(tmp_path: Path, *, extra_account: dict | None = None) -> Path:
    account = {
        "account_id": "deepseek_primary",
        "provider": "deepseek",
        "secret_ref": "env://TEST_YGGDRASIL_DEEPSEEK_KEY",
        "capabilities": ["chat", "code"],
        "models": ["deepseek-v4-flash"],
        "status": "healthy",
        "priority": 100,
        "quota_remaining": 10,
        "token_quota_remaining": 100000,
    }
    if extra_account:
        account.update(extra_account)
    path = tmp_path / "router.json"
    path.write_text(
        json.dumps({"failure_threshold": 3, "accounts": [account]}),
        encoding="utf-8",
    )
    return path


def test_from_env_is_disabled_without_config(monkeypatch):
    monkeypatch.delenv(ROUTER_CONFIG_ENV, raising=False)
    assert YggdrasilGateway.from_env() is None
    probe = probe_gateway()
    assert probe.configured is False
    assert probe.available is False
    assert probe.mode == "shadow"


def test_router_mode_is_explicit_and_fail_fast(monkeypatch):
    monkeypatch.delenv(ROUTER_MODE_ENV, raising=False)
    assert router_mode() == "shadow"
    monkeypatch.setenv(ROUTER_MODE_ENV, "enforce")
    assert router_mode() == "enforce"
    monkeypatch.setenv(ROUTER_MODE_ENV, "unsafe")
    with pytest.raises(GatewayConfigError, match="invalid YGGDRASIL_ROUTER_MODE"):
        router_mode()


def test_real_router_checkout_routes_without_exposing_secret(tmp_path, monkeypatch):
    router_home = _router_home()
    config = _write_config(tmp_path)
    monkeypatch.setenv(ROUTER_HOME_ENV, str(router_home))

    gateway = YggdrasilGateway(config, router_home=router_home)
    decision = gateway.route(
        capability="chat",
        estimated_tokens=1000,
        preferred_provider="deepseek",
        preferred_model="deepseek-v4-flash",
        tags={"lilith", "test"},
    )

    assert decision.account_id == "deepseek_primary"
    assert decision.provider == "deepseek"
    assert decision.model == "deepseek-v4-flash"
    assert decision.secret_ref == "env://TEST_YGGDRASIL_DEEPSEEK_KEY"
    assert "secret_ref" not in decision.public_dict()
    assert "TEST_YGGDRASIL_DEEPSEEK_KEY" not in repr(decision)


def test_config_rejects_unknown_account_fields(tmp_path):
    config = _write_config(tmp_path, extra_account={"mystery_budget": 1})
    with pytest.raises(GatewayConfigError, match="unsupported fields"):
        YggdrasilGateway(config, router_home=_router_home())


def test_resolve_env_secret_supports_only_env_refs(monkeypatch):
    monkeypatch.setenv("TEST_YGGDRASIL_SECRET", "opaque-value")
    assert resolve_env_secret("env://TEST_YGGDRASIL_SECRET") == "opaque-value"
    with pytest.raises(GatewayUnavailable, match="env://"):
        resolve_env_secret("vault://providers/example")


def test_probe_is_sanitized_and_reports_account_count(tmp_path, monkeypatch):
    router_home = _router_home()
    config = _write_config(tmp_path)
    monkeypatch.setenv(ROUTER_HOME_ENV, str(router_home))
    monkeypatch.setenv(ROUTER_CONFIG_ENV, str(config))
    monkeypatch.setenv(ROUTER_MODE_ENV, "shadow")

    probe = probe_gateway()
    public = probe.public_dict()

    assert probe.configured is True
    assert probe.available is True
    assert probe.account_count == 1
    assert probe.mode == "shadow"
    assert "secret" not in repr(public).lower()


def test_from_env_reuses_process_router_until_config_changes(tmp_path, monkeypatch):
    router_home = _router_home()
    config = _write_config(tmp_path)
    monkeypatch.setenv(ROUTER_HOME_ENV, str(router_home))
    monkeypatch.setenv(ROUTER_CONFIG_ENV, str(config))
    clear_gateway_cache()

    first = YggdrasilGateway.from_env()
    second = YggdrasilGateway.from_env()
    assert first is second

    original = config.read_text(encoding="utf-8")
    config.write_text(original + "\n", encoding="utf-8")
    third = YggdrasilGateway.from_env()
    assert third is not first
    clear_gateway_cache()


def test_multi_account_route_rotates_after_preferred_account_quota_is_consumed(tmp_path, monkeypatch):
    router_home = _router_home()
    config = tmp_path / "router-multi.json"
    config.write_text(
        json.dumps(
            {
                "failure_threshold": 3,
                "accounts": [
                    {
                        "account_id": "deepseek-a",
                        "provider": "deepseek",
                        "secret_ref": "env://TEST_YGGDRASIL_DEEPSEEK_A",
                        "capabilities": ["chat"],
                        "models": ["deepseek-v4-flash"],
                        "status": "healthy",
                        "priority": 100,
                        "quota_remaining": 1,
                        "token_quota_remaining": 100,
                    },
                    {
                        "account_id": "deepseek-b",
                        "provider": "deepseek",
                        "secret_ref": "env://TEST_YGGDRASIL_DEEPSEEK_B",
                        "capabilities": ["chat"],
                        "models": ["deepseek-v4-flash"],
                        "status": "healthy",
                        "priority": 90,
                        "quota_remaining": 10,
                        "token_quota_remaining": 1000,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(ROUTER_HOME_ENV, str(router_home))
    monkeypatch.setenv(ROUTER_CONFIG_ENV, str(config))
    clear_gateway_cache()
    gateway = YggdrasilGateway.from_env()
    assert gateway is not None

    first = gateway.route(
        capability="chat",
        estimated_tokens=10,
        preferred_provider="deepseek",
        preferred_model="deepseek-v4-flash",
    )
    assert first.account_id == "deepseek-a"
    gateway.record_outcome(first.account_id, success=True, tokens_used=10)

    second = gateway.route(
        capability="chat",
        estimated_tokens=10,
        preferred_provider="deepseek",
        preferred_model="deepseek-v4-flash",
    )
    assert second.account_id == "deepseek-b"
    clear_gateway_cache()


def test_enforced_outcome_consumes_cached_account_quota(tmp_path, monkeypatch):
    router_home = _router_home()
    config = _write_config(
        tmp_path,
        extra_account={
            "quota_remaining": 2,
            "token_quota_remaining": 100,
            "cost_per_1k_tokens_usd": 1.0,
        },
    )
    monkeypatch.setenv(ROUTER_HOME_ENV, str(router_home))
    monkeypatch.setenv(ROUTER_CONFIG_ENV, str(config))
    clear_gateway_cache()
    gateway = YggdrasilGateway.from_env()
    assert gateway is not None

    route = gateway.route(
        capability="chat",
        estimated_tokens=10,
        preferred_provider="deepseek",
        preferred_model="deepseek-v4-flash",
    ).public_dict()
    route.update({"mode": "enforce", "status": "selected"})
    record_gateway_outcome(route, success=True, usage={"total_tokens": 12})

    account = gateway.router.accounts[0]
    assert account.quota_remaining == 1
    assert account.token_quota_remaining == 88
    assert account.spent_today_usd == pytest.approx(0.012)

    # Shadow routing is observational only and must not consume quota.
    shadow = dict(route, mode="shadow")
    record_gateway_outcome(shadow, success=True, usage={"total_tokens": 10})
    assert account.quota_remaining == 1
    assert account.token_quota_remaining == 88
    clear_gateway_cache()
