"""Tests for the /subagents slash command.

Sub-agents are real LLM-backed presets, so the heavy lifting is mocked:
``_load_subagent_presets`` returns scripted preset dicts and
``LLMProviderWrapper`` is replaced with ``FakeProvider`` (scriptable
``complete()`` responses, no network).
"""

from __future__ import annotations

import asyncio
import sys
import types
from typing import Any

import pytest


def _run(coro):
    return asyncio.run(coro)


# ── Test doubles ──────────────────────────────────────────────────────


class _FakeProvider:
    """Stand-in for ``LLMProviderWrapper`` used by ``SubagentsCommand._test``."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.completed_calls: list[dict[str, Any]] = []
        self.closed = 0

    async def complete(self, messages, *, tools=None, **kwargs):
        self.completed_calls.append({"messages": messages, "tools": tools, "kwargs": kwargs})
        if not self._responses:
            raise AssertionError("FakeProvider ran out of scripted responses")
        return self._responses.pop(0)

    async def close(self):
        self.closed += 1


def _make_cfg(providers: dict[str, Any] | None = None) -> Any:
    """Build a YggdrasilConfig-like object that SubagentsCommand accepts."""
    from types import SimpleNamespace

    from lilith_cli.config import YggdrasilConfig

    base = YggdrasilConfig(provider="local", model="local-model")
    base.providers = dict(providers or {})
    return base


def _install_fake_main(monkeypatch, presets, cfg):
    """Patch ``lilith_cli.main._load_subagent_presets`` and
    ``lilith_cli.config.load_config`` to return our scripted data."""
    from lilith_cli import main as main_mod

    monkeypatch.setattr(
        main_mod, "_load_subagent_presets", lambda config_path=None: presets
    )
    monkeypatch.setattr(
        "lilith_cli.config.load_config", lambda: cfg, raising=True
    )


def _install_fake_providers(monkeypatch, responses_by_provider: dict[str, list]):
    """Replace ``LLMProviderWrapper`` so /subagents test never touches the network.

    ``responses_by_provider`` maps ``provider_name -> list of response dicts``
    returned in order on consecutive ``complete()`` calls.
    """
    from lilith_cli import providers as prov_mod

    remaining = {k: list(v) for k, v in responses_by_provider.items()}

    class _Wrapper:
        def __init__(self, cfg):
            self.cfg = cfg
            self.provider_name = (cfg.provider or "").lower()

        async def complete(self, messages, *, tools=None, **kwargs):
            queue = remaining.setdefault(self.provider_name, [])
            if not queue:
                raise AssertionError(
                    f"No scripted response for provider {self.provider_name!r}"
                )
            return queue.pop(0)

        async def close(self):
            return None

    # ``_probe_max_tokens`` calls wrapper.complete too — leave the queues
    # sized accordingly.
    monkeypatch.setattr(prov_mod, "LLMProviderWrapper", _Wrapper)


# ── /subagents list ───────────────────────────────────────────────────


def test_subagents_list_empty_presets(fake_session, monkeypatch, capsys):
    from lilith_cli.commands import SubagentsCommand

    _install_fake_main(monkeypatch, presets={}, cfg=_make_cfg())
    _run(SubagentsCommand(fake_session).execute("list"))

    out = capsys.readouterr().out
    assert "No hay presets" in out


def test_subagents_list_renders_table(fake_session, monkeypatch, capsys):
    from lilith_cli.commands import SubagentsCommand

    presets = {
        "fast": {"provider": "sakana", "model": "fugu-ultra"},
        "cheap": {"provider": "opencode", "model": "glm-5.2"},
        "broken": {"provider": "mystery_provider", "model": "???"},
    }
    from types import SimpleNamespace

    cfg = _make_cfg(
        {
            "sakana": SimpleNamespace(api_key="x", base_url="u", model="fugu-ultra",
                                     temperature=None, max_tokens=None,
                                     use_responses=None),
            "opencode": SimpleNamespace(api_key="x", base_url="u", model="glm-5.2",
                                        temperature=None, max_tokens=None,
                                        use_responses=None),
        }
    )
    _install_fake_main(monkeypatch, presets, cfg)
    _run(SubagentsCommand(fake_session).execute("list"))

    out = capsys.readouterr().out
    # Every preset name appears in the table.
    for preset_name in presets:
        assert preset_name in out, f"missing {preset_name} in output"
    # Providers that match config get the "ok" status; the unknown one
    # gets the "no en config.yaml" marker.
    assert "no en config.yaml" in out


def test_subagents_list_accepts_ls_alias(fake_session, monkeypatch, capsys):
    from lilith_cli.commands import SubagentsCommand

    _install_fake_main(monkeypatch, presets={"a": {"provider": "p", "model": "m"}}, cfg=_make_cfg())
    _run(SubagentsCommand(fake_session).execute("ls"))
    out = capsys.readouterr().out
    assert "a" in out


# ── /subagents test ───────────────────────────────────────────────────


def _ok_response(content: str = "PONG echo") -> dict[str, Any]:
    return {
        "content": content,
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
    }


def _err_response(error: dict | None = None) -> dict[str, Any]:
    # Mirrors what LLMProviderWrapper.complete returns on provider errors.
    return {
        "content": "",
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "error": error or {"message": "auth failed", "type": "AuthError"},
    }


def test_subagents_test_unknown_target_errors(fake_session, monkeypatch, capsys):
    from lilith_cli.commands import SubagentsCommand

    presets = {"a": {"provider": "sakana", "model": "fugu-ultra"}}
    _install_fake_main(monkeypatch, presets, _make_cfg({"sakana": _profile()}))
    _install_fake_providers(monkeypatch, {"sakana": [_ok_response()]})

    _run(SubagentsCommand(fake_session).execute("test does_not_exist"))

    out = capsys.readouterr().out
    assert "no existe" in out


def test_subagents_test_handles_empty_presets(fake_session, monkeypatch, capsys):
    from lilith_cli.commands import SubagentsCommand

    _install_fake_main(monkeypatch, presets={}, cfg=_make_cfg())
    _run(SubagentsCommand(fake_session).execute("test"))
    out = capsys.readouterr().out
    assert "No hay presets" in out


def test_subagents_test_runs_in_parallel(fake_session, monkeypatch, capsys):
    """Two presets, two providers, all complete() calls fan out and the
    rendered table contains both."""
    from lilith_cli.commands import SubagentsCommand

    presets = {
        "fast": {"provider": "sakana", "model": "fugu-ultra"},
        "cheap": {"provider": "opencode", "model": "glm-5.2"},
    }
    cfg = _make_cfg(
        {
            "sakana": _profile(model="fugu-ultra"),
            "opencode": _profile(model="glm-5.2"),
        }
    )
    _install_fake_main(monkeypatch, presets, cfg)
    # Two responses per provider: the PONG ping + the probe_max_tokens ping.
    _install_fake_providers(
        monkeypatch,
        {
            "sakana": [_ok_response("hi from sakana"), _ok_response("probe ok")],
            "opencode": [_ok_response("hi from opencode"), _ok_response("probe ok")],
        },
    )

    _run(SubagentsCommand(fake_session).execute("test"))

    out = capsys.readouterr().out
    # Both presets show up in the rendered table.
    assert "fast" in out
    assert "cheap" in out


def test_subagents_test_provider_error_renders_row(fake_session, monkeypatch, capsys):
    """A provider that raises is still rendered as a row with the error."""
    from lilith_cli.commands import SubagentsCommand

    presets = {"broken": {"provider": "sakana", "model": "fugu-ultra"}}
    cfg = _make_cfg({"sakana": _profile(model="fugu-ultra")})
    _install_fake_main(monkeypatch, presets, cfg)

    # Make ``complete()`` raise so the CLI exercises the
    # ``except Exception as exc`` branch in ``_ping_one``.
    from lilith_cli import providers as prov_mod

    class _RaisingWrapper:
        def __init__(self, cfg):
            pass

        async def complete(self, messages, *, tools=None, **kwargs):
            raise RuntimeError("auth failed: 401 invalid api key")

        async def close(self):
            return None

    monkeypatch.setattr(prov_mod, "LLMProviderWrapper", _RaisingWrapper)

    _run(SubagentsCommand(fake_session).execute("test broken"))

    out = capsys.readouterr().out
    assert "broken" in out
    # Error surfaces somewhere in the rendered table.
    assert "auth failed" in out


# ── Command metadata ─────────────────────────────────────────────────


def test_subagents_command_metadata():
    from lilith_cli.commands import SubagentsCommand

    # Build a minimal session — execute shouldn't be called.
    cmd = SubagentsCommand(object())
    assert cmd.name == "subagents"
    assert "sa" in cmd.aliases


def test_subagents_unknown_subcommand_prints_usage(fake_session, monkeypatch, capsys):
    from lilith_cli.commands import SubagentsCommand

    _install_fake_main(monkeypatch, presets={}, cfg=_make_cfg())
    _run(SubagentsCommand(fake_session).execute("frobnicate"))
    out = capsys.readouterr().out
    assert "Uso:" in out


# ── helpers ───────────────────────────────────────────────────────────


def _profile(
    api_key: str = "sk-test",
    base_url: str = "https://fake.example/v1",
    model: str = "fake-model",
):
    from types import SimpleNamespace

    return SimpleNamespace(
        api_key=api_key,
        base_url=base_url,
        model=model,
        temperature=None,
        max_tokens=None,
        use_responses=None,
    )


# ── ITEM 1 (tanda 6): reasoning_content handling ─────────────────────


def test_subagents_test_accepts_reasoning_only(fake_session, monkeypatch, capsys):
    """A response with empty ``content`` but non-empty
    ``reasoning_content`` (Kimi / DeepSeek / GLM-5.1 pattern) must
    count as ok and surface the "solo reasoning" tag in the table."""
    from lilith_cli.commands import SubagentsCommand

    presets = {"kimi": {"provider": "sakana", "model": "kimi-k2"}}
    cfg = _make_cfg({"sakana": _profile(model="kimi-k2")})
    _install_fake_main(monkeypatch, presets, cfg)

    # Empty content, non-empty reasoning — exactly the failure mode
    # that drove the fix. Two responses per provider (ping + probe).
    reasoning_only = {
        "content": "",
        "reasoning_content": "thinking about PONG...",
        "usage": {"prompt_tokens": 5, "completion_tokens": 64, "total_tokens": 69},
    }
    _install_fake_providers(
        monkeypatch,
        {"sakana": [reasoning_only, _ok_response("probe ok")]},
    )

    _run(SubagentsCommand(fake_session).execute("test kimi"))

    out = capsys.readouterr().out
    assert "kimi" in out
    assert "ok (raz)" in out
    # The previous false-positive label must be gone for this row.
    assert "respuesta vacía" not in out


def test_subagents_test_max_tokens_is_64(fake_session, monkeypatch, capsys):
    """The PONG ping must request >= 64 tokens so reasoning_content
    does not consume the entire budget."""
    from lilith_cli.commands import SubagentsCommand

    presets = {"p": {"provider": "sakana", "model": "m"}}
    cfg = _make_cfg({"sakana": _profile(model="m")})
    _install_fake_main(monkeypatch, presets, cfg)

    captured_max_tokens: list[int] = []

    from lilith_cli import providers as prov_mod

    class _CapturingWrapper:
        def __init__(self, cfg):
            pass

        async def complete(self, messages, *, tools=None, **kwargs):
            captured_max_tokens.append(int(kwargs.get("max_tokens", -1)))
            return _ok_response("ok")

        async def close(self):
            return None

    monkeypatch.setattr(prov_mod, "LLMProviderWrapper", _CapturingWrapper)

    _run(SubagentsCommand(fake_session).execute("test p"))

    # First call is the ping (>= 64); second is the 65536 probe.
    assert captured_max_tokens, "wrapper.complete was never called"
    assert captured_max_tokens[0] >= 64


def test_subagents_test_real_empty_is_still_error(fake_session, monkeypatch, capsys):
    """A response with neither ``content`` nor ``reasoning_content``
    must still render as an empty/error row — the fix only changed
    the reasoning-only case."""
    from lilith_cli.commands import SubagentsCommand

    presets = {"silent": {"provider": "sakana", "model": "m"}}
    cfg = _make_cfg({"sakana": _profile(model="m")})
    _install_fake_main(monkeypatch, presets, cfg)

    truly_empty = {
        "content": "",
        "reasoning_content": "",
        "usage": {"prompt_tokens": 5, "completion_tokens": 0, "total_tokens": 5},
    }
    _install_fake_providers(
        monkeypatch,
        {"sakana": [truly_empty, _ok_response("probe ok")]},
    )

    _run(SubagentsCommand(fake_session).execute("test silent"))

    out = capsys.readouterr().out
    assert "silent" in out
    assert "respuesta vacía" in out