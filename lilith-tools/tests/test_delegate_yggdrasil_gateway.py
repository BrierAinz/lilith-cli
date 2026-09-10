from __future__ import annotations

import json
import sys
import types
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from lilith_tools.base import ToolResult
from lilith_tools.delegate import DelegateSubagentTool, _gateway_route_for_delegate
from lilith_tools.orchestration_state import OrchestrationStateStore
from lilith_tools import yggdrasil_gateway


class _Decision:
    account_id = "account-b"
    provider = "deepseek"
    model = "deepseek-v4-flash"
    score = 101.0
    estimated_cost_usd = 0.01
    reason = "test route"
    secret_ref = "env://TEST_ROUTED_ACCOUNT_KEY"

    def public_dict(self):
        return {
            "account_id": self.account_id,
            "provider": self.provider,
            "model": self.model,
            "score": self.score,
            "estimated_cost_usd": self.estimated_cost_usd,
            "reason": self.reason,
        }


class _Gateway:
    def route(self, **kwargs):
        self.request = kwargs
        return _Decision()


class _OtherProviderDecision(_Decision):
    account_id = "openai-a"
    provider = "openai"
    model = "gpt-example"


class _OtherProviderGateway(_Gateway):
    def route(self, **kwargs):
        self.request = kwargs
        return _OtherProviderDecision()


def _cfg(secret: str = "original"):
    return SimpleNamespace(
        api_key=SecretStr(secret),
        model="deepseek-v4-flash",
        max_tokens=2048,
    )


def test_shadow_route_is_observable_but_does_not_change_credential(monkeypatch):
    gateway = _Gateway()
    monkeypatch.setattr(
        yggdrasil_gateway.YggdrasilGateway,
        "from_env",
        classmethod(lambda cls: gateway),
    )
    monkeypatch.setattr(yggdrasil_gateway, "router_mode", lambda: "shadow")
    cfg = _cfg()

    route, error = _gateway_route_for_delegate(
        cfg=cfg,
        provider_name="deepseek",
        preset_name="batch-deepseek",
        agentic=True,
        structured=False,
    )

    assert error is None
    assert route["status"] == "selected"
    assert route["mode"] == "shadow"
    assert route["account_id"] == "account-b"
    assert "secret_ref" not in route
    assert cfg.api_key.get_secret_value() == "original"
    assert gateway.request["capability"] == "code"
    assert gateway.request["preferred_provider"] is None
    assert gateway.request["preferred_model"] is None
    assert route["current_provider"] == "deepseek"
    assert route["current_model"] == "deepseek-v4-flash"
    assert route["compatible_with_current_executor"] is True


def test_shadow_can_observe_cross_provider_choice_without_switching_executor(monkeypatch):
    gateway = _OtherProviderGateway()
    monkeypatch.setattr(
        yggdrasil_gateway.YggdrasilGateway,
        "from_env",
        classmethod(lambda cls: gateway),
    )
    monkeypatch.setattr(yggdrasil_gateway, "router_mode", lambda: "shadow")
    cfg = _cfg()

    route, error = _gateway_route_for_delegate(
        cfg=cfg,
        provider_name="deepseek",
        preset_name="batch-deepseek",
        agentic=False,
        structured=False,
    )

    assert error is None
    assert route["provider"] == "openai"
    assert route["account_id"] == "openai-a"
    assert route["compatible_with_current_executor"] is False
    assert gateway.request["preferred_provider"] is None
    assert gateway.request["preferred_model"] is None
    assert cfg.api_key.get_secret_value() == "original"


def test_enforce_route_uses_env_secret_in_process_only(monkeypatch):
    gateway = _Gateway()
    monkeypatch.setattr(
        yggdrasil_gateway.YggdrasilGateway,
        "from_env",
        classmethod(lambda cls: gateway),
    )
    monkeypatch.setattr(yggdrasil_gateway, "router_mode", lambda: "enforce")
    monkeypatch.setenv("TEST_ROUTED_ACCOUNT_KEY", "routed-secret-value")
    cfg = _cfg()

    route, error = _gateway_route_for_delegate(
        cfg=cfg,
        provider_name="deepseek",
        preset_name="batch-deepseek",
        agentic=False,
        structured=True,
    )

    assert error is None
    assert route["mode"] == "enforce"
    assert route["capability"] == "chat"
    assert route["compatible_with_current_executor"] is True
    assert gateway.request["preferred_provider"] == "deepseek"
    assert gateway.request["preferred_model"] == "deepseek-v4-flash"
    assert cfg.api_key.get_secret_value() == "routed-secret-value"
    serialized = repr(route)
    assert "TEST_ROUTED_ACCOUNT_KEY" not in serialized
    assert "routed-secret-value" not in serialized


def test_enforce_fails_closed_when_secret_reference_cannot_resolve(monkeypatch):
    gateway = _Gateway()
    monkeypatch.setattr(
        yggdrasil_gateway.YggdrasilGateway,
        "from_env",
        classmethod(lambda cls: gateway),
    )
    monkeypatch.setattr(yggdrasil_gateway, "router_mode", lambda: "enforce")
    monkeypatch.delenv("TEST_ROUTED_ACCOUNT_KEY", raising=False)
    cfg = _cfg()

    route, error = _gateway_route_for_delegate(
        cfg=cfg,
        provider_name="deepseek",
        preset_name="batch-deepseek",
        agentic=False,
        structured=False,
    )

    assert route["status"] == "fallback"
    assert error is not None
    assert cfg.api_key.get_secret_value() == "original"


def test_delegate_persists_only_sanitized_gateway_route(monkeypatch, tmp_path):
    state_path = tmp_path / "orchestration.json"
    monkeypatch.setenv("YGGDRASIL_ORCHESTRATION_STATE", str(state_path))
    route = {
        "account_id": "account-b",
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "score": 101.0,
        "estimated_cost_usd": 0.01,
        "reason": "test route",
        "mode": "shadow",
        "status": "selected",
        "capability": "chat",
    }
    tool = DelegateSubagentTool()
    monkeypatch.setattr(
        tool,
        "_execute_delegate",
        lambda preset, prompt, kwargs: ToolResult(
            success=True,
            data={
                "preset": preset,
                "provider": "deepseek",
                "content": "ok",
                "usage": {"total_tokens": 12},
                "gateway_route": route,
            },
        ),
    )

    result = tool.execute(preset="batch-deepseek", prompt="inspect project")

    assert result.success is True
    state = OrchestrationStateStore(state_path).get()
    task = state["tasks"][0]
    post_mortem = state["post_mortems"][0]
    assert task["routing"]["gateway"] == route
    assert post_mortem["gateway_route"] == route
    serialized = repr(state)
    assert "secret_ref" not in serialized
    assert "routed-secret-value" not in serialized


def test_enforce_rotates_real_router_accounts_end_to_end_without_network(monkeypatch, tmp_path):
    router_home = yggdrasil_gateway._candidate_router_package()
    if router_home is None:
        pytest.skip("Yggdrasil Router checkout not present")

    router_config = tmp_path / "router.json"
    router_config.write_text(
        json.dumps(
            {
                "failure_threshold": 3,
                "accounts": [
                    {
                        "account_id": "deepseek-a",
                        "provider": "deepseek",
                        "secret_ref": "env://TEST_LILITH_ROUTER_ACCOUNT_A",
                        "capabilities": ["chat"],
                        "models": ["deepseek-v4-flash"],
                        "status": "healthy",
                        "priority": 100,
                        "quota_remaining": 1,
                        "token_quota_remaining": 1000,
                    },
                    {
                        "account_id": "deepseek-b",
                        "provider": "deepseek",
                        "secret_ref": "env://TEST_LILITH_ROUTER_ACCOUNT_B",
                        "capabilities": ["chat"],
                        "models": ["deepseek-v4-flash"],
                        "status": "healthy",
                        "priority": 90,
                        "quota_remaining": 5,
                        "token_quota_remaining": 1000,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    state_path = tmp_path / "orchestration.json"
    monkeypatch.setenv("YGGDRASIL_ORCHESTRATION_STATE", str(state_path))
    monkeypatch.setenv(yggdrasil_gateway.ROUTER_CONFIG_ENV, str(router_config))
    monkeypatch.setenv(yggdrasil_gateway.ROUTER_HOME_ENV, str(router_home))
    monkeypatch.setenv(yggdrasil_gateway.ROUTER_MODE_ENV, "enforce")
    monkeypatch.setenv("TEST_LILITH_ROUTER_ACCOUNT_A", "opaque-account-a")
    monkeypatch.setenv("TEST_LILITH_ROUTER_ACCOUNT_B", "opaque-account-b")
    yggdrasil_gateway.clear_gateway_cache()

    profile = SimpleNamespace(
        model="deepseek-v4-flash",
        api_key=SecretStr("preset-default"),
        base_url="https://example.invalid/v1",
        max_tokens=None,
        temperature=None,
    )
    cfg = SimpleNamespace(
        provider="deepseek",
        model="deepseek-v4-flash",
        api_key=SecretStr("preset-default"),
        base_url="https://example.invalid/v1",
        providers={"deepseek": profile},
        max_tokens=100,
        temperature=0.0,
    )
    seen_credentials: list[str] = []

    class Provider:
        def __init__(self, routed_cfg):
            seen_credentials.append(routed_cfg.api_key.get_secret_value())

        async def complete(self, messages, *, tools=None, **kwargs):
            return {
                "content": "ok",
                "usage": {"prompt_tokens": 7, "completion_tokens": 4, "total_tokens": 11},
                "tool_calls": [],
            }

        async def close(self):
            return None

    cfg_mod = types.ModuleType("lilith_cli.config")
    cfg_mod.load_config = lambda: cfg
    cfg_mod.require_supported_provider = lambda name: name
    cfg_mod.require_supported_model = lambda provider, model: model
    main_mod = types.ModuleType("lilith_cli.main")
    main_mod._load_subagent_presets = lambda config_path=None: {
        "batch-deepseek": {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "system_prompt": "test",
        }
    }
    providers_mod = types.ModuleType("lilith_cli.providers")
    providers_mod.LLMProviderWrapper = Provider
    providers_mod.ToolCall = type("ToolCall", (), {})
    providers_mod.ToolResult = type("ToolResult", (), {})
    for module in (cfg_mod, main_mod, providers_mod):
        monkeypatch.setitem(sys.modules, module.__name__, module)

    tool = DelegateSubagentTool()
    first = tool.execute(preset="batch-deepseek", prompt="first")
    second = tool.execute(preset="batch-deepseek", prompt="second")

    assert first.success is True
    assert second.success is True
    assert first.data["gateway_route"]["account_id"] == "deepseek-a"
    assert second.data["gateway_route"]["account_id"] == "deepseek-b"
    assert seen_credentials == ["opaque-account-a", "opaque-account-b"]

    state = OrchestrationStateStore(state_path).get()
    assert [task["routing"]["gateway"]["account_id"] for task in state["tasks"]] == [
        "deepseek-a",
        "deepseek-b",
    ]
    serialized = repr(state)
    assert "opaque-account-a" not in serialized
    assert "opaque-account-b" not in serialized
    assert "TEST_LILITH_ROUTER_ACCOUNT_A" not in serialized
    assert "TEST_LILITH_ROUTER_ACCOUNT_B" not in serialized
    yggdrasil_gateway.clear_gateway_cache()
