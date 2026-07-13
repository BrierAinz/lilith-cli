"""LLM provider abstraction for Yggdrasil."""

import os
from collections.abc import Iterator
from dataclasses import dataclass

import requests


@dataclass
class ProviderConfig:
    """Configuration for an LLM provider."""

    name: str
    base_url: str
    api_key: str
    model: str
    headers: dict = None

    def __post_init__(self):
        if self.headers is None:
            self.headers = {}


# Known provider profiles
PROVIDERS = {
    "mimo": {
        "base_url": "https://token-plan-sgp.xiaomimimo.com/v1",
        "env_key": "MIMO_API_KEY",
        "default_model": "MiMo-V2.5-Pro",
    },
    "lm-studio": {
        "base_url": "http://localhost:1234/v1",
        "env_key": None,
        "default_model": "local-model",
    },
}


def get_provider(name: str) -> ProviderConfig:
    """Get provider config by name."""
    if name not in PROVIDERS:
        raise ValueError(f"Unknown provider: {name}. Available: {list(PROVIDERS.keys())}")

    p = PROVIDERS[name]
    api_key = os.getenv(p["env_key"], "") if p["env_key"] else ""

    return ProviderConfig(
        name=name,
        base_url=p["base_url"],
        api_key=api_key,
        model=p["default_model"],
    )


def chat_completion(
    provider: ProviderConfig,
    messages: list[dict],
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 2048,
    stream: bool = False,
) -> dict | Iterator[str]:
    """Send a chat completion request to the provider."""
    url = f"{provider.base_url}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {provider.api_key}",
        **provider.headers,
    }
    payload = {
        "model": model or provider.model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": stream,
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json()
