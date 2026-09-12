from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from threading import Barrier
from types import ModuleType

import pytest

from lilith_tools.yggdrasil_gateway import (
    GatewayConfigError,
    GatewayNoRoute,
    GatewayUnavailable,
    ROUTER_CONFIG_ENV,
    ROUTER_HOME_ENV,
    ROUTER_MODE_ENV,
    ROUTER_STATE_ENV,
    YggdrasilGateway,
    clear_gateway_cache,
    probe_gateway,
    record_gateway_outcome,
    resolve_env_secret,
    router_mode,
)


@pytest.fixture(autouse=True)
def _isolated_router_state(tmp_path, monkeypatch):
    monkeypatch.setenv(ROUTER_STATE_ENV, str(tmp_path / "router-state.json"))
    clear_gateway_cache()
    yield
    clear_gateway_cache()


def _router_home() -> Path:
    root = Path(__file__).resolve()
    for parent in root.parents:
        candidate = parent / "Vanaheim" / "Core" / "yggdrasil_router"
        if (candidate / "__init__.py").is_file():
            return candidate
    pytest.skip("Yggdrasil Router checkout not present")


def _write_config(
    tmp_path: Path,
    *,
    extra_account: dict | None = None,
    top_level: dict | None = None,
) -> Path:
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
    payload: dict = {"failure_threshold": 3, "accounts": [account]}
    if top_level:
        payload.update(top_level)
    path = tmp_path / "router.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
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


def test_account_field_typo_still_fails(tmp_path):
    # La validacion estricta se mantiene: derivar los campos del contrato no
    # puede convertirse en aceptarlo todo.  Una errata tiene que doler.
    config = _write_config(tmp_path, extra_account={"capabilites": ["chat"]})
    with pytest.raises(GatewayConfigError, match="capabilites"):
        YggdrasilGateway(config, router_home=_router_home())


def test_account_accepts_runtime_from_the_real_contract(tmp_path):
    # Este es el fallo que tenia bloqueada a Lilith: `runtime` es el eje del
    # enrutado y el adaptador lo rechazaba por tener la lista copiada a mano.
    config = _write_config(tmp_path, extra_account={"runtime": "direct-api"})
    gateway = YggdrasilGateway(config, router_home=_router_home())

    assert gateway.router.accounts[0].runtime == "direct-api"


def test_top_level_metadata_does_not_break_loading(tmp_path):
    config = _write_config(
        tmp_path,
        top_level={
            "schema_version": "1.2",
            "nota": "comentario del fichero",
            "nota_costes": "otro comentario",
        },
    )
    gateway = YggdrasilGateway(config, router_home=_router_home())

    assert len(gateway.router.accounts) == 1


def test_newer_schema_version_fails_saying_so(tmp_path):
    # Lo que no puede volver a pasar: que un esquema mas nuevo se manifieste
    # como "campo desconocido: runtime" y mande a depurar al sitio equivocado.
    config = _write_config(tmp_path, top_level={"schema_version": "2.0"})
    with pytest.raises(GatewayConfigError, match="newer than supported"):
        YggdrasilGateway(config, router_home=_router_home())


def test_namespaced_schema_version_is_understood(tmp_path):
    # Forma REAL que escribe el Fabric en agents.yggdrasil.json.  Suponer una
    # version desnuda ("1.0") hacia que la configuracion de produccion fallara.
    config = _write_config(
        tmp_path, top_level={"schema_version": "ygg.router.accounts/1.0"}
    )
    gateway = YggdrasilGateway(config, router_home=_router_home())

    assert len(gateway.router.accounts) == 1


def test_namespaced_schema_version_still_detects_a_newer_major(tmp_path):
    config = _write_config(
        tmp_path, top_level={"schema_version": "ygg.router.accounts/2.0"}
    )
    with pytest.raises(GatewayConfigError, match="newer than supported"):
        YggdrasilGateway(config, router_home=_router_home())


def test_unparseable_schema_version_does_not_block_loading(tmp_path):
    config = _write_config(tmp_path, top_level={"schema_version": "experimental"})
    gateway = YggdrasilGateway(config, router_home=_router_home())

    assert len(gateway.router.accounts) == 1


def test_unknown_top_level_key_still_fails(tmp_path):
    config = _write_config(tmp_path, top_level={"mystery_toggle": True})
    with pytest.raises(GatewayConfigError, match="mystery_toggle"):
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


def test_exhausted_account_from_state_is_not_selected(tmp_path, monkeypatch):
    config = _write_config(tmp_path)
    state_path = tmp_path / "router-state.json"
    state_path.write_text(
        json.dumps(
            {
                "accounts": {
                    "deepseek_primary": {
                        "status": "quota_exhausted",
                        "quota_remaining": 0,
                    }
                },
                "processed_codex_job_ids": [],
                "suspicious_codex_job_ids": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(ROUTER_STATE_ENV, str(state_path))

    gateway = YggdrasilGateway(config, router_home=_router_home())

    with pytest.raises(GatewayNoRoute):
        gateway.route(capability="chat")


def test_enforced_outcome_survives_gateway_reconstruction(tmp_path, monkeypatch):
    config = _write_config(tmp_path, extra_account={"quota_remaining": 1})
    state_path = tmp_path / "router-state.json"
    monkeypatch.setenv(ROUTER_HOME_ENV, str(_router_home()))
    monkeypatch.setenv(ROUTER_CONFIG_ENV, str(config))
    monkeypatch.setenv(ROUTER_STATE_ENV, str(state_path))

    gateway = YggdrasilGateway.from_env()
    assert gateway is not None
    route = gateway.route(capability="chat").public_dict()
    route.update({"mode": "enforce", "status": "selected"})
    record_gateway_outcome(route, success=True)
    assert state_path.is_file()

    clear_gateway_cache()
    rebuilt = YggdrasilGateway.from_env()
    assert rebuilt is not None
    with pytest.raises(GatewayNoRoute):
        rebuilt.route(capability="chat")


def test_two_gateways_concurrently_preserve_both_token_updates(tmp_path, monkeypatch):
    config = _write_config(
        tmp_path,
        extra_account={
            "daily_token_quota": 40_000,
            "token_quota_remaining": 40_000,
        },
    )
    state_path = tmp_path / "router-state.json"
    monkeypatch.setenv(ROUTER_STATE_ENV, str(state_path))
    first = YggdrasilGateway(config, router_home=_router_home())
    second = YggdrasilGateway(config, router_home=_router_home())
    barrier = Barrier(2)

    def consume(gateway, tokens):
        barrier.wait(timeout=5)
        gateway.record_outcome("deepseek_primary", success=True, tokens_used=tokens)

    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(consume, first, 10_000)
        two = pool.submit(consume, second, 15_000)
        one.result(timeout=5)
        two.result(timeout=5)

    rebuilt = YggdrasilGateway(config, router_home=_router_home())
    assert rebuilt.router.accounts[0].token_quota_remaining == 15_000


def test_router_state_off_neither_reads_nor_writes(tmp_path, monkeypatch):
    config = _write_config(tmp_path, extra_account={"quota_remaining": 1})
    state_path = tmp_path / "router-state.json"
    state_path.write_text("not-json", encoding="utf-8")
    state_path.unlink()
    monkeypatch.setenv(ROUTER_HOME_ENV, str(_router_home()))
    monkeypatch.setenv(ROUTER_CONFIG_ENV, str(config))
    monkeypatch.setenv(ROUTER_STATE_ENV, "off")

    gateway = YggdrasilGateway.from_env()
    assert gateway is not None
    route = gateway.route(capability="chat").public_dict()
    route.update({"mode": "enforce", "status": "selected"})
    record_gateway_outcome(route, success=True)

    assert not state_path.exists()


def test_router_checkout_without_state_module_still_routes(tmp_path):
    config = _write_config(tmp_path)
    current = YggdrasilGateway(config, router_home=_router_home())._module
    legacy = ModuleType("legacy_yggdrasil_router")
    legacy.__file__ = str(tmp_path / "legacy-router" / "__init__.py")
    for name in ("Account", "AccountStatus", "RouteRequest", "YggdrasilRouter"):
        setattr(legacy, name, getattr(current, name))

    gateway = YggdrasilGateway(config, router_module=legacy)

    assert gateway.route(capability="chat").account_id == "deepseek_primary"
    gateway.record_outcome("deepseek_primary", success=True)


def test_corrupt_router_state_does_not_block_routing(tmp_path, monkeypatch, caplog):
    config = _write_config(tmp_path)
    state_path = tmp_path / "router-state.json"
    state_path.write_text("{broken", encoding="utf-8")
    monkeypatch.setenv(ROUTER_STATE_ENV, str(state_path))

    gateway = YggdrasilGateway(config, router_home=_router_home())

    assert gateway.route(capability="chat").account_id == "deepseek_primary"
    assert "Could not read router state" in caplog.text
