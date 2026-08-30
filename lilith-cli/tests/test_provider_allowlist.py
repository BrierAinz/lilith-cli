"""Provider allow-list and legacy configuration migration tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import yaml

from lilith_cli.config import (
    SUPPORTED_PROVIDERS,
    activate_provider_config_file,
    load_config,
    migrate_provider_config_file,
    require_supported_model,
    require_supported_provider,
)
from lilith_cli.providers import LLMProviderWrapper


def test_load_config_prunes_retired_provider_in_memory(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        """
provider: retired
model: retired-model
providers:
  retired:
    api_key: retired-secret
    base_url: https://retired.invalid/v1
    model: retired-model
  deepseek:
    api_key: deepseek-secret
    base_url: https://wrong.invalid/v1
    model: deepseek-v4-flash
""",
        encoding="utf-8",
    )

    cfg = load_config(path)

    assert cfg.provider == "deepseek"
    assert cfg.model == "deepseek-v4-flash"
    assert set(cfg.providers) == set(SUPPORTED_PROVIDERS)
    assert cfg.base_url == "https://api.deepseek.com/v1"
    assert cfg.providers["deepseek"].base_url == "https://api.deepseek.com/v1"
    assert cfg.providers["grok"].base_url == "https://api.x.ai/v1"
    assert cfg.providers["sakana"].base_url == "https://api.sakana.ai/v1"


def test_migrate_provider_file_removes_retired_credentials(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        """
provider: retired
providers:
  retired:
    api_key: must-disappear
    base_url: https://retired.invalid/v1
    model: retired-model
""",
        encoding="utf-8",
    )

    migrate_provider_config_file(path)
    migrated_text = path.read_text(encoding="utf-8")
    migrated = yaml.safe_load(migrated_text)

    assert set(migrated["providers"]) == set(SUPPORTED_PROVIDERS)
    assert migrated["provider"] == "deepseek"
    assert "must-disappear" not in migrated_text
    assert migrated["providers"]["deepseek"]["api_key"] == "${DEEPSEEK_API_KEY}"
    assert migrated["providers"]["grok"]["api_key"] == "${XAI_API_KEY}"
    assert migrated["providers"]["sakana"]["api_key"] == "${SAKANA_API_KEY}"


def test_provider_and_model_validators_reject_unknown_routes():
    with pytest.raises(ValueError, match="Proveedor no soportado"):
        require_supported_provider("retired")
    with pytest.raises(ValueError, match="Modelo no soportado"):
        require_supported_model("deepseek", "grok-4.5")


def test_sakana_route_is_supported():
    assert require_supported_provider("SAKANA") == "sakana"
    assert require_supported_model("sakana", "fugu-ultra") == "fugu-ultra"


def test_activate_sakana_persists_env_reference_without_resolving_secret(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "provider: deepseek\nproviders:\n  deepseek:\n"
        "    api_key: ${DEEPSEEK_API_KEY}\n"
        "    model: deepseek-v4-flash\n",
        encoding="utf-8",
    )

    activate_provider_config_file("sakana", path)
    activated_text = path.read_text(encoding="utf-8")
    activated = yaml.safe_load(activated_text)

    assert activated["provider"] == "sakana"
    assert activated["model"] == "fugu-ultra"
    assert activated["base_url"] == "https://api.sakana.ai/v1"
    assert activated["api_key"] == "${SAKANA_API_KEY}"
    assert activated["providers"]["sakana"]["api_key"] == "${SAKANA_API_KEY}"


@pytest.mark.asyncio
async def test_wrapper_blocks_retired_provider_before_http():
    cfg = SimpleNamespace(
        provider="retired",
        model="retired-model",
        providers={},
        retry_max=0,
    )
    provider = LLMProviderWrapper(cfg)

    with pytest.raises(ValueError, match="Proveedor no soportado"):
        await provider.complete([{"role": "user", "content": "hello"}])
