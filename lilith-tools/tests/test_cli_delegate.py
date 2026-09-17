"""Tests for lilith_tools.cli_delegate (Vor / Huginn delegate tools).

DROP INTO: <repo>/lilith-tools/tests/test_cli_delegate.py
Pure monkeypatch against ``subprocess.run`` and ``Path.exists`` — no real
PowerShell wrapper, Codex CLI, Ollama or GPU is touched.
Run:  env -u FORCE_COLOR -u COLORTERM TERM=dumb <asgard-venv-python> -m pytest lilith-tools/tests/test_cli_delegate.py -q
"""

from __future__ import annotations

import subprocess

import lilith_tools.cli_delegate as cd
import pytest
from lilith_tools.cli_delegate import (
    OUTPUT_CHAR_LIMIT,
    HuginnDelegateTool,
    MuninnDelegateTool,
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
def _wrappers_exist(monkeypatch, tmp_path):
    monkeypatch.setattr(cd, "VOR_WRAPPER", tmp_path / "vor.ps1")
    monkeypatch.setattr(cd, "VOR_HOME2", tmp_path / "codex-home2")
    monkeypatch.setattr(cd, "HUGINN_WRAPPER", tmp_path / "huginn.ps1")
    monkeypatch.setattr(cd, "MUNINN_WRAPPER", tmp_path / "muninn.ps1")
    original = cd.Path.exists
    monkeypatch.setattr(
        cd.Path,
        "exists",
        lambda self: (
            True
            if self in (cd.VOR_WRAPPER, cd.HUGINN_WRAPPER, cd.MUNINN_WRAPPER)
            else original(self)
        ),
    )


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
    assert ToolRegistry.get("muninn_delegate") is MuninnDelegateTool


def test_vor_success_builds_argv(monkeypatch):
    rec = []
    _patch_run(
        monkeypatch,
        proc=FakeProc(stdout="Vor finished (exit 0)\nDONE", returncode=0),
        recorder=rec,
    )
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


@pytest.mark.parametrize("profile_val", [2, "2", "home2"])
def test_vor_profile_home2_adds_codex_home(monkeypatch, profile_val):
    rec = []
    _patch_run(
        monkeypatch,
        proc=FakeProc(stdout="Vor finished (exit 0)", returncode=0),
        recorder=rec,
    )
    res = VorDelegateTool().execute(task="do something", profile=profile_val)
    assert res.success is True
    argv = rec[0][0]
    assert "-CodexHome" in argv
    assert argv[argv.index("-CodexHome") + 1] == str(cd.VOR_HOME2)


@pytest.mark.parametrize("profile_val", [None, 1, "1", "home"])
def test_vor_profile_default_omits_codex_home(monkeypatch, profile_val):
    rec = []
    _patch_run(
        monkeypatch,
        proc=FakeProc(stdout="Vor finished (exit 0)", returncode=0),
        recorder=rec,
    )
    kwargs = {"task": "do something"}
    if profile_val is not None:
        kwargs["profile"] = profile_val
    res = VorDelegateTool().execute(**kwargs)
    assert res.success is True
    argv = rec[0][0]
    assert "-CodexHome" not in argv


@pytest.mark.parametrize("bad_profile", [3, "3", "home3", "invalid", True, False, ""])
def test_vor_invalid_profile_rejected(monkeypatch, bad_profile):
    rec = []
    _patch_run(monkeypatch, recorder=rec)
    res = VorDelegateTool().execute(task="do something", profile=bad_profile)
    assert res.success is False
    assert res.data["agent"] == "Vor"
    assert "profile invalido" in res.error
    for accepted in ("1", "home", "2", "home2"):
        assert accepted in res.error
    assert rec == []



def test_huginn_success_with_model_and_files(monkeypatch):
    rec = []
    _patch_run(
        monkeypatch,
        proc=FakeProc(stdout="Huginn finished (exit 0)", returncode=0),
        recorder=rec,
    )
    res = HuginnDelegateTool().execute(
        task="edit", model="uncensored", files=["a.py", "b.py"]
    )
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


def test_muninn_success_builds_argv(monkeypatch):
    rec = []
    _patch_run(
        monkeypatch,
        proc=FakeProc(stdout="--- Muninn finished (exit 0) ---\nALL GOOD", returncode=0),
        recorder=rec,
    )
    res = MuninnDelegateTool().execute(
        task="configure system acl",
        cd="D:\\Proyectos\\ops",
        model="claude-3-7-sonnet",
        timeout=1200,
    )
    assert res.success is True
    assert res.data["status"] == "ok"
    assert "ALL GOOD" in res.data["output"]
    argv = rec[0][0]
    assert argv[:3] == ["powershell", "-NoProfile", "-ExecutionPolicy"]
    assert "-File" in argv and str(cd.MUNINN_WRAPPER) in argv
    assert argv[argv.index("-Task") + 1] == "configure system acl"
    assert argv[argv.index("-Cd") + 1] == "D:\\Proyectos\\ops"
    assert argv[argv.index("-Model") + 1] == "claude-3-7-sonnet"
    assert argv[argv.index("-TimeoutSec") + 1] == "1200"
    assert "-AllowOutsideD" not in argv


def test_muninn_no_outside_d_flag_exposed(monkeypatch):
    rec = []
    _patch_run(
        monkeypatch,
        proc=FakeProc(stdout="--- Muninn finished (exit 0) ---", returncode=0),
        recorder=rec,
    )
    params = MuninnDelegateTool.parameters
    assert "allow_outside_d" not in params
    assert "allow_outside" not in params
    assert not any("outside" in k.lower() for k in params)

    res = MuninnDelegateTool().execute(task="check credentials", allow_outside_d=True)
    assert res.success is True
    argv = rec[0][0]
    assert "-AllowOutsideD" not in argv


def test_muninn_description_specifies_configuration_and_no_elevation():
    desc = MuninnDelegateTool.description.lower()
    assert "lilith_muninn_wrapper" in desc
    assert "no eleva privilegios" in desc
    assert "permisos del proceso" in desc


def test_muninn_repeated_request_does_not_repeat_worker(monkeypatch):
    rec = []
    _patch_run(
        monkeypatch,
        recorder=rec,
        proc=FakeProc(stdout="--- Muninn finished (exit 0) ---"),
    )
    first = MuninnDelegateTool().execute(task="privileged task", request_id="c" * 32)
    second = MuninnDelegateTool().execute(task="privileged task", request_id="c" * 32)
    assert first.success
    assert not second.success
    assert second.data["status"] == "existing_attempt"
    assert second.data["reference"] == first.data["reference"]
    assert not second.data["execution_performed"]
    assert len(rec) == 1


@pytest.mark.parametrize("tool", [VorDelegateTool, HuginnDelegateTool, MuninnDelegateTool])
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
    _patch_run(
        monkeypatch,
        proc=FakeProc(stderr="runas: no se guardo ninguna credencial", returncode=1),
    )
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
    _patch_run(
        monkeypatch, proc=FakeProc(stdout="y" * (OUTPUT_CHAR_LIMIT + 100), returncode=0)
    )
    res = VorDelegateTool().execute(task="x")
    assert "truncated" in res.data["output"]


@pytest.mark.parametrize(
    "tool,agent",
    [
        (VorDelegateTool, "Vor"),
        (HuginnDelegateTool, "Huginn"),
        (MuninnDelegateTool, "Muninn"),
    ],
)
def test_job_failure_overrides_wrapper_success(monkeypatch, tool, agent):
    _patch_run(
        monkeypatch, proc=FakeProc(stdout=f"--- {agent} finished (exit 1) ---\n")
    )
    result = tool().execute(task="bounded review")
    assert not result.success
    assert result.data["status"] == "failed"
    assert result.data["job_returncode"] == 1


@pytest.mark.parametrize(
    "output",
    [
        "",
        "started",
        "--- Huginn finished (exit 0) ---",
        "--- Vor finished (exit invalid) ---",
        "--- Vor finished (exit 0) ---\n--- Vor finished (exit 1) ---",
    ],
)
def test_missing_or_ambiguous_completion_is_not_success(monkeypatch, output):
    _patch_run(monkeypatch, proc=FakeProc(stdout=output))
    result = VorDelegateTool().execute(task="review")
    assert not result.success
    assert result.data["status"] == "unconfirmed"
    assert not result.data["completion_confirmed"]


def test_completion_after_display_limit_is_checked(monkeypatch):
    _patch_run(
        monkeypatch,
        proc=FakeProc(
            stdout="x" * (OUTPUT_CHAR_LIMIT + 100) + "\n--- Vor finished (exit 0) ---\n"
        ),
    )
    result = VorDelegateTool().execute(task="review")
    assert result.success
    assert result.data["job_returncode"] == 0
    assert "truncated" in result.data["output"]


def test_timeout_warns_about_unknown_effects(monkeypatch):
    _patch_run(monkeypatch, exc=subprocess.TimeoutExpired(cmd="powershell", timeout=5))
    result = VorDelegateTool().execute(task="review", timeout=5)
    assert result.data["effects_unknown"]
    assert not result.data["completion_confirmed"]
    assert "before retrying" in result.error


def test_powershell_requests_hidden_window_on_windows(monkeypatch):
    rec = []
    _patch_run(monkeypatch, recorder=rec)
    VorDelegateTool().execute(task="review")
    assert rec[0][1]["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)


@pytest.mark.parametrize(
    "tool", [VorDelegateTool, HuginnDelegateTool, MuninnDelegateTool]
)
@pytest.mark.parametrize("timeout", [0, -1, True, False, 1.5, "5", "invalid", 86401])
def test_invalid_timeout_never_starts_wrapper(monkeypatch, tool, timeout):
    rec = []
    _patch_run(monkeypatch, recorder=rec)
    result = tool().execute(task="review", timeout=timeout)
    assert not result.success
    assert "timeout" in result.error
    assert rec == []


@pytest.mark.parametrize(
    "tool", [VorDelegateTool, HuginnDelegateTool, MuninnDelegateTool]
)
@pytest.mark.parametrize("timeout", [None, 1, 86400])
def test_wrapper_receives_observation_timeout(monkeypatch, tool, timeout):
    rec = []
    _patch_run(monkeypatch, recorder=rec)
    tool().execute(task="review", timeout=timeout)
    expected = cd.DEFAULT_TIMEOUT if timeout is None else timeout
    argv, options = rec[0]
    assert argv[argv.index("-TimeoutSec") + 1] == str(expected)
    assert options["timeout"] == expected


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_task_credential_text_is_not_launcher_diagnosis(monkeypatch, stream):
    _patch_run(
        monkeypatch,
        proc=FakeProc(**{stream: "failed to rotate credential 1219", "returncode": 3}),
    )
    result = VorDelegateTool().execute(task="review")
    assert result.data["status"] == "failed"
    assert "-Prime" not in result.error


def test_launcher_diagnosis_uses_untruncated_stderr(monkeypatch):
    _patch_run(
        monkeypatch,
        proc=FakeProc(
            stderr="x" * (OUTPUT_CHAR_LIMIT + 100)
            + "\nrunas could not use the saved credential for synthetic-worker",
            returncode=1,
        ),
    )
    result = VorDelegateTool().execute(task="review")
    assert result.data["status"] == "needs_prime"


@pytest.mark.parametrize("as_bytes", [False, True])
def test_timeout_preserves_partial_output_and_vor_job_hint(monkeypatch, as_bytes):
    output = "=== Vor job 20260912-025440-1234  (cd=D:\\project)  ===\nworking"
    stderr = "partial diagnostic"
    _patch_run(
        monkeypatch,
        exc=subprocess.TimeoutExpired(
            cmd="powershell",
            timeout=5,
            output=output.encode() if as_bytes else output,
            stderr=stderr.encode() if as_bytes else stderr,
        ),
    )
    result = VorDelegateTool().execute(task="review", timeout=5)
    assert result.data["job_id"] == "20260912-025440-1234"
    assert result.data["job_id_source"] == "launcher_stdout_unverified"
    assert result.data["output"] == output
    assert result.data["stderr"] == stderr
    assert not result.data["retry_safe"]
    assert not result.data["completion_confirmed"]


@pytest.mark.parametrize(
    "output",
    [
        "=== Vor job ../../secret  (cd=D:\\project)  ===",
        "quoted === Vor job 20260912-025440-1234  (cd=D:\\project)  ===",
        "=== Vor job 20260912-025440-1234  (cd=D:\\project)  ===\n" * 2,
    ],
)
def test_unusable_job_hints_are_not_recovery_identity(monkeypatch, output):
    _patch_run(monkeypatch, proc=FakeProc(stdout=output))
    result = VorDelegateTool().execute(task="review")
    assert result.data["job_id"] is None


def test_huginn_does_not_claim_vor_job_id(monkeypatch):
    _patch_run(
        monkeypatch,
        proc=FakeProc(stdout="=== Vor job 20260912-025440-1234  (cd=D:\\project)  ==="),
    )
    result = HuginnDelegateTool().execute(task="review")
    assert result.data["job_id"] is None


def test_timeout_output_is_bounded_and_invalid_utf8_is_tolerated(monkeypatch):
    _patch_run(
        monkeypatch,
        exc=subprocess.TimeoutExpired(
            cmd="powershell",
            timeout=5,
            output=b"\xff" + b"x" * (OUTPUT_CHAR_LIMIT + 10),
        ),
    )
    result = VorDelegateTool().execute(task="review", timeout=5)
    assert result.data["output"].startswith("\ufffd")
    assert "truncated" in result.data["output"]
    assert result.data["job_id"] is None


def test_journal_failure_prevents_dispatch(monkeypatch):
    rec = []
    _patch_run(monkeypatch, recorder=rec)

    def fail(*args, **kwargs):
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(cd.CliJobJournal, "begin", fail)
    result = VorDelegateTool().execute(task="review")
    assert not result.success
    assert result.data["status"] == "journal_unavailable"
    assert rec == []


def test_observation_save_failure_does_not_repeat_worker(monkeypatch):
    rec = []
    _patch_run(
        monkeypatch, recorder=rec, proc=FakeProc(stdout="--- Vor finished (exit 0) ---")
    )

    def fail(*args, **kwargs):
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(cd.CliJobJournal, "finish", fail)
    result = VorDelegateTool().execute(task="review")
    assert not result.success
    assert result.data["reference_saved"]
    assert not result.data["observation_saved"]
    assert len(rec) == 1
    row = cd.CliJobJournal().recent()[0]
    assert row["reference"] == result.data["reference"]
    assert row["observation"] is None


def test_repeated_request_does_not_repeat_worker(monkeypatch):
    rec = []
    _patch_run(
        monkeypatch, recorder=rec, proc=FakeProc(stdout="--- Vor finished (exit 0) ---")
    )
    first = VorDelegateTool().execute(task="review", request_id="a" * 32)
    second = VorDelegateTool().execute(task="review", request_id="a" * 32)
    assert first.success
    assert not second.success
    assert second.data["status"] == "existing_attempt"
    assert second.data["reference"] == first.data["reference"]
    assert not second.data["execution_performed"]
    assert len(rec) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"task": "different"},
        {"cd": "D:\\other"},
        {"safe": True},
        {"timeout": 5},
        {"profile": 2},
    ],
)
def test_request_key_cannot_change_intent(monkeypatch, change):
    rec = []
    _patch_run(monkeypatch, recorder=rec)
    args = {"task": "review", "request_id": "b" * 32}
    VorDelegateTool().execute(**args)
    result = VorDelegateTool().execute(**(args | change))
    assert result.data["status"] == "request_rejected"
    assert len(rec) == 1
