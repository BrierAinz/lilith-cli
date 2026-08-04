"""Tests for DelegateSubagentTool — agentic mini-loop + structured output.

All tests mock the lazy ``lilith_cli.*`` imports by pre-injecting stub
modules into ``sys.modules`` before calling ``execute()``. The
``LLMProviderWrapper`` is replaced with ``FakeProvider`` that scripts
``complete()`` responses from a queue — no network, no real model.
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

import lilith_tools.delegate as delegate_mod
from lilith_tools.delegate import DelegateSubagentTool


# ── Test doubles ────────────────────────────────────────────────────────


class _FakeProvider:
    """Scriptable stand-in for ``LLMProviderWrapper``.

    Each call to ``complete()`` pops the next response from ``responses``;
    if the queue is exhausted the test fails (catches accidental extra
    LLM calls). ``close()`` is a no-op.
    """

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def complete(self, messages, *, tools=None, **kwargs):  # noqa: ANN001
        self.calls.append({"messages": list(messages), "tools": tools, "kwargs": kwargs})
        if not self._responses:
            raise AssertionError("FakeProvider ran out of scripted responses")
        return self._responses.pop(0)

    async def close(self) -> None:
        return None


def _make_cfg() -> Any:
    """Build a minimal stand-in for ``YggdrasilConfig``.

    We only touch the fields ``DelegateSubagentTool.execute`` reads or
    mutates; using a SimpleNamespace keeps the test independent of
    Pydantic validation details.
    """
    from types import SimpleNamespace

    profile = SimpleNamespace(
        api_key="sk-test",
        base_url="https://fake.example/v1",
        model="fake-model",
        temperature=None,
        max_tokens=None,
        use_responses=None,
    )
    return SimpleNamespace(
        provider="fake",
        model="fake-model",
        api_key="sk-test",
        base_url="https://fake.example/v1",
        providers={"fake": profile},
        temperature=0.7,
        max_tokens=4096,
    )


def _install_fake_lilith_cli(monkeypatch, fake_provider: _FakeProvider) -> None:
    """Inject stub ``lilith_cli.config``/``main``/``providers`` modules.

    The stub modules expose the three names ``DelegateSubagentTool.execute``
    imports lazily: ``load_config``, ``_load_subagent_presets``,
    ``LLMProviderWrapper``. After this call, invoking ``execute(...)`` will
    resolve those names from the stubs without touching the real CLI.
    """

    cfg = _make_cfg()
    presets = {
        "fake-preset": {
            "provider": "fake",
            "model": "fake-model",
            "system_prompt": "stub system prompt",
        },
    }

    cfg_mod = types.ModuleType("lilith_cli.config")
    cfg_mod.load_config = lambda: cfg  # type: ignore[attr-defined]
    main_mod = types.ModuleType("lilith_cli.main")
    main_mod._load_subagent_presets = lambda config_path=None: presets  # type: ignore[attr-defined]
    providers_mod = types.ModuleType("lilith_cli.providers")
    providers_mod.LLMProviderWrapper = lambda _cfg: fake_provider  # type: ignore[attr-defined]
    # The structured-retry helpers also import this lazily — keep it on
    # the stub so the name resolves.
    providers_mod.ToolCall = type("ToolCall", (), {})  # type: ignore[attr-defined]
    providers_mod.ToolResult = type("ToolResult", (), {})  # type: ignore[attr-defined]

    for mod in (cfg_mod, main_mod, providers_mod):
        monkeypatch.setitem(sys.modules, mod.__name__, mod)

    # Also patch the names already imported by name in delegate module
    # (defensive: the real imports are lazy but if a previous test
    # already resolved them they would shadow our stubs).
    monkeypatch.setattr(delegate_mod, "_FakeProviderRef", fake_provider, raising=False)


def _make_tool_response(
    content: str = "",
    *,
    tool_calls: list[dict[str, Any]] | None = None,
    usage: dict[str, int] | None = None,
    finish_reason: str = "stop",
) -> dict[str, Any]:
    return {
        "content": content,
        "tool_calls": tool_calls or [],
        "usage": usage or {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        "finish_reason": finish_reason,
    }


def _tc(name: str, args: dict[str, Any] | str, tc_id: str = "call_1") -> dict[str, Any]:
    """Build a tool_call dict in the shape providers return."""
    if isinstance(args, dict):
        args_str = json.dumps(args)
    else:
        args_str = args
    return {"id": tc_id, "name": name, "arguments": args_str}


# ── (e) One-shot default is intact ─────────────────────────────────────


class TestOneShotUnchanged:
    """The default mode must still do exactly one ``complete()`` call and
    surface the content as before. No new tool execution paths leak in.
    """

    def test_one_shot_returns_provider_content(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        provider = _FakeProvider(
            [_make_tool_response(content="hello from sub-agent", finish_reason="stop")]
        )
        _install_fake_lilith_cli(monkeypatch, provider)

        result = DelegateSubagentTool().execute(preset="fake-preset", prompt="do thing")

        assert result.success is True
        assert result.data["preset"] == "fake-preset"
        assert result.data["provider"] == "fake"
        assert result.data["content"] == "hello from sub-agent"
        assert result.data["usage"]["total_tokens"] == 15
        # Exactly one LLM call.
        assert len(provider.calls) == 1
        # No tools handed to the provider.
        assert provider.calls[0]["tools"] is None

    def test_missing_prompt_or_preset_returns_error(self):
        tool = DelegateSubagentTool()
        assert tool.execute(preset="", prompt="x").success is False
        assert tool.execute(preset="x", prompt="").success is False


# ── (a) Sandbox: writes inside, rejects outside ───────────────────────


class TestSandbox:
    """Direct tests of the sandbox helpers — no LLM involved."""

    def test_resolve_workdir_creates_unique_dirs(self, tmp_path):
        wd1 = delegate_mod._resolve_workdir(None, "preset", tmp_path)
        wd2 = delegate_mod._resolve_workdir(None, "preset", tmp_path)
        assert wd1.exists()
        assert wd2.exists()
        assert wd1 != wd2
        assert wd1.name.startswith("preset-")

    def test_resolve_workdir_honors_explicit_path(self, tmp_path):
        target = tmp_path / "mybox"
        wd = delegate_mod._resolve_workdir(str(target), "preset", tmp_path)
        assert wd == target.resolve()
        assert wd.exists()

    def test_sanitize_path_rejects_parent_traversal(self, tmp_path):
        wd = (tmp_path / "sandbox").resolve()
        wd.mkdir()
        with pytest.raises(ValueError, match="outside the sandbox"):
            delegate_mod._sanitize_path("../../../etc/passwd", wd, "file_read")

    def test_sanitize_path_rejects_absolute_outside(self, tmp_path):
        wd = (tmp_path / "sandbox").resolve()
        wd.mkdir()
        outside = tmp_path / "other" / "secret.txt"
        with pytest.raises(ValueError, match="outside the sandbox"):
            delegate_mod._sanitize_path(str(outside), wd, "file_read")

    def test_sanitize_path_accepts_relative_inside(self, tmp_path):
        wd = (tmp_path / "sandbox").resolve()
        wd.mkdir()
        p = delegate_mod._sanitize_path("notes.md", wd, "file_write")
        assert p == (wd / "notes.md").resolve()

    def test_sanitize_path_accepts_absolute_inside(self, tmp_path):
        wd = (tmp_path / "sandbox").resolve()
        wd.mkdir()
        target = wd / "sub" / "file.txt"
        p = delegate_mod._sanitize_path(str(target), wd, "file_read")
        assert p == target.resolve()

    def test_run_agentic_tool_rejects_disallowed_tool(self, tmp_path):
        wd = (tmp_path / "sandbox").resolve()
        wd.mkdir()
        out = delegate_mod._run_agentic_tool("shell_exec", {"cmd": "ls"}, wd, [])
        assert json.loads(out)["ok"] is False
        assert "not available" in json.loads(out)["error"]

    def test_run_agentic_tool_writes_only_inside(self, tmp_path):
        wd = (tmp_path / "sandbox").resolve()
        wd.mkdir()
        written: list[str] = []
        out = delegate_mod._run_agentic_tool(
            "file_write", {"path": "a/b.txt", "content": "hi"}, wd, written
        )
        assert json.loads(out)["ok"] is True
        # Path separators differ between POSIX and Windows; compare as POSIX.
        assert [p.replace("\\", "/") for p in written] == ["a/b.txt"]
        assert (wd / "a" / "b.txt").read_text() == "hi"

    def test_run_agentic_tool_blocks_outside_via_sanitize(self, tmp_path):
        wd = (tmp_path / "sandbox").resolve()
        wd.mkdir()
        written: list[str] = []
        out = delegate_mod._run_agentic_tool(
            "file_write",
            {"path": str(tmp_path / "escape.txt"), "content": "x"},
            wd,
            written,
        )
        assert json.loads(out)["ok"] is False
        assert "outside the sandbox" in json.loads(out)["error"]
        assert written == []  # nothing was written

    def test_run_agentic_tool_file_read_missing(self, tmp_path):
        wd = (tmp_path / "sandbox").resolve()
        wd.mkdir()
        out = delegate_mod._run_agentic_tool("file_read", {"path": "missing.txt"}, wd, [])
        assert json.loads(out)["ok"] is False
        assert "not found" in json.loads(out)["error"]


# ── Agentic loop integration: writes inside / outside ──────────────────


class TestAgenticLoop:
    """End-to-end through the public ``execute(..., agentic=True)`` API."""

    def test_writes_files_inside_workdir(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        # Turn 1: model asks to write a file. Turn 2: model emits final text.
        provider = _FakeProvider(
            [
                _make_tool_response(
                    tool_calls=[_tc("file_write", {"path": "out.txt", "content": "hi"})],
                    finish_reason="tool_calls",
                ),
                _make_tool_response(content="done", finish_reason="stop"),
            ]
        )
        _install_fake_lilith_cli(monkeypatch, provider)

        result = DelegateSubagentTool().execute(
            preset="fake-preset",
            prompt="write something",
            agentic=True,
            workdir=str(tmp_path / "sandbox1"),
        )

        assert result.success is True
        assert "out.txt" in result.data["files_written"]
        assert "written_files" not in result.data
        wd = Path(result.data["workdir"])
        assert (wd / "out.txt").read_text() == "hi"
        assert result.data["turns_used"] == 2

    def test_rejects_writes_outside_workdir_via_model_args(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        outside = tmp_path / "escape.txt"
        provider = _FakeProvider(
            [
                _make_tool_response(
                    tool_calls=[_tc("file_write", {"path": str(outside), "content": "pwn"})],
                    finish_reason="tool_calls",
                ),
                _make_tool_response(content="aborted", finish_reason="stop"),
            ]
        )
        _install_fake_lilith_cli(monkeypatch, provider)

        result = DelegateSubagentTool().execute(
            preset="fake-preset",
            prompt="escape",
            agentic=True,
            workdir=str(tmp_path / "sandbox2"),
        )

        assert result.success is True
        assert outside.exists() is False  # nothing actually written
        assert result.data["files_written"] == []

    def test_blocks_parent_traversal_in_tool_call(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        provider = _FakeProvider(
            [
                _make_tool_response(
                    tool_calls=[
                        _tc(
                            "file_write",
                            {"path": "../escape.txt", "content": "bad"},
                        )
                    ],
                    finish_reason="tool_calls",
                ),
                _make_tool_response(content="halted", finish_reason="stop"),
            ]
        )
        _install_fake_lilith_cli(monkeypatch, provider)

        result = DelegateSubagentTool().execute(
            preset="fake-preset",
            prompt="escape",
            agentic=True,
            workdir=str(tmp_path / "sandbox3"),
        )

        assert result.success is True
        assert (tmp_path / "escape.txt").exists() is False
        assert result.data["files_written"] == []


# ── (c) FEATURE C minimal: per-turn progress log ─────────────────────


class TestAgenticProgressLog:
    """Tanda 4 FEATURE C minimal version: each tool the agentic loop
    executes emits one ``logger.info`` line with ``turn``, tool name,
    and ok/error status. The richer Live panel is deferred."""

    def test_log_emitted_for_each_tool_call(self, monkeypatch, tmp_path, caplog):
        import logging as _logging

        monkeypatch.chdir(tmp_path)
        # Turn 1: write a file (will succeed). Turn 2: final text.
        provider = _FakeProvider(
            [
                _make_tool_response(
                    tool_calls=[_tc("file_write", {"path": "ok.txt", "content": "x"})],
                    finish_reason="tool_calls",
                ),
                _make_tool_response(content="done", finish_reason="stop"),
            ]
        )
        _install_fake_lilith_cli(monkeypatch, provider)

        with caplog.at_level(_logging.INFO, logger="lilith_tools.delegate"):
            result = DelegateSubagentTool().execute(
                preset="fake-preset",
                prompt="go",
                agentic=True,
                workdir=str(tmp_path / "sandbox_log_ok"),
            )

        assert result.success is True
        ok_lines = [
            r for r in caplog.records
            if r.name == "lilith_tools.delegate"
            and "delegate turn 1: file_write ok" in r.getMessage()
        ]
        assert len(ok_lines) == 1, caplog.text

    def test_log_marks_errors_for_rejected_paths(self, monkeypatch, tmp_path, caplog):
        import logging as _logging

        monkeypatch.chdir(tmp_path)
        outside = tmp_path / "leak.txt"
        provider = _FakeProvider(
            [
                _make_tool_response(
                    tool_calls=[_tc("file_write", {"path": str(outside), "content": "pwn"})],
                    finish_reason="tool_calls",
                ),
                _make_tool_response(content="stop", finish_reason="stop"),
            ]
        )
        _install_fake_lilith_cli(monkeypatch, provider)

        with caplog.at_level(_logging.INFO, logger="lilith_tools.delegate"):
            result = DelegateSubagentTool().execute(
                preset="fake-preset",
                prompt="leak",
                agentic=True,
                workdir=str(tmp_path / "sandbox_log_err"),
            )

        assert result.success is True
        err_lines = [
            r for r in caplog.records
            if r.name == "lilith_tools.delegate"
            and "delegate turn 1: file_write error" in r.getMessage()
        ]
        assert len(err_lines) == 1, caplog.text
        # The error message from the sandbox must appear in the log.
        assert "outside the sandbox" in err_lines[0].getMessage()

    def test_log_fires_once_per_tool_call_across_turns(
        self, monkeypatch, tmp_path, caplog
    ):
        """A 3-turn loop (two tool calls + final) should produce exactly
        two log lines — one per executed tool."""
        import logging as _logging

        monkeypatch.chdir(tmp_path)
        provider = _FakeProvider(
            [
                _make_tool_response(
                    tool_calls=[_tc("file_write", {"path": "a.txt", "content": "1"})],
                    finish_reason="tool_calls",
                ),
                _make_tool_response(
                    tool_calls=[_tc("file_write", {"path": "b.txt", "content": "2"})],
                    finish_reason="tool_calls",
                ),
                _make_tool_response(content="done", finish_reason="stop"),
            ]
        )
        _install_fake_lilith_cli(monkeypatch, provider)

        with caplog.at_level(_logging.INFO, logger="lilith_tools.delegate"):
            DelegateSubagentTool().execute(
                preset="fake-preset",
                prompt="multi",
                agentic=True,
                workdir=str(tmp_path / "sandbox_multi"),
            )

        delegate_logs = [
            r for r in caplog.records
            if r.name == "lilith_tools.delegate"
            and "delegate turn" in r.getMessage()
        ]
        assert len(delegate_logs) == 2
        assert "turn 1" in delegate_logs[0].getMessage()
        assert "turn 2" in delegate_logs[1].getMessage()


# ── (b) max_turns cuts the loop and returns partial ────────────────────


class TestMaxTurns:
    """When the loop never sees a no-tool-calls turn, the run must
    surface a partial result instead of raising."""

    def test_max_turns_caps_loop_and_returns_partial(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        # Each scripted response keeps asking for another file_write.
        # With max_turns=2 the loop runs 2 iterations then stops.
        provider = _FakeProvider(
            [
                _make_tool_response(
                    tool_calls=[_tc("file_write", {"path": "a.txt", "content": "a"})],
                    finish_reason="tool_calls",
                ),
                _make_tool_response(
                    tool_calls=[_tc("file_write", {"path": "b.txt", "content": "b"})],
                    finish_reason="tool_calls",
                ),
            ]
        )
        _install_fake_lilith_cli(monkeypatch, provider)

        result = DelegateSubagentTool().execute(
            preset="fake-preset",
            prompt="loop forever",
            agentic=True,
            workdir=str(tmp_path / "sandbox_max"),
            max_turns=2,
        )

        assert result.success is True
        assert result.data["partial"] is True
        assert result.data["turns_used"] == 2
        assert "max_turns" in result.data["content"]
        # The two files written before the cutoff ARE on disk.
        wd = Path(result.data["workdir"])
        assert (wd / "a.txt").read_text() == "a"
        assert (wd / "b.txt").read_text() == "b"
        # No third call attempted.
        assert len(provider.calls) == 2


# ── (c) Structured validation + retry on failure ───────────────────────


class TestStructuredOneShot:
    """``structured=True`` validates the response and retries once with a
    corrective message that includes the concrete errors."""

    def test_structured_valid_response(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        good = {"summary": "all good", "status": "completed", "confidence": 0.9}
        provider = _FakeProvider(
            [_make_tool_response(content=json.dumps(good), finish_reason="stop")]
        )
        _install_fake_lilith_cli(monkeypatch, provider)

        result = DelegateSubagentTool().execute(
            preset="fake-preset", prompt="x", structured=True
        )

        assert result.success is True
        assert result.data["structured"]["summary"] == "all good"
        assert result.data["validation_errors"] == []
        assert result.data["raw_content"] is None

    def test_structured_invalid_then_valid_after_retry(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        bad = {"summary": "x"}  # missing status
        good = {"summary": "fixed", "status": "completed"}
        provider = _FakeProvider(
            [
                _make_tool_response(content=json.dumps(bad), finish_reason="stop"),
                _make_tool_response(content=json.dumps(good), finish_reason="stop"),
            ]
        )
        _install_fake_lilith_cli(monkeypatch, provider)

        result = DelegateSubagentTool().execute(
            preset="fake-preset", prompt="x", structured=True
        )

        assert result.success is True
        assert result.data["structured"]["summary"] == "fixed"
        # Two LLM calls: original + retry.
        assert len(provider.calls) == 2
        # The retry message must include the concrete error.
        retry_user = provider.calls[1]["messages"][-1]
        assert retry_user["role"] == "user"
        assert "'status'" in retry_user["content"]

    def test_structured_invalid_twice_returns_raw_content(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        original_raw = json.dumps({"summary": "no status"})
        retry_raw = "still not json"
        provider = _FakeProvider(
            [
                _make_tool_response(content=original_raw, finish_reason="stop"),
                _make_tool_response(content=retry_raw, finish_reason="stop"),
            ]
        )
        _install_fake_lilith_cli(monkeypatch, provider)

        result = DelegateSubagentTool().execute(
            preset="fake-preset", prompt="x", structured=True
        )

        assert result.success is False
        assert result.data["raw_content"] == original_raw
        assert result.data["structured"] is None
        assert result.data["validation_errors"]  # has at least one error
        assert "validation" in result.error.lower()

    def test_structured_strips_markdown_fences(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        fenced = "```json\n" + json.dumps({"summary": "ok", "status": "completed"}) + "\n```"
        provider = _FakeProvider(
            [_make_tool_response(content=fenced, finish_reason="stop")]
        )
        _install_fake_lilith_cli(monkeypatch, provider)

        result = DelegateSubagentTool().execute(
            preset="fake-preset", prompt="x", structured=True
        )

        assert result.success is True
        assert result.data["structured"]["summary"] == "ok"


# ── (d) Structured + agentic combined ──────────────────────────────────


class TestStructuredAgentic:
    """When both flags are on, validation runs against the loop's final
    assistant message. Failure triggers a no-tools retry against the same
    message history."""

    def test_agentic_then_structured_valid(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        provider = _FakeProvider(
            [
                _make_tool_response(
                    tool_calls=[_tc("file_write", {"path": "x.txt", "content": "x"})],
                    finish_reason="tool_calls",
                ),
                _make_tool_response(
                    content=json.dumps({"summary": "wrote x", "status": "completed"}),
                    finish_reason="stop",
                ),
            ]
        )
        _install_fake_lilith_cli(monkeypatch, provider)

        result = DelegateSubagentTool().execute(
            preset="fake-preset",
            prompt="write and report",
            agentic=True,
            workdir=str(tmp_path / "sandbox_str"),
            structured=True,
        )

        assert result.success is True
        assert result.data["structured"]["summary"] == "wrote x"
        assert result.data["files_written"] == ["x.txt"]
        # No retry needed → two LLM calls only.
        assert len(provider.calls) == 2

    def test_agentic_then_structured_invalid_retries_with_errors(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        retry_success = {"summary": "ok after retry", "status": "completed"}
        provider = _FakeProvider(
            [
                # Loop turns:
                _make_tool_response(content="not json at all", finish_reason="stop"),
                # Structured retry turn (no tools):
                _make_tool_response(
                    content=json.dumps(retry_success), finish_reason="stop"
                ),
            ]
        )
        _install_fake_lilith_cli(monkeypatch, provider)

        result = DelegateSubagentTool().execute(
            preset="fake-preset",
            prompt="x",
            agentic=True,
            workdir=str(tmp_path / "sandbox_str2"),
            structured=True,
        )

        assert result.success is True
        assert result.data["structured"]["summary"] == "ok after retry"
        # The retry must have been invoked with tools=None.
        retry_call = provider.calls[1]
        assert retry_call["tools"] is None
        # And the corrective message must contain concrete validation
        # errors — "response is not JSON" is the parser error from
        # "not json at all".
        last_user = retry_call["messages"][-1]
        assert last_user["role"] == "user"
        assert "not JSON" in last_user["content"] or "expected" in last_user["content"].lower()

    def test_agentic_then_structured_double_failure(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        original_raw = "still garbage"
        provider = _FakeProvider(
            [
                _make_tool_response(content=original_raw, finish_reason="stop"),
                _make_tool_response(content="also garbage", finish_reason="stop"),
            ]
        )
        _install_fake_lilith_cli(monkeypatch, provider)

        result = DelegateSubagentTool().execute(
            preset="fake-preset",
            prompt="x",
            agentic=True,
            workdir=str(tmp_path / "sandbox_str3"),
            structured=True,
        )

        assert result.success is False
        assert result.data["raw_content"] == original_raw
        assert result.data["structured"] is None
        assert result.data["validation_errors"]


# ── Robustness: truncated tool args (tanda-1 pattern) ──────────────────


class TestRobustToolArgs:
    """The mini-loop must surface a corrective tool_result when the model
    emits non-JSON / truncated tool arguments, mirroring the agent loop."""

    def test_truncated_args_yields_corrective_tool_result(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        provider = _FakeProvider(
            [
                _make_tool_response(
                    tool_calls=[
                        _tc(
                            "file_write",
                            '{"path": "x.txt", "content": "hello',  # truncated JSON
                        )
                    ],
                    finish_reason="length",
                ),
                _make_tool_response(content="done", finish_reason="stop"),
            ]
        )
        _install_fake_lilith_cli(monkeypatch, provider)

        result = DelegateSubagentTool().execute(
            preset="fake-preset",
            prompt="x",
            agentic=True,
            workdir=str(tmp_path / "sandbox_trunc"),
        )

        # The file was NOT written (the broken call was rejected).
        assert result.data["files_written"] == []
        assert result.success is True
        # The second LLM call must have been issued (loop continues after
        # the corrective tool_result, mirroring the agent loop's behaviour).
        assert len(provider.calls) == 2
        # Find the corrective tool_result the first turn emitted; it must
        # carry the "JSON válido" hint that tells the model to retry with
        # smaller payloads.
        second_call_messages = provider.calls[1]["messages"]
        corrective = next(
            (
                m for m in second_call_messages
                if m.get("role") == "tool" and "JSON válido" in m.get("content", "")
            ),
            None,
        )
        assert corrective is not None, (
            f"expected a corrective tool_result with 'JSON válido', got: {second_call_messages}"
        )


# ── Sandbox applied to all tool names ──────────────────────────────────


class TestAllAgenticToolsAreSandboxed:
    """Direct call to each restricted tool with an outside path."""

    @pytest.mark.parametrize(
        "tool_name,args",
        [
            ("file_read", {"path": "/etc/hostname"}),
            ("file_write", {"path": "/tmp/zz.txt", "content": "x"}),
            ("directory_list", {"path": "/etc"}),
            ("file_edit", {"path": "/tmp/zz.txt", "old_string": "a", "new_string": "b"}),
        ],
    )
    def test_each_tool_rejects_outside(self, tool_name, args, tmp_path):
        wd = (tmp_path / "sandbox").resolve()
        wd.mkdir()
        written: list[str] = []
        out = json.loads(
            delegate_mod._run_agentic_tool(tool_name, args, wd, written)
        )
        assert out["ok"] is False
        assert "outside the sandbox" in out["error"]
        assert written == []

# ── ITEM 2 (tanda 6): file_append in the agentic loop ─────────────────


class TestAgenticFileAppend:
    """file_append is exposed in agentic mode alongside file_write."""

    def test_file_append_is_in_agentic_allowlist(self):
        assert "file_append" in delegate_mod._AGENTIC_TOOL_NAMES

    def test_file_append_writes_inside_sandbox(self, tmp_path):
        wd = (tmp_path / "sandbox").resolve()
        wd.mkdir()
        written: list[str] = []
        out = json.loads(
            delegate_mod._run_agentic_tool(
                "file_append", {"path": "notes.txt", "content": "line1\n"}, wd, written
            )
        )
        assert out["ok"] is True
        assert out["appended"] is False  # file did not exist before
        assert (wd / "notes.txt").read_text(encoding="utf-8") == "line1\n"
        assert written and written[0].replace("\\", "/") == "notes.txt"

    def test_file_append_chunks_accumulate(self, tmp_path):
        wd = (tmp_path / "sandbox").resolve()
        wd.mkdir()
        written: list[str] = []
        for chunk in ("head", "-", "tail"):
            out = json.loads(
                delegate_mod._run_agentic_tool(
                    "file_append", {"path": "log.txt", "content": chunk}, wd, written
                )
            )
            assert out["ok"] is True
        # file_append must NOT add a separator newline — concatenation
        # is the caller's responsibility.
        assert (wd / "log.txt").read_text(encoding="utf-8") == "head-tail"

    def test_file_append_blocks_outside_sandbox(self, tmp_path):
        wd = (tmp_path / "sandbox").resolve()
        wd.mkdir()
        written: list[str] = []
        out = json.loads(
            delegate_mod._run_agentic_tool(
                "file_append",
                {"path": str(tmp_path / "escape.txt"), "content": "x"},
                wd,
                written,
            )
        )
        assert out["ok"] is False
        assert "outside the sandbox" in out["error"]
        assert written == []

    def test_file_append_schema_in_tool_definitions(self):
        defs = delegate_mod._tool_definitions()
        names = [d["function"]["name"] for d in defs]
        assert "file_append" in names
        spec = next(d for d in defs if d["function"]["name"] == "file_append")
        assert "path" in spec["function"]["parameters"]["required"]
        assert "content" in spec["function"]["parameters"]["required"]
