"""Tests for ConclaveTool — parallel fan-out across Hlidskjalf presets.

The conclave reuses :class:`DelegateSubagentTool` for each preset, so the
test strategy is to monkeypatch the symbol the conclave imported
(``lilith_tools.conclave.DelegateSubagentTool``) with a stub that
returns scripted results. That keeps the tests fast and free of
network or real provider config while exercising the real parallel
fan-out, the per-preset timeout, and the "one preset fails, others
survive" contract.
"""

from __future__ import annotations

import sys
import threading
import time
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import lilith_tools.conclave as conclave_mod
from lilith_tools.conclave import (
    DEFAULT_PRESET_TIMEOUT_SECONDS,
    ConclaveTool,
)
from lilith_tools.delegate import DelegateSubagentTool


# ── Stubs ───────────────────────────────────────────────────────────────


class _StubDelegate:
    """Stand-in for ``DelegateSubagentTool`` that returns scripted results.

    Each invocation pops the next scripted (preset→result) mapping and
    returns the configured ``ToolResult``. If the preset name isn't in
    the script, the stub raises ``AssertionError`` to make the test
    fail loudly — keeps the assertion close to the data.
    """

    def __init__(self, script: dict[str, Any]) -> None:
        self._script = dict(script)
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def __call__(self) -> "_StubDelegate":
        return self  # DelegateSubagentTool() is constructed with no args

    def execute(self, **kwargs: Any):  # noqa: ANN001
        from lilith_tools.base import ToolResult

        with self._lock:
            self.calls.append(dict(kwargs))
        preset = kwargs.get("preset")
        if preset not in self._script:
            raise AssertionError(
                f"StubDelegate: no scripted result for preset {preset!r}"
            )
        item = self._script[preset]
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            return item(kwargs)
        if isinstance(item, BaseException):
            raise item
        # Default: treat the script value as a ToolResult or a dict.
        if isinstance(item, ToolResult):
            return item
        if isinstance(item, dict):
            return ToolResult(
                success=item.get("success", True),
                data=item.get("data"),
                error=item.get("error", ""),
            )
        raise AssertionError(
            f"StubDelegate: unsupported script value type {type(item).__name__}"
        )


def _ok_result(preset: str, model: str, content: str, usage: dict | None = None):
    """Build a successful one-shot ToolResult shaped like delegate's."""
    from lilith_tools.base import ToolResult

    return ToolResult(
        success=True,
        data={
            "preset": preset,
            "provider": "fake",
            "model": model,
            "content": content,
            "usage": usage or {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        },
        error="",
    )


def _fail_result(preset: str, model: str, error: str):
    """Build a failed ToolResult (delegate returned success=False)."""
    from lilith_tools.base import ToolResult

    return ToolResult(
        success=False,
        data={
            "preset": preset,
            "provider": "fake",
            "model": model,
            "content": "",
            "usage": {},
        },
        error=error,
    )


def _install_stub(monkeypatch, stub: _StubDelegate) -> None:
    """Patch the ``DelegateSubagentTool`` symbol the conclave imported."""
    monkeypatch.setattr(conclave_mod, "DelegateSubagentTool", stub)


# ── (a) Happy path: 2 presets in parallel, all return content ──────────


class TestConclaveHappyPath:
    def test_two_presets_return_all_responses(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        stub = _StubDelegate({
            "investigador-minimax": _ok_result(
                "investigador-minimax", "minimax-m1", "answer A"
            ),
            "grok-research": _ok_result(
                "grok-research", "grok-2", "answer B"
            ),
        })
        _install_stub(monkeypatch, stub)

        result = ConclaveTool().execute(
            question="Compare the trade-offs",
            presets=["investigador-minimax", "grok-research"],
        )

        assert result.success is True
        assert result.data["ok_count"] == 2
        assert result.data["failed_count"] == 0
        rows = result.data["responses"]
        assert [r["preset"] for r in rows] == [
            "investigador-minimax", "grok-research"
        ]
        assert rows[0]["content"] == "answer A"
        assert rows[0]["model"] == "minimax-m1"
        assert rows[1]["content"] == "answer B"
        assert rows[1]["model"] == "grok-2"
        # Both calls were dispatched; kwargs forwarded.
        assert {c["preset"] for c in stub.calls} == {
            "investigador-minimax", "grok-research"
        }
        for call in stub.calls:
            assert call["prompt"] == "Compare the trade-offs"

    def test_default_presets_when_unspecified(self, monkeypatch, tmp_path):
        """No `presets` kwarg → uses investigador-minimax + grok-research."""
        monkeypatch.chdir(tmp_path)
        stub = _StubDelegate({
            "investigador-minimax": _ok_result("investigador-minimax", "m1", "A"),
            "grok-research": _ok_result("grok-research", "grok", "B"),
        })
        _install_stub(monkeypatch, stub)

        result = ConclaveTool().execute(question="anything")

        assert result.success is True
        assert result.data["presets_requested"] == [
            "investigador-minimax", "grok-research"
        ]

    def test_structured_true_forwards_flag(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        from lilith_tools.base import ToolResult

        def _structured_ok(_kwargs):
            return ToolResult(
                success=True,
                data={
                    "preset": "investigador-minimax",
                    "provider": "fake",
                    "model": "m1",
                    "content": "structured summary",
                    "usage": {"prompt_tokens": 9, "completion_tokens": 4, "total_tokens": 13},
                    "structured": {"summary": "structured summary", "status": "completed"},
                    "validation_errors": [],
                },
                error="",
            )

        stub = _StubDelegate({
            "investigador-minimax": _structured_ok,
            "grok-research": _ok_result("grok-research", "grok", "plain"),
        })
        _install_stub(monkeypatch, stub)

        result = ConclaveTool().execute(
            question="structured?",
            presets=["investigador-minimax", "grok-research"],
            structured=True,
        )

        assert result.success is True
        # structured=True forwarded on both calls.
        assert all(c["structured"] is True for c in stub.calls)
        # First row carries the structured payload.
        assert result.data["responses"][0]["structured"]["summary"] == "structured summary"

    def test_max_tokens_forwarded(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        stub = _StubDelegate({
            "investigador-minimax": _ok_result("investigador-minimax", "m1", "A"),
            "grok-research": _ok_result("grok-research", "grok", "B"),
        })
        _install_stub(monkeypatch, stub)

        ConclaveTool().execute(
            question="x",
            presets=["investigador-minimax", "grok-research"],
            max_tokens=512,
        )

        for call in stub.calls:
            assert call["max_tokens"] == 512


# ── (b) Failure isolation: one preset explodes, the other survives ─────


class TestConclaveFailureIsolation:
    def test_one_preset_raises_does_not_break_others(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.chdir(tmp_path)
        stub = _StubDelegate({
            "investigador-minimax": _ok_result("investigador-minimax", "m1", "ok"),
            "grok-research": RuntimeError("provider 500"),
        })
        _install_stub(monkeypatch, stub)

        result = ConclaveTool().execute(
            question="x",
            presets=["investigador-minimax", "grok-research"],
        )

        # The conclave still succeeds: at least one preset made it.
        assert result.success is True
        rows = result.data["responses"]
        assert rows[0]["error"] == ""
        assert rows[0]["content"] == "ok"
        assert "RuntimeError" in rows[1]["error"]
        assert rows[1]["content"] == ""
        assert result.data["ok_count"] == 1
        assert result.data["failed_count"] == 1

    def test_preset_returning_failure_result_is_reported_as_error(
        self, monkeypatch, tmp_path
    ):
        """``ToolResult(success=False, error='...')`` from delegate surfaces
        in the row's error field; it does not raise out of the worker.
        """
        monkeypatch.chdir(tmp_path)
        stub = _StubDelegate({
            "investigador-minimax": _fail_result(
                "investigador-minimax", "m1", "preset not in config"
            ),
            "grok-research": _ok_result("grok-research", "grok", "ok"),
        })
        _install_stub(monkeypatch, stub)

        result = ConclaveTool().execute(
            question="x",
            presets=["investigador-minimax", "grok-research"],
        )

        assert result.success is True
        rows = result.data["responses"]
        assert rows[0]["error"] == "preset not in config"
        assert rows[1]["error"] == ""
        assert rows[1]["content"] == "ok"

    def test_all_presets_fail_makes_conclave_fail(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        stub = _StubDelegate({
            "investigador-minimax": RuntimeError("boom 1"),
            "grok-research": RuntimeError("boom 2"),
        })
        _install_stub(monkeypatch, stub)

        result = ConclaveTool().execute(
            question="x",
            presets=["investigador-minimax", "grok-research"],
        )

        assert result.success is False
        assert "todos los presets fallaron" in result.error
        assert result.data["ok_count"] == 0
        assert result.data["failed_count"] == 2


# ── (c) Per-preset timeout ─────────────────────────────────────────────


class TestConclaveTimeout:
    def test_hanging_preset_times_out(self, monkeypatch, tmp_path):
        """A preset whose worker thread is still running after the per-preset
        budget appears in the row with ``error='timeout'`` and the rest of
        the conclave proceeds normally.
        """
        monkeypatch.chdir(tmp_path)

        release = threading.Event()

        def _hang(_kwargs):
            # Block well past the per-preset budget; the main thread will
            # mark us as timed out and we keep blocking until ``release``
            # so the daemon isn't a surprise on Windows test teardown.
            release.wait(timeout=5.0)
            from lilith_tools.base import ToolResult
            return ToolResult(success=True, data={}, error="")

        stub = _StubDelegate({
            "investigador-minimax": _hang,
            "grok-research": _ok_result("grok-research", "grok", "fast answer"),
        })
        _install_stub(monkeypatch, stub)

        try:
            t0 = time.perf_counter()
            result = ConclaveTool().execute(
                question="x",
                presets=["investigador-minimax", "grok-research"],
                timeout=0.5,  # tight budget for the test
            )
            elapsed = time.perf_counter() - t0
        finally:
            # Free the hung worker so the test process can exit cleanly.
            release.set()

        assert result.success is True
        rows = result.data["responses"]
        assert rows[0]["error"] == "timeout"
        assert rows[0]["content"] == ""
        assert rows[1]["content"] == "fast answer"
        assert rows[1]["error"] == ""
        # The conclave did not wait the full hang duration; it gave up at
        # the per-preset ceiling. (Allow generous slack for thread teardown.)
        assert elapsed < 3.0, f"conclave waited too long: {elapsed:.2f}s"


# ── (d) Argument validation ────────────────────────────────────────────


class TestConclaveValidation:
    def test_missing_question_returns_error(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        stub = _StubDelegate({})  # no calls should reach the stub
        _install_stub(monkeypatch, stub)

        result = ConclaveTool().execute(question="")
        assert result.success is False
        assert "'question' es requerido" in result.error

    def test_too_few_presets_returns_error(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        stub = _StubDelegate({})
        _install_stub(monkeypatch, stub)

        result = ConclaveTool().execute(
            question="x", presets=["only-one"],
        )
        assert result.success is False
        assert "al menos 2" in result.error

    def test_too_many_presets_returns_error(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        stub = _StubDelegate({})
        _install_stub(monkeypatch, stub)

        result = ConclaveTool().execute(
            question="x",
            presets=["a", "b", "c", "d", "e"],
        )
        assert result.success is False
        assert "maximo 4" in result.error


# ── (e) ToolRegistry integration ───────────────────────────────────────


class TestConclaveRegistered:
    def test_conclave_is_in_tool_registry(self):
        from lilith_tools.registry import ToolRegistry

        # The decorator fires at import time, so the conclave class is
        # already registered when this test runs (it was imported via
        # ``lilith_tools/__init__.py``).
        assert ToolRegistry.get("conclave") is ConclaveTool
        assert "conclave" in ToolRegistry.list_tools()
