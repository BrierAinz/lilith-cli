"""Tests for `lilith delegate` subcommand flags (tanda 14, ITEM 1).

The ``delegate`` subcommand in ``lilith_cli.main`` historically was a thin
one-shot wrapper around ``AgentSession`` + ``run_oneshot``. Tanda 14 adds
five delegation flags (``--preset``, ``--agentic``, ``--structured``,
``--max-tokens``, ``--max-turns``) that route the call through
``lilith_tools.delegate.DelegateSubagentTool`` instead — same machinery the
REPL/orchestrator already use, so the call lands in orchestration state and
supports the full agentic/structured/multi-turn surface.

These tests cover the routing decisions and the rendered output contract:

* Compatibility: no new flag -> original one-shot path (``AgentSession``
  + ``run_oneshot``) is invoked. We assert ``run_oneshot`` is called and
  ``DelegateSubagentTool`` is NOT.
* Routing: any new flag -> ``DelegateSubagentTool().execute(**kwargs)`` is
  invoked with the correct kwargs (preset, prompt, agentic, structured,
  max_tokens, max_turns). ``run_oneshot`` must NOT be called.
* Rendering: on success the renderer prints the content (or pretty-prints
  structured JSON), plus the ``files_written`` / ``turns_used`` / ``usage``
  footer; on failure it prints a red error and the process exits 1.
* ``--preset`` overrides target; without ``--preset`` the target is used as
  the preset name.
* ITEM 3: when routed through the tool, the call is recorded in
  ``orchestration_state`` automatically (verified by an isolated test that
  uses ``YGGDRASIL_ORCHESTRATION_STATE`` env var to redirect the JSON file).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ── Test doubles ────────────────────────────────────────────────────────


class _StubToolResult:
    """Real ``ToolResult`` instance — needed because the CLI does an
    ``isinstance(result, ToolResult)`` defensive check on the tool path."""

    def __init__(self, success: bool, data: Any = None, error: str = "") -> None:
        from lilith_tools.base import ToolResult
        self._inner = ToolResult(
            success=success,
            data=data if data is not None else {},
            error=error,
        )

    # Proxy attribute access to the inner real ToolResult so it is an
    # exact duck-typed equivalent for the renderer.
    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def __repr__(self) -> str:
        return repr(self._inner)


class _StubDelegateTool:
    """Stand-in for ``lilith_tools.delegate.DelegateSubagentTool``.

    The real tool is heavy (provider I/O, workdir, structured validation).
    Here we only need to verify: (a) it was constructed, (b) ``execute()``
    was called once with the expected kwargs, and (c) the result we script
    flows back through the CLI renderer.
    """

    def __init__(self, script: Any = None) -> None:
        self._script = script or _StubToolResult(
            success=True,
            data={
                "preset": "ejecutor-kimi",
                "provider": "kimi",
                "model": "kimi-for-coding",
                "content": "stubbed one-shot content",
                "usage": {"prompt_tokens": 12, "completion_tokens": 7},
            },
        )
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self.instances: list["_StubDelegateTool"] = []

    def __call__(self) -> "_StubDelegateTool":
        # Mimic ``DelegateSubagentTool()`` no-arg construction.
        with self._lock:
            self.instances.append(self)
        return self

    def execute(self, **kwargs: Any) -> _StubToolResult:
        with self._lock:
            self.calls.append(kwargs)
        if isinstance(self._script, BaseException):
            raise self._script
        return self._script


class _RecordingOneshot:
    """Stand-in for ``repl.run_oneshot`` (which is an async function)."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, str]] = []

    async def __call__(self, session: Any, text: str) -> None:
        self.calls.append((session, text))


# ── Helpers ─────────────────────────────────────────────────────────────


def _install_stub(monkeypatch: pytest.MonkeyPatch, stub: _StubDelegateTool) -> None:
    """Patch ``DelegateSubagentTool`` at the source module.

    The CLI does a lazy import inside the function body, so we have to
    patch the symbol on ``lilith_tools.delegate`` — not on the consumer.
    """
    import lilith_tools.delegate as delegate_mod

    monkeypatch.setattr(delegate_mod, "DelegateSubagentTool", stub)


def _install_oneshot(monkeypatch: pytest.MonkeyPatch, rec: _RecordingOneshot) -> None:
    """Patch ``repl.run_oneshot`` so the legacy path is observable without LLM I/O."""
    import lilith_cli.repl as repl_mod

    monkeypatch.setattr(repl_mod, "run_oneshot", rec)


# ── (a) Backward compatibility — no new flag → legacy path ─────────────


def test_delegate_no_flags_uses_oneshot_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without any new flag, the subcommand must keep the original one-shot behaviour."""
    import lilith_cli.agent as agent_mod
    import lilith_cli.main as main_mod
    from lilith_cli.config import ProviderProfile, YggdrasilConfig

    cfg = YggdrasilConfig(provider="kimi", model="kimi-for-coding")
    cfg.providers = {
        "kimi": ProviderProfile(provider="kimi", model="kimi-for-coding", api_key="k"),
    }

    stub = _StubDelegateTool()  # should NEVER be called
    rec = _RecordingOneshot()
    _install_stub(monkeypatch, stub)
    _install_oneshot(monkeypatch, rec)

    fake_session = MagicMock(name="fake_AgentSession_instance")
    fake_session_cls = MagicMock(return_value=fake_session, name="AgentSession")
    with patch.object(main_mod, "load_config", return_value=cfg), \
         patch.object(agent_mod, "AgentSession", fake_session_cls):
        main_mod.delegate(target="kimi", text="hello world")

    # Legacy path: run_oneshot called once with the session + text.
    assert len(rec.calls) == 1, "run_oneshot must run on the legacy path"
    _session, text = rec.calls[0]
    assert text == "hello world"
    assert fake_session_cls.called, "AgentSession must be constructed on the legacy path"

    # Tool path: DelegateSubagentTool must NOT have been instantiated.
    assert stub.instances == [], "DelegateSubagentTool must not run on the legacy path"
    assert stub.calls == []


def test_delegate_unknown_provider_keeps_legacy_error(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """The legacy path's provider-validation error must remain identical."""
    import lilith_cli.main as main_mod
    from lilith_cli.config import YggdrasilConfig

    cfg = YggdrasilConfig(provider="kimi", model="kimi-for-coding")
    cfg.providers = {}  # none configured

    stub = _StubDelegateTool()
    rec = _RecordingOneshot()
    _install_stub(monkeypatch, stub)
    _install_oneshot(monkeypatch, rec)

    with patch.object(main_mod, "load_config", return_value=cfg):
        with pytest.raises(SystemExit) as exc:
            main_mod.delegate(target="nonexistent", text="hi")
    assert exc.value.code == 2

    out = capsys.readouterr().out
    assert "not in config" in out
    assert stub.instances == [] and rec.calls == []


# ── (b) Routing — new flag → tool path ─────────────────────────────────


def test_delegate_routes_to_tool_when_preset_set(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """With --preset, the subcommand must invoke DelegateSubagentTool with the preset."""
    import lilith_cli.main as main_mod

    stub = _StubDelegateTool()  # success + content
    rec = _RecordingOneshot()   # must NOT be called
    _install_stub(monkeypatch, stub)
    _install_oneshot(monkeypatch, rec)

    main_mod.delegate(
        target="ignored-when-preset",
        text="summarise X",
        preset="ejecutor-kimi",
    )

    assert len(stub.calls) == 1
    call = stub.calls[0]
    assert call["preset"] == "ejecutor-kimi"
    assert call["prompt"] == "summarise X"
    assert call["agentic"] is False
    assert call["structured"] is False
    assert "max_tokens" not in call
    assert "max_turns" not in call
    assert rec.calls == [], "run_oneshot must NOT run on the tool path"

    out = capsys.readouterr().out
    assert "stubbed one-shot content" in out
    # Footer: usage should be present.
    assert "usage(prompt=12, completion=7)" in out


def test_delegate_routes_to_tool_when_agentic_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Even without --preset, --agentic flips the subcommand to the tool path."""
    import lilith_cli.main as main_mod

    stub = _StubDelegateTool(
        script=_StubToolResult(
            success=True,
            data={
                "preset": "ejecutor-kimi",
                "provider": "kimi",
                "model": "kimi-for-coding",
                "content": "loop ran",
                "files_written": ["subagent_work/ejecutor-kimi-1/out.py"],
                "turns_used": 3,
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
                "partial": False,
            },
        )
    )
    rec = _RecordingOneshot()
    _install_stub(monkeypatch, stub)
    _install_oneshot(monkeypatch, rec)

    main_mod.delegate(
        target="ejecutor-kimi",  # used as preset name when --preset is absent
        text="do thing",
        agentic=True,
        max_turns=4,
    )

    assert len(stub.calls) == 1
    call = stub.calls[0]
    assert call["preset"] == "ejecutor-kimi"
    assert call["prompt"] == "do thing"
    assert call["agentic"] is True
    assert call["structured"] is False
    assert call["max_turns"] == 4


def test_delegate_routes_when_structured(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """--structured alone is enough to switch to the tool path and pretty-print JSON."""
    import lilith_cli.main as main_mod

    stub = _StubDelegateTool(
        script=_StubToolResult(
            success=True,
            data={
                "preset": "investigador-minimax",
                "provider": "m2",
                "model": "MiniMax-M3",
                "content": "summary text",
                "structured": {"summary": "structured!", "deliverables": []},
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )
    )
    rec = _RecordingOneshot()
    _install_stub(monkeypatch, stub)
    _install_oneshot(monkeypatch, rec)

    main_mod.delegate(
        target="investigador-minimax",
        text="investigate",
        structured=True,
        max_tokens=2048,
    )

    assert len(stub.calls) == 1
    call = stub.calls[0]
    assert call["structured"] is True
    assert call["max_tokens"] == 2048

    out = capsys.readouterr().out
    # Structured payload is rendered as pretty JSON.
    assert '"structured!"' in out
    assert "summary" in out


def test_delegate_tool_failure_exits_nonzero(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """When the tool returns success=False, the CLI exits with code 1 and prints the error."""
    import lilith_cli.main as main_mod

    stub = _StubDelegateTool(
        script=_StubToolResult(
            success=False,
            data={"preset": "ejecutor-kimi", "content": ""},
            error="preset no encontrado",
        )
    )
    rec = _RecordingOneshot()
    _install_stub(monkeypatch, stub)
    _install_oneshot(monkeypatch, rec)

    with pytest.raises(SystemExit) as exc:
        main_mod.delegate(target="ejecutor-kimi", text="x", preset="ejecutor-kimi")
    assert exc.value.code == 1

    out = capsys.readouterr().out
    assert "preset no encontrado" in out
    assert "delegate (ejecutor-kimi)" in out


def test_delegate_model_warning_on_tool_path(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """When --model is passed on the tool path, the CLI prints a warning instead of failing."""
    import lilith_cli.main as main_mod

    stub = _StubDelegateTool()
    rec = _RecordingOneshot()
    _install_stub(monkeypatch, stub)
    _install_oneshot(monkeypatch, rec)

    main_mod.delegate(
        target="ejecutor-kimi", text="x", preset="ejecutor-kimi", model="ignored-model",
    )

    assert len(stub.calls) == 1
    assert stub.calls[0].get("model") is None  # never forwarded to the tool
    out = capsys.readouterr().out
    assert "--model se ignora" in out


def test_delegate_tool_render_files_written_footer(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """The renderer surfaces files_written + turns_used + usage in a single footer line."""
    import lilith_cli.main as main_mod

    files = [f"subagent_work/ejecutor-kimi-1/f{i}.txt" for i in range(7)]
    stub = _StubDelegateTool(
        script=_StubToolResult(
            success=True,
            data={
                "preset": "ejecutor-kimi",
                "content": "ok",
                "files_written": files,
                "turns_used": 6,
                "usage": {"prompt_tokens": 200, "completion_tokens": 80},
            },
        )
    )
    _install_stub(monkeypatch, stub)
    _install_oneshot(monkeypatch, _RecordingOneshot())

    main_mod.delegate(target="ejecutor-kimi", text="x", preset="ejecutor-kimi")

    out = capsys.readouterr().out
    assert "files_written=7" in out
    # Cap the inline list at 5 + "+2 mas".
    assert "+2 mas" in out
    assert "turns_used=6" in out
    assert "usage(prompt=200, completion=80)" in out


def test_delegate_empty_target_and_preset_rejected(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """If target AND preset are both empty/whitespace, the tool path errors out cleanly."""
    import lilith_cli.main as main_mod

    stub = _StubDelegateTool()
    _install_stub(monkeypatch, stub)
    _install_oneshot(monkeypatch, _RecordingOneshot())

    with pytest.raises(SystemExit) as exc:
        main_mod.delegate(target="", text="x", preset="   ", agentic=True)
    assert exc.value.code == 2
    assert stub.calls == []


# ── (c) ITEM 3 — orchestration state records the delegation ────────────


def test_delegate_tool_path_records_in_orchestration_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    """When routed through the tool, the delegation lands in orchestration state automatically.

    The CLI does not call ``OrchestrationStateStore`` directly — the
    *tool* does that in its own ``execute()`` prelude/ending (tanda 3).
    ITEM 3's job is to verify that this happens even when the call comes
    from the CLI subcommand, not just the REPL.

    Strategy: redirect the state JSON via ``YGGDRASIL_ORCHESTRATION_STATE``,
    then inject stub ``lilith_cli.*`` modules into ``sys.modules`` (same
    idiom as ``lilith-tools/tests/test_delegate_subagent.py``) so the
    tool runs end-to-end (state prelude -> provider call -> state update)
    without touching the network or the real CLI config.
    """
    import types

    import lilith_cli.main as main_mod
    import lilith_tools.delegate as delegate_mod

    state_file = tmp_path / "orchestration.json"
    monkeypatch.setenv("YGGDRASIL_ORCHESTRATION_STATE", str(state_file))
    assert not state_file.exists()

    # Stub lilith_cli.config / .main / .providers (idiom from
    # lilith-tools/tests/test_delegate_subagent.py).
    from lilith_cli.config import ProviderProfile, YggdrasilConfig

    cfg = YggdrasilConfig(provider="kimi", model="kimi-for-coding")
    cfg.providers = {
        "kimi": ProviderProfile(
            provider="kimi", model="kimi-for-coding",
            api_key="k", max_tokens=4096,
        ),
    }
    presets = {
        "ejecutor-kimi": {
            "provider": "kimi",
            "model": "kimi-for-coding",
            "temperature": 0.3,
            "max_tokens": 4096,
            "system_prompt": "stub",
        }
    }

    class _StubProvider:
        def __init__(self, _cfg):
            pass

        async def complete(self, *_a, **_kw):
            return {
                "content": "from stub",
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }

        async def close(self):
            return None

    cfg_mod = types.ModuleType("lilith_cli.config")
    cfg_mod.load_config = lambda: cfg  # type: ignore[attr-defined]
    main_stub = types.ModuleType("lilith_cli.main")
    main_stub._load_subagent_presets = lambda config_path=None: presets  # type: ignore[attr-defined]
    providers_mod = types.ModuleType("lilith_cli.providers")
    providers_mod.LLMProviderWrapper = lambda _cfg: _StubProvider(_cfg)  # type: ignore[attr-defined]
    providers_mod.ToolCall = type("ToolCall", (), {})  # type: ignore[attr-defined]
    providers_mod.ToolResult = type("ToolResult", (), {})  # type: ignore[attr-defined]

    for mod in (cfg_mod, main_stub, providers_mod):
        monkeypatch.setitem(sys.modules, mod.__name__, mod)

    # Run the CLI subcommand on the tool path.
    main_mod.delegate(target="ejecutor-kimi", text="hi", preset="ejecutor-kimi")

    # ITEM 3 contract: the delegation is automatically recorded.
    assert state_file.exists(), "orchestration state file must be created"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    tasks = state.get("tasks", [])
    assert tasks, "no tasks recorded in orchestration state"
    matching = [t for t in tasks if t.get("preset") == "ejecutor-kimi"]
    assert matching, f"no task for preset ejecutor-kimi, got: {tasks}"
    completed = [t for t in matching if t["status"] == "completada"]
    assert completed, (
        f"delegation must land as completada, got: "
        f"{[t['status'] for t in matching]}"
    )
