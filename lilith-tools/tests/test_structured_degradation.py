"""Tests for the structured-output degradation chain in DelegateSubagentTool.

The degradation chain lives in :meth:`DelegateSubagentTool._enforce_structured`
(one-shot) and is also exposed via the public ``execute(..., structured=True)``
path. These tests drive the chain entirely with mocks:

* 400 on json_schema  → fall back to json_object
* 400 on both         → fall back to prompt-only
* ``\\`\\`\\`json`` fences are stripped before validation
* Invalid response after the whole chain + 1 corrective retry → success=False
  with ``raw_content`` preserved
* Happy path returns ``data['structured']`` validated against TASK_SCHEMA

The 1-token / 1-call shape is enforced by an explicit assertion on the fake
provider's call log so an accidental extra LLM round trips would fail the
suite.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import lilith_tools.delegate as delegate_mod
from lilith_tools.delegate import DelegateSubagentTool


# ── Test doubles ────────────────────────────────────────────────────────


class _FakeProvider:
    """Scriptable stand-in for ``LLMProviderWrapper``.

    Each ``complete()`` call pops the next scripted response. If the queue
    is exhausted the test fails — that catches accidental extra LLM calls
    and makes the expected call count a property of the test data.
    """

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def complete(self, messages, *, tools=None, **kwargs):  # noqa: ANN001
        self.calls.append({
            "messages": list(messages),
            "tools": tools,
            "kwargs": dict(kwargs),
        })
        if not self._responses:
            raise AssertionError("FakeProvider ran out of scripted responses")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self) -> None:
        return None


def _make_cfg() -> Any:
    """Build a minimal stand-in for ``YggdrasilConfig``."""
    profile = SimpleNamespace(
        api_key="sk-test",
        base_url="https://fake.example/v1",
        model="deepseek-v4-flash",
        temperature=None,
        max_tokens=None,
    )
    return SimpleNamespace(
        provider="deepseek",
        model="deepseek-v4-flash",
        api_key="sk-test",
        base_url="https://fake.example/v1",
        providers={"deepseek": profile},
        temperature=0.7,
        max_tokens=4096,
    )


def _install_fake_lilith_cli(monkeypatch, fake_provider: _FakeProvider) -> None:
    """Inject stub ``lilith_cli.*`` modules so ``DelegateSubagentTool.execute``
    can resolve its lazy imports without touching the real CLI package.
    """
    cfg = _make_cfg()
    presets = {
        "fake-preset": {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "system_prompt": "stub system prompt",
        },
    }

    cfg_mod = types.ModuleType("lilith_cli.config")
    cfg_mod.load_config = lambda: cfg  # type: ignore[attr-defined]
    cfg_mod.require_supported_provider = lambda name: name  # type: ignore[attr-defined]
    cfg_mod.require_supported_model = lambda provider, model: model  # type: ignore[attr-defined]
    main_mod = types.ModuleType("lilith_cli.main")
    main_mod._load_subagent_presets = lambda config_path=None: presets  # type: ignore[attr-defined]
    providers_mod = types.ModuleType("lilith_cli.providers")
    providers_mod.LLMProviderWrapper = lambda _cfg: fake_provider  # type: ignore[attr-defined]
    providers_mod.ToolCall = type("ToolCall", (), {})  # type: ignore[attr-defined]
    providers_mod.ToolResult = type("ToolResult", (), {})  # type: ignore[attr-defined]

    for mod in (cfg_mod, main_mod, providers_mod):
        monkeypatch.setitem(sys.modules, mod.__name__, mod)


def _ok_structured(summary: str = "all good") -> dict[str, Any]:
    """A minimal valid TASK_SCHEMA object."""
    return {
        "summary": summary,
        "status": "completed",
        "deliverables": [],
        "blockers": [],
        "next_steps": [],
        "confidence": 0.9,
    }


def _make_tool_response(
    content: str = "",
    *,
    usage: dict[str, int] | None = None,
) -> dict[str, Any]:
    return {
        "content": content,
        "tool_calls": [],
        "usage": usage or {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        "finish_reason": "stop",
    }


class _BadRequestError(Exception):
    """An exception that mimics a 400-on-json_schema API rejection.

    Carries ``status_code = 400`` so the delegate's
    ``_looks_like_unsupported_format_error`` heuristic would also match it
    in production. Used here to drive the degradation chain off the
    mock provider.
    """

    status_code = 400

    def __init__(self, message: str = "json_schema unsupported") -> None:
        super().__init__(f"400 Bad Request: {message}")
        self.message = message


# ── (1) 400 on json_schema → reintento json_object ──────────────────────


class TestDegradationJsonSchemaRejected:
    """When the provider rejects ``response_format=json_schema`` (HTTP 400)
    the chain MUST fall back to ``response_format=json_object``.
    """

    def test_400_on_json_schema_falls_back_to_json_object(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.chdir(tmp_path)
        # One-shot call returns a string that isn't valid TASK_SCHEMA JSON
        # (so the wrapper's content alone is not acceptable) — the chain
        # must drive the degradation.
        first = _make_tool_response(content="not valid json")
        # Degradation level A (json_schema) raises 400; level B (json_object)
        # returns the structured object as plain text (we still get JSON back
        # from the model — the chain validates the string itself).
        level_b_response = _make_tool_response(
            content=json.dumps(_ok_structured("recovered via json_object"))
        )
        provider = _FakeProvider([first, _BadRequestError("json_schema unsupported"), level_b_response])
        _install_fake_lilith_cli(monkeypatch, provider)

        result = DelegateSubagentTool().execute(
            preset="fake-preset",
            prompt="do the thing",
            structured=True,
        )

        # The chain eventually succeeded.
        assert result.success is True, f"expected success, got {result.error!r}"
        assert result.data is not None
        assert result.data["structured"]["summary"] == "recovered via json_object"
        assert result.data["validation_errors"] == []
        assert result.data["raw_content"] is None  # only set on full failure

        # The chain made exactly 3 LLM calls: the one-shot + level A + level B.
        assert len(provider.calls) == 3
        # Call 0: the one-shot (no response_format kwarg).
        assert "response_format" not in provider.calls[0]["kwargs"]
        # Call 1: level A — json_schema payload.
        rf_a = provider.calls[1]["kwargs"].get("response_format")
        assert rf_a is not None and rf_a.get("type") == "json_schema"
        # Call 2: level B — json_object payload (the fallback that succeeded).
        rf_b = provider.calls[2]["kwargs"].get("response_format")
        assert rf_b == {"type": "json_object"}


# ── (2) 400 on ambos → prompt-only ──────────────────────────────────────


class TestDegradationBothRejected:
    """When both json_schema and json_object are rejected (HTTP 400 on each)
    the chain MUST fall back to prompt-only (no ``response_format``).
    """

    def test_400_on_both_falls_back_to_prompt_only(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.chdir(tmp_path)
        first = _make_tool_response(content="nope")
        level_a_err = _BadRequestError("json_schema unsupported")
        level_b_err = _BadRequestError("json_object unsupported")
        level_c_response = _make_tool_response(
            content=json.dumps(_ok_structured("recovered via prompt-only"))
        )
        provider = _FakeProvider([first, level_a_err, level_b_err, level_c_response])
        _install_fake_lilith_cli(monkeypatch, provider)

        result = DelegateSubagentTool().execute(
            preset="fake-preset",
            prompt="do the thing",
            structured=True,
        )

        assert result.success is True, f"expected success, got {result.error!r}"
        assert result.data["structured"]["summary"] == "recovered via prompt-only"
        assert result.data["validation_errors"] == []

        # 4 calls: one-shot + levels A, B, C.
        assert len(provider.calls) == 4
        # Level C — the prompt-only fallback — must not carry response_format.
        rf_c = provider.calls[3]["kwargs"].get("response_format")
        assert rf_c is None, f"expected no response_format on prompt-only, got {rf_c!r}"


# ── (3) Fences ```json se limpian ────────────────────────────────────────


class TestFenceStripping:
    """Models often wrap JSON in ``\\`\\`\\`json ... \\`\\`\\``` fences. The chain
    MUST strip the fences before validation; otherwise valid structured
    output would be rejected on the first pass.
    """

    def test_markdown_fences_are_stripped(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        fenced = "```json\n" + json.dumps(_ok_structured("fenced ok")) + "\n```"
        first = _make_tool_response(content=fenced)
        provider = _FakeProvider([first])
        _install_fake_lilith_cli(monkeypatch, provider)

        result = DelegateSubagentTool().execute(
            preset="fake-preset",
            prompt="do the thing",
            structured=True,
        )

        # Fenced content was parsed on the first attempt — no degradation
        # round trips were needed.
        assert result.success is True, f"expected success, got {result.error!r}"
        assert result.data["structured"]["summary"] == "fenced ok"
        assert result.data["validation_errors"] == []
        # Only the one-shot call ran — the fence-stripping path resolved it
        # without invoking the degradation chain.
        assert len(provider.calls) == 1

    def test_unclosed_fence_is_stripped(self, monkeypatch, tmp_path):
        """Some models emit an opening fence without the matching close.
        The parser must still recover the inner object.
        """
        monkeypatch.chdir(tmp_path)
        # Open fence, no close — parser falls through to the "starts with
        # backtick" path and re-extracts the object.
        content = "```json\n" + json.dumps(_ok_structured("unclosed fence")) + "\n"
        first = _make_tool_response(content=content)
        provider = _FakeProvider([first])
        _install_fake_lilith_cli(monkeypatch, provider)

        result = DelegateSubagentTool().execute(
            preset="fake-preset",
            prompt="do the thing",
            structured=True,
        )

        assert result.success is True
        assert result.data["structured"]["summary"] == "unclosed fence"


# ── (4) Invalid response after degradation + 1 corrective retry ────────


class TestCorrectiveRetryExhausts:
    """If the model keeps emitting invalid JSON across all three degradation
    levels, the chain returns ``success=False`` and preserves the model's
    last attempt in ``data['raw_content']`` so the orchestrator can still
    inspect it. (The corrective retry is internal to the chain — what the
    caller sees is one ToolResult.)
    """

    def test_invalid_after_chain_returns_raw_content(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.chdir(tmp_path)
        invalid = "still not json — the model is confused"
        first = _make_tool_response(content=invalid)
        # Levels A, B, C each return garbage.
        garbage = _make_tool_response(content="nope nope nope")
        provider = _FakeProvider([first, garbage, garbage, garbage])
        _install_fake_lilith_cli(monkeypatch, provider)

        result = DelegateSubagentTool().execute(
            preset="fake-preset",
            prompt="do the thing",
            structured=True,
        )

        assert result.success is False
        assert result.data is not None
        # raw_content carries the most recent attempt; the structured object
        # is None and validation_errors surfaces the reason.
        assert result.data["raw_content"] == "nope nope nope"
        assert result.data["structured"] is None
        assert result.data["validation_errors"], "expected non-empty errors"
        assert result.error, "expected an error string on failure"

        # 4 calls: one-shot + 3 degradation levels.
        assert len(provider.calls) == 4


# ── (5) Happy path: data['structured'] validado en el caso feliz ────────


class TestHappyPathStructured:
    """The one-shot provider call returns a valid TASK_SCHEMA object. The
    chain must NOT trigger any degradation; the response is parsed and
    surfaced verbatim under ``data['structured']``.
    """

    def test_happy_path_returns_validated_structured(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.chdir(tmp_path)
        structured = _ok_structured("happy path")
        first = _make_tool_response(content=json.dumps(structured))
        provider = _FakeProvider([first])
        _install_fake_lilith_cli(monkeypatch, provider)

        result = DelegateSubagentTool().execute(
            preset="fake-preset",
            prompt="do the thing",
            structured=True,
        )

        assert result.success is True
        assert result.data["structured"] == structured
        assert result.data["validation_errors"] == []
        assert result.data["raw_content"] is None
        # content mirrors the summary — orchestrator-friendly shorthand.
        assert result.data["content"] == "happy path"
        # No degradation round trips on the happy path.
        assert len(provider.calls) == 1
