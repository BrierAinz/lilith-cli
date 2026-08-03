"""Tests for the delegate_to_hermes tool (Lilith → Hermes CLI bridge).

Everything is hermetic: the Hermes executable is faked via the HERMES_BIN
env var, ``subprocess.run`` is mocked so no real process is spawned, and
the orchestration state is redirected to a tmp file via
``YGGDRASIL_ORCHESTRATION_STATE`` so the real ~/.yggdrasil state is never
touched.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from lilith_tools.hermes_delegate import DelegateToHermesTool, _find_hermes


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect orchestration state to a tmp file and fake the Hermes bin."""
    state = tmp_path / "orchestration_state.json"
    monkeypatch.setenv("YGGDRASIL_ORCHESTRATION_STATE", str(state))
    monkeypatch.setenv("HERMES_BIN", "hermes-fake")
    return state


def _completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["hermes-fake"], returncode=returncode, stdout=stdout, stderr=stderr
    )


# ── Validation ─────────────────────────────────────────────────────────

def test_empty_prompt_is_rejected() -> None:
    result = DelegateToHermesTool().execute(prompt="   ")
    assert result.success is False
    assert "prompt" in result.error.lower()


def test_missing_hermes_binary_is_clean_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HERMES_BIN", raising=False)
    with patch("lilith_tools.hermes_delegate.shutil.which", return_value=None):
        result = DelegateToHermesTool().execute(prompt="hacé algo")
    assert result.success is False
    assert "hermes" in result.error.lower()


def test_hermes_bin_env_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_BIN", "/custom/hermes")
    assert _find_hermes() == "/custom/hermes"


# ── Happy path ─────────────────────────────────────────────────────────

def test_successful_delegation_returns_stdout() -> None:
    with patch(
        "lilith_tools.hermes_delegate.subprocess.run",
        return_value=_completed(0, stdout="listo, hecho\n"),
    ) as run:
        result = DelegateToHermesTool().execute(prompt="escribí un haiku")

    assert result.success is True
    assert result.data["content"] == "listo, hecho"
    # The command must be the one-shot form with --yolo by default.
    cmd = run.call_args.args[0]
    assert cmd[0] == "hermes-fake"
    assert cmd[1] == "-z"
    assert cmd[2] == "escribí un haiku"
    assert "--yolo" in cmd


def test_model_and_provider_are_forwarded() -> None:
    with patch(
        "lilith_tools.hermes_delegate.subprocess.run",
        return_value=_completed(0, stdout="ok"),
    ) as run:
        DelegateToHermesTool().execute(
            prompt="tarea", model="claude-sonnet-4.6", provider="anthropic"
        )
    cmd = run.call_args.args[0]
    assert "-m" in cmd and cmd[cmd.index("-m") + 1] == "claude-sonnet-4.6"
    assert "--provider" in cmd and cmd[cmd.index("--provider") + 1] == "anthropic"


def test_yolo_false_omits_flag() -> None:
    with patch(
        "lilith_tools.hermes_delegate.subprocess.run",
        return_value=_completed(0, stdout="ok"),
    ) as run:
        DelegateToHermesTool().execute(prompt="tarea", yolo=False)
    assert "--yolo" not in run.call_args.args[0]


def test_custom_timeout_is_passed_to_subprocess() -> None:
    with patch(
        "lilith_tools.hermes_delegate.subprocess.run",
        return_value=_completed(0, stdout="ok"),
    ) as run:
        DelegateToHermesTool().execute(prompt="tarea", timeout=42)
    assert run.call_args.kwargs["timeout"] == 42


# ── Failure modes ──────────────────────────────────────────────────────

def test_nonzero_exit_is_failure_with_stderr() -> None:
    with patch(
        "lilith_tools.hermes_delegate.subprocess.run",
        return_value=_completed(2, stdout="", stderr="boom"),
    ):
        result = DelegateToHermesTool().execute(prompt="tarea")
    assert result.success is False
    assert "2" in result.error
    assert "boom" in result.error


def test_timeout_is_reported_cleanly() -> None:
    with patch(
        "lilith_tools.hermes_delegate.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="hermes-fake", timeout=600),
    ):
        result = DelegateToHermesTool().execute(prompt="tarea", timeout=600)
    assert result.success is False
    assert "timeout" in result.error.lower()


# ── Orchestration-state integration ────────────────────────────────────

def test_delegation_is_recorded_in_state(_isolate_state: Path) -> None:
    with patch(
        "lilith_tools.hermes_delegate.subprocess.run",
        return_value=_completed(0, stdout="terminado"),
    ):
        result = DelegateToHermesTool().execute(prompt="implementá X")
    assert result.success is True

    state = json.loads(_isolate_state.read_text(encoding="utf-8"))
    hermes_tasks = [t for t in state["tasks"] if t.get("preset") == "hermes"]
    assert len(hermes_tasks) == 1
    task = hermes_tasks[0]
    assert task["status"] == "completada"
    assert "terminado" in (task["result"] or "")


def test_failed_delegation_marks_task_fallida(_isolate_state: Path) -> None:
    with patch(
        "lilith_tools.hermes_delegate.subprocess.run",
        return_value=_completed(1, stdout="", stderr="explotó"),
    ):
        DelegateToHermesTool().execute(prompt="algo que falla")

    state = json.loads(_isolate_state.read_text(encoding="utf-8"))
    hermes_tasks = [t for t in state["tasks"] if t.get("preset") == "hermes"]
    assert len(hermes_tasks) == 1
    assert hermes_tasks[0]["status"] == "fallida"


# ── Registry wiring ────────────────────────────────────────────────────

def test_tool_is_registered() -> None:
    from lilith_tools.registry import ToolRegistry

    # The shared registry is global mutable state: another test in the
    # full suite may have called ToolRegistry.clear(), and our module's
    # import-time @register won't re-run. Re-register defensively — this
    # also asserts the decorator accepts the class idempotently.
    ToolRegistry.register(DelegateToHermesTool)
    # list_tools() returns a {name: description} dict.
    assert "delegate_to_hermes" in ToolRegistry.list_tools()
    assert ToolRegistry.get("delegate_to_hermes") is DelegateToHermesTool
