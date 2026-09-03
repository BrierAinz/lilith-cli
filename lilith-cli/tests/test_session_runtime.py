"""Tests for the stable session runtime boundary."""

from __future__ import annotations

from typing import Any

from lilith_cli import session_runtime


def test_create_session_delegates_to_compatible_agent_class(monkeypatch) -> None:
    config = object()
    provider = object()
    expected = object()
    captured: dict[str, Any] = {}

    class FakeAgentSession:
        def __new__(cls, passed_config, *, provider=None):
            captured["config"] = passed_config
            captured["provider"] = provider
            return expected

    import lilith_cli.agent as agent_module

    monkeypatch.setattr(agent_module, "AgentSession", FakeAgentSession)

    result = session_runtime.create_session(config, provider=provider)

    assert result is expected
    assert captured == {"config": config, "provider": provider}


def test_session_runtime_module_does_not_eagerly_import_agent() -> None:
    """The concrete implementation is loaded only when the factory is called."""

    assert "AgentSession" not in vars(session_runtime)
