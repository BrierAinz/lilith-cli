"""Tests for lilith_tools.cli_delegate (Vor / Huginn delegate tools).

DROP INTO: <repo>/lilith-tools/tests/test_cli_delegate.py
Pure monkeypatch against ``subprocess.run`` and ``Path.exists`` — no real
PowerShell wrapper, Codex CLI, Ollama or GPU is touched.
Run:  env -u FORCE_COLOR -u COLORTERM TERM=dumb <asgard-venv-python> -m pytest lilith-tools/tests/test_cli_delegate.py -q
"""

from __future__ import annotations

import subprocess

import pytest

import lilith_tools.cli_delegate as cd
from lilith_tools.cli_delegate import (
    OUTPUT_CHAR_LIMIT,
    HuginnDelegateTool,
    VorDelegateTool,
    _truncate,
)
from lilith_tools.registry import ToolRegistry


class FakeProc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


@pytest.fixture(autouse=True)
def _wrappers_exist(monkeypatch):
    monkeypatch.setattr(cd.Path, "exists", lambda self: True)


def _patch_run(monkeypatch, *, proc=None, exc=None, recorder=None):
    def fake_run(argv, **kwargs):
        if recorder is not None:
            recorder.append((argv, kwargs))
        if exc is not None:
            raise exc
        return proc if proc is not None else FakeProc()

    monkeypatch.setattr(subprocess, "run", fake_run)


def test_tools_registered():
    assert ToolRegistry.get("vor_delegate") is VorDelegateTool
    assert ToolRegistry.get("huginn_delegate") is HuginnDelegateTool


def test_vor_success_builds_argv(monkeypatch):
    rec = []
    _patch_run(monkeypatch, proc=FakeProc(stdout="Vor finished (exit 0)\nDONE", returncode=0), recorder=rec)
    res = VorDelegateTool().execute(task="do a thing", cd="D:\\Proyectos\\x", safe=True)
    assert res.success is True
    assert res.data["status"] == "ok"
    assert "DONE" in res.data["output"]
    argv = rec[0][0]
    assert argv[:3] == ["powershell", "-NoProfile", "-ExecutionPolicy"]
    assert "-File" in argv and str(cd.VOR_WRAPPER) in argv
    assert argv[argv.index("-Task") + 1] == "do a thing"
    assert argv[argv.index("-Cd") + 1] == "D:\\Proyectos\\x"
    assert "-Safe" in argv


def test_huginn_success_with_model_and_files(monkeypatch):
    rec = []
    _patch_run(monkeypatch, proc=FakeProc(stdout="Huginn finished (exit 0)", returncode=0), recorder=rec)
    res = HuginnDelegateTool().execute(task="edit", model="uncensored", files=["a.py", "b.py"])
    assert res.success is True
    argv = rec[0][0]
    assert argv[argv.index("-Model") + 1] == "uncensored"
    assert argv.count("-File") == 3  # 1 for the .ps1 + 2 for aider files
    assert "a.py" in argv and "b.py" in argv


def test_huginn_default_model_is_coder(monkeypatch):
    rec = []
    _patch_run(monkeypatch, proc=FakeProc(returncode=0), recorder=rec)
    HuginnDelegateTool().execute(task="x")
    argv = rec[0][0]
    assert argv[argv.index("-Model") + 1] == "coder"


@pytest.mark.parametrize("tool", [VorDelegateTool, HuginnDelegateTool])
def test_empty_task_rejected(tool, monkeypatch):
    _patch_run(monkeypatch, proc=FakeProc())
    res = tool().execute(task="   ")
    assert res.success is False
    assert "task" in res.error


def test_huginn_bad_model_rejected(monkeypatch):
    _patch_run(monkeypatch, proc=FakeProc())
    res = HuginnDelegateTool().execute(task="x", model="gpt-9")
    assert res.success is False
    assert "gpt-9" in res.error


def test_nonzero_exit_is_failure(monkeypatch):
    _patch_run(monkeypatch, proc=FakeProc(stdout="boom", returncode=3))
    res = VorDelegateTool().execute(task="x")
    assert res.success is False
    assert res.data["status"] == "failed"
    assert res.data["returncode"] == 3


def test_needs_prime_detected(monkeypatch):
    _patch_run(monkeypatch, proc=FakeProc(stderr="runas: no se guardo ninguna credencial", returncode=1))
    res = HuginnDelegateTool().execute(task="x")
    assert res.success is False
    assert res.data["status"] == "needs_prime"
    assert "-Prime" in res.error


def test_timeout(monkeypatch):
    _patch_run(monkeypatch, exc=subprocess.TimeoutExpired(cmd="powershell", timeout=5))
    res = VorDelegateTool().execute(task="x", timeout=5)
    assert res.success is False
    assert res.data["status"] == "timeout"


def test_missing_wrapper(monkeypatch):
    monkeypatch.setattr(cd.Path, "exists", lambda self: False)
    res = VorDelegateTool().execute(task="x")
    assert res.success is False
    assert "not found" in res.error


def test_powershell_missing(monkeypatch):
    _patch_run(monkeypatch, exc=FileNotFoundError("powershell"))
    res = HuginnDelegateTool().execute(task="x")
    assert res.success is False


def test_truncate_caps_output():
    big = "z" * (OUTPUT_CHAR_LIMIT + 500)
    out = _truncate(big)
    assert len(out) < len(big)
    assert "truncated 500 chars" in out


def test_execute_truncates_stdout(monkeypatch):
    _patch_run(monkeypatch, proc=FakeProc(stdout="y" * (OUTPUT_CHAR_LIMIT + 100), returncode=0))
    res = VorDelegateTool().execute(task="x")
    assert "truncated" in res.data["output"]
