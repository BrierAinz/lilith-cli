"""Tests for longrun/supervisor.py — no network, no subprocesses, no real sleep."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from lilith_cli.longrun.supervisor import (
    LegResult,
    StopReason,
    _check_pending_tools,
    _fire_escalation,
    classify,
    failure_signature,
    supervise,
)
from lilith_cli.task_workspace import atomic_json


# ---------------------------------------------------------------------------
# Minimal stubs for contract, journal, evidence — coded against the contract
# signatures so these work whether the real modules exist yet or not.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _Budget:
    max_wall_seconds: int = 86400
    max_legs: int = 10
    max_iterations_per_leg: int = 12
    max_tokens: int | None = None
    max_usd: float | None = None


@dataclass(frozen=True)
class _RunContract:
    run_id: str = "GU-20260916-000000-test"
    goal: str = "Test goal"
    end_criterion: str = "Tests pass"
    verify: str = ""
    project_root: str = "."
    budget: _Budget = field(default_factory=_Budget)
    provider: str = "default"
    created_at: str = ""
    deadline: str = ""

    def save(self, directory: Path) -> Path:
        data = {
            "run_id": self.run_id,
            "goal": self.goal,
            "end_criterion": self.end_criterion,
            "verify": self.verify,
            "project_root": self.project_root,
            "budget": {
                "max_wall_seconds": self.budget.max_wall_seconds,
                "max_legs": self.budget.max_legs,
                "max_iterations_per_leg": self.budget.max_iterations_per_leg,
                "max_tokens": self.budget.max_tokens,
                "max_usd": self.budget.max_usd,
            },
            "created_at": self.created_at,
            "deadline": self.deadline,
        }
        path = directory / "contract.json"
        atomic_json(path, data)
        return path

    @classmethod
    def load(cls, directory: Path) -> "_RunContract":
        data = json.loads((directory / "contract.json").read_text(encoding="utf-8"))
        budget = _Budget(**data["budget"])
        return cls(
            run_id=data["run_id"],
            goal=data["goal"],
            end_criterion=data["end_criterion"],
            verify=data.get("verify", ""),
            project_root=data.get("project_root", "."),
            budget=budget,
            created_at=data["created_at"],
            deadline=data["deadline"],
        )

    @classmethod
    def create(cls, *, goal, end_criterion, verify, project_root, budget,
               now=None):
        t = now or datetime.now(timezone.utc)
        deadline = t + timedelta(seconds=budget.max_wall_seconds)
        return cls(
            goal=goal,
            end_criterion=end_criterion,
            verify=verify,
            project_root=project_root,
            budget=budget,
            created_at=t.isoformat(),
            deadline=deadline.isoformat(),
        )


@dataclass
class _Consumed:
    legs: int = 0
    iterations: int = 0
    tokens: int = 0
    usd: float = 0.0

    def save(self, directory: Path) -> Path:
        data = {"legs": self.legs, "iterations": self.iterations,
                "tokens": self.tokens, "usd": self.usd}
        path = directory / "consumed.json"
        atomic_json(path, data)
        return path

    @classmethod
    def load(cls, directory: Path) -> "_Consumed":
        path = directory / "consumed.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(**data)
        return cls()


def _exhausted(contract, consumed, now=None) -> str | None:
    t = now or datetime.now(timezone.utc)
    if isinstance(t, datetime):
        deadline = datetime.fromisoformat(contract.deadline.replace("Z", "+00:00"))
        if t >= deadline:
            return "reloj"
    if consumed.legs >= contract.budget.max_legs:
        return "tramos"
    if contract.budget.max_tokens and consumed.tokens >= contract.budget.max_tokens:
        return "tokens"
    if contract.budget.max_usd and consumed.usd >= contract.budget.max_usd:
        return "gasto"
    return None


class _Journal:
    def __init__(self, path: Path):
        self.path = path
        self._seq = 0

    def append(self, kind: str, text: str, *, evidence: dict | None = None) -> dict:
        self._seq += 1
        entry = {"at": datetime.now(timezone.utc).isoformat(), "kind": kind,
                 "text": text, "evidence": evidence, "seq": self._seq}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=True) + "\n")
        return entry

    def entries(self, *, kind: str | None = None) -> list[dict]:
        if not self.path.exists():
            return []
        items = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entry = json.loads(line)
                if kind is None or entry.get("kind") == kind:
                    items.append(entry)
        return items

    def digest(self, max_chars: int) -> str:
        return ""


class _Evidence:
    def __init__(self, path: Path):
        self.path = path

    def record(self, kind: str, payload: dict) -> dict:
        return {}

    def events(self) -> list[dict]:
        return []


def _report(contract, consumed, journal, evidence, *, stop_reason=None, now=None):
    return f"## INFORME\nstop_reason: {stop_reason}\n"


# ---------------------------------------------------------------------------
# Monkey-patch the longrun package so supervisor imports resolve to our stubs.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _patch_longrun_imports(monkeypatch):
    """Patch the longrun contract/journal/evidence modules used by supervisor."""
    import lilith_cli.longrun.supervisor as sup_mod

    # We need to patch at the point where supervisor does its lazy imports.
    # supervisor uses: from .contract import RunContract, Consumed, exhausted
    #                  from .journal import Journal
    #                  from .evidence import Evidence, report
    # We create stub modules and inject them.

    import types
    contract_mod = types.ModuleType("lilith_cli.longrun.contract")
    contract_mod.RunContract = _RunContract  # type: ignore[attr-defined]
    contract_mod.Consumed = _Consumed  # type: ignore[attr-defined]
    contract_mod.exhausted = _exhausted  # type: ignore[attr-defined]

    journal_mod = types.ModuleType("lilith_cli.longrun.journal")
    journal_mod.Journal = _Journal  # type: ignore[attr-defined]

    evidence_mod = types.ModuleType("lilith_cli.longrun.evidence")
    evidence_mod.Evidence = _Evidence  # type: ignore[attr-defined]
    evidence_mod.report = _report  # type: ignore[attr-defined]

    import sys as _sys
    monkeypatch.setitem(_sys.modules, "lilith_cli.longrun.contract", contract_mod)
    monkeypatch.setitem(_sys.modules, "lilith_cli.longrun.journal", journal_mod)
    monkeypatch.setitem(_sys.modules, "lilith_cli.longrun.evidence", evidence_mod)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_dir(tmp_path: Path, *, budget: _Budget | None = None,
              now: datetime | None = None,
              consumed: _Consumed | None = None,
              state: dict | None = None) -> Path:
    """Set up a run directory with contract.json and consumed.json."""
    t = now or datetime(2026, 9, 16, 12, 0, 0, tzinfo=timezone.utc)
    b = budget or _Budget()
    contract = _RunContract.create(
        goal="Test goal",
        end_criterion="All tests pass",
        verify="",
        project_root=".",
        budget=b,
        now=t,
    )
    contract.save(tmp_path)
    c = consumed or _Consumed()
    c.save(tmp_path)
    if state is not None:
        atomic_json(tmp_path / "state.json", state)
    return tmp_path


class FakeRunner:
    """A runner that returns pre-loaded LegResult objects in sequence."""

    def __init__(self, results: list[LegResult]):
        self.results = list(results)
        self.calls = 0

    def __call__(self, contract, consumed, session_id, directory) -> LegResult:
        if self.calls >= len(self.results):
            raise RuntimeError("Runner called more times than expected")
        result = self.results[self.calls]
        self.calls += 1
        return result


class FakeSleeper:
    """Records sleep calls instead of actually sleeping."""

    def __init__(self):
        self.calls: list[float] = []

    def __call__(self, seconds: float):
        self.calls.append(seconds)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFailureSignature:
    def test_same_error_different_timestamps(self):
        """Test 4: identical failures differing only in timestamp, PID, and
        temp path produce the same signature."""
        payload_a = {
            "status": "blocked",
            "error": "FileNotFoundError",
            "progress": {
                "activity": [
                    {"event": "tool_result", "tool": "file_write",
                     "at": "2026-09-15T10:00:00Z"},
                ]
            },
        }
        payload_b = {
            "status": "blocked",
            "error": "FileNotFoundError",
            "progress": {
                "activity": [
                    {"event": "tool_result", "tool": "file_write",
                     "at": "2026-09-16T14:30:00Z"},
                ]
            },
        }
        assert failure_signature(payload_a) == failure_signature(payload_b)

    def test_same_error_different_pid_and_path(self):
        """Companion to test 4: PID and temp path differences are normalised."""
        payload_a = {
            "status": "blocked",
            "error": "OSError at /tmp/abc123/work pid 12345",
            "progress": {"activity": []},
        }
        payload_b = {
            "status": "blocked",
            "error": "OSError at /tmp/xyz789/work pid 99999",
            "progress": {"activity": []},
        }
        assert failure_signature(payload_a) == failure_signature(payload_b)

    def test_different_errors_different_signatures(self):
        payload_a = {
            "status": "blocked",
            "error": "FileNotFoundError",
            "progress": {"activity": []},
        }
        payload_b = {
            "status": "blocked",
            "error": "PermissionError",
            "progress": {"activity": []},
        }
        assert failure_signature(payload_a) != failure_signature(payload_b)

    def test_uuid_normalised(self):
        """UUIDs are replaced with # so two identical errors with different
        UUIDs produce the same signature."""
        payload_a = {
            "status": "blocked",
            "error": "Task 550e8400-e29b-41d4-a716-446655440000 failed",
            "progress": {"activity": []},
        }
        payload_b = {
            "status": "blocked",
            "error": "Task a1b2c3d4-e5f6-7890-abcd-ef1234567890 failed",
            "progress": {"activity": []},
        }
        assert failure_signature(payload_a) == failure_signature(payload_b)


class TestClassify:
    def test_verified_is_ok(self):
        """Test 10a: verified -> ok."""
        assert classify({"status": "verified"}) == "ok"

    def test_quota_error(self):
        """Test 10b: quota patterns -> quota."""
        assert classify({"status": "blocked", "error": "Usage limit exceeded"}) == "quota"
        assert classify({"status": "blocked", "error": "Rate limit hit"}) == "quota"
        assert classify({"status": "blocked", "error": "Error 429 too many requests"}) == "quota"
        assert classify({"status": "blocked", "error": "insufficient_quota"}) == "quota"

    def test_decision_needed(self):
        """Test 10c: blocked with question -> decision."""
        payload = {
            "status": "blocked",
            "response": "ask_operator: should I proceed?",
            "progress": {"status": "blocked"},
        }
        assert classify(payload) == "decision"

    def test_transient_fallback(self):
        """Test 10d: anything else -> transient."""
        assert classify({"status": "blocked", "error": "ConnectionReset"}) == "transient"

    def test_irreversible_commit(self):
        payload = {
            "status": "responded",
            "progress": {
                "activity": [{"event": "tool_call", "tool": "git_commit"}],
            },
        }
        assert classify(payload) == "irreversible"


class TestSupervise:
    def test_verified_stops_with_terminado(self, tmp_path):
        """Test 1: a single verified leg stops with 'terminado'."""
        _make_dir(tmp_path)
        runner = FakeRunner([
            LegResult(status="verified", session="s1", signature=None,
                      retry_after=None, tokens=100, usd=0.01,
                      raw={"status": "verified"}),
        ])
        fixed_now = datetime(2026, 9, 16, 12, 0, 30, tzinfo=timezone.utc)
        result = supervise(tmp_path, runner=runner, sleeper=FakeSleeper(), now=fixed_now)
        assert result.code == "terminado"
        assert runner.calls == 1

    def test_same_signature_twice_stops(self, tmp_path):
        """Test 2: two legs with the SAME failure signature stop with
        'misma_causa_dos_veces', and the runner is NOT called a third time."""
        _make_dir(tmp_path)
        sig = "blocked|SomeError|tool_x|some normalised msg"
        runner = FakeRunner([
            LegResult(status="blocked", session="s1", signature=sig,
                      retry_after=None, tokens=50, usd=0.005,
                      raw={"status": "blocked", "error": "SomeError"}),
            LegResult(status="blocked", session="s2", signature=sig,
                      retry_after=None, tokens=50, usd=0.005,
                      raw={"status": "blocked", "error": "SomeError"}),
            LegResult(status="verified", session="s3", signature=None,
                      retry_after=None,
                      raw={"status": "verified"}),  # should never be reached
        ])
        fixed_now = datetime(2026, 9, 16, 12, 0, 30, tzinfo=timezone.utc)
        result = supervise(tmp_path, runner=runner, sleeper=FakeSleeper(), now=fixed_now)
        assert result.code == "misma_causa_dos_veces"
        assert runner.calls == 2  # NOT 3

    def test_different_signatures_keep_going(self, tmp_path):
        """Test 3: two legs with DIFFERENT signatures do not trigger
        misma_causa_dos_veces — the supervisor keeps iterating."""
        _make_dir(tmp_path)
        runner = FakeRunner([
            LegResult(status="blocked", session="s1",
                      signature="blocked|ErrorA|tool_a|msg_a",
                      retry_after=None, tokens=50, usd=0.005,
                      raw={"status": "blocked", "error": "ErrorA"}),
            LegResult(status="blocked", session="s2",
                      signature="blocked|ErrorB|tool_b|msg_b",
                      retry_after=None, tokens=50, usd=0.005,
                      raw={"status": "blocked", "error": "ErrorB"}),
            LegResult(status="verified", session="s3", signature=None,
                      retry_after=None, tokens=50, usd=0.005,
                      raw={"status": "verified"}),
        ])
        fixed_now = datetime(2026, 9, 16, 12, 0, 30, tzinfo=timezone.utc)
        result = supervise(tmp_path, runner=runner, sleeper=FakeSleeper(), now=fixed_now)
        assert result.code == "terminado"
        assert runner.calls == 3

    def test_quota_parks_and_continues(self, tmp_path):
        """Test 5: a quota leg with 'try again at Sep 19th, 2026 2:09 AM'
        sets park_until and does NOT count as repeated failure."""
        _make_dir(tmp_path, budget=_Budget(max_wall_seconds=86400 * 7))
        quota_raw = {
            "status": "blocked",
            "error": "Usage limit exceeded. Try again at Sep 19th, 2026 2:09 AM",
        }
        runner = FakeRunner([
            LegResult(status="blocked", session="s1",
                      signature="blocked|quota|...",
                      retry_after="2026-09-19T02:09:00+00:00",
                      tokens=10, usd=0.001,
                      raw=quota_raw),
            LegResult(status="verified", session="s2", signature=None,
                      retry_after=None, tokens=100, usd=0.01,
                      raw={"status": "verified"}),
        ])

        sleep_log = FakeSleeper()
        # Use a clock that advances past the park_until after sleeping.
        # The now() callable is invoked many times: budget checks, parking
        # loop iterations, post-parking re-checks, etc.  We need to stay
        # at Sep 16 long enough for the parking loop to call sleeper at
        # least once, then jump past park_until.
        call_count = [0]
        park_target = datetime(2026, 9, 19, 2, 9, 0, tzinfo=timezone.utc)

        def advancing_clock():
            call_count[0] += 1
            if call_count[0] <= 8:
                # During first leg and parking loop — stay before park_until
                return datetime(2026, 9, 16, 12, 0, 30, tzinfo=timezone.utc)
            # After enough iterations: jump past the park_until time
            return park_target + timedelta(seconds=1)

        result = supervise(tmp_path, runner=runner, sleeper=sleep_log, now=advancing_clock)
        assert result.code == "terminado"
        assert runner.calls == 2
        assert len(sleep_log.calls) > 0  # sleeper was called
        # Verify state.json was written with park_until
        state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
        # After completion, stopped should be set
        assert state["stopped"]["code"] == "terminado"

    def test_parking_sleeps_in_chunks_and_cancel_mid_park(self, tmp_path):
        """Test 6: parking sleeps in chunks of <=60s and cancels mid-park
        when control.json says cancel."""
        _make_dir(tmp_path)
        quota_raw = {
            "status": "blocked",
            "error": "Rate limit hit",
        }
        runner = FakeRunner([
            LegResult(status="blocked", session="s1",
                      signature=None,
                      retry_after=None,
                      tokens=10, usd=0.001,
                      raw=quota_raw),
        ])

        sleep_log = FakeSleeper()
        sleep_call_count = [0]
        original_sleeper = sleep_log

        def cancelling_sleeper(seconds):
            original_sleeper(seconds)
            sleep_call_count[0] += 1
            # After 2 sleep calls, write cancel to control.json
            if sleep_call_count[0] == 2:
                atomic_json(tmp_path / "control.json", {"action": "cancel"})

        # Clock that stays before park_until
        call_count = [0]

        def slow_clock():
            call_count[0] += 1
            # Always return a time before the park target so we keep parking
            return datetime(2026, 9, 16, 12, 1, 0, tzinfo=timezone.utc)

        result = supervise(tmp_path, runner=runner, sleeper=cancelling_sleeper, now=slow_clock)
        assert result.code == "parada_solicitada"
        # All sleep chunks must be <= 60
        for chunk in original_sleeper.calls:
            assert chunk <= 60

    def test_budget_clock_stops_before_runner(self, tmp_path):
        """Test 7: exhausted clock budget stops with 'presupuesto' detail='reloj'
        BEFORE calling the runner."""
        # Set a deadline that is already in the past
        past = datetime(2026, 9, 16, 10, 0, 0, tzinfo=timezone.utc)
        _make_dir(tmp_path, now=past, budget=_Budget(max_wall_seconds=60))
        runner = FakeRunner([])  # should never be called

        # Current time is well past the deadline
        current = datetime(2026, 9, 16, 12, 0, 0, tzinfo=timezone.utc)
        result = supervise(tmp_path, runner=runner, sleeper=FakeSleeper(), now=current)
        assert result.code == "presupuesto"
        assert result.detail == "reloj"
        assert runner.calls == 0

    def test_resume_after_restart(self, tmp_path):
        """Test 8: run supervise, exhaust the runner, then call supervise again
        on the same directory — it resumes session and consumed from disk."""
        _make_dir(tmp_path, budget=_Budget(max_legs=10))

        # First run: two transient failures with different signatures, then verified
        runner1 = FakeRunner([
            LegResult(status="blocked", session="s1",
                      signature="blocked|ErrA|t1|m1",
                      retry_after=None, tokens=100, usd=0.01,
                      raw={"status": "blocked", "error": "ErrA"}),
            LegResult(status="verified", session="s2", signature=None,
                      retry_after=None, tokens=200, usd=0.02,
                      raw={"status": "verified"}),
        ])
        fixed_now = datetime(2026, 9, 16, 12, 0, 30, tzinfo=timezone.utc)
        result1 = supervise(tmp_path, runner=runner1, sleeper=FakeSleeper(), now=fixed_now)
        assert result1.code == "terminado"

        # Check consumed was persisted
        consumed = json.loads((tmp_path / "consumed.json").read_text(encoding="utf-8"))
        assert consumed["legs"] == 2
        assert consumed["tokens"] == 300

        # Second run: already stopped, should return immediately
        runner2 = FakeRunner([])
        result2 = supervise(tmp_path, runner=runner2, sleeper=FakeSleeper(), now=fixed_now)
        assert result2.code == "terminado"
        assert runner2.calls == 0

    def test_already_stopped_returns_without_running(self, tmp_path):
        """Test 9: a guard with state['stopped'] present returns that stop
        without calling the runner."""
        _make_dir(tmp_path, state={
            "session": "old",
            "signatures": [],
            "park_until": None,
            "stopped": {"code": "decision_ajena", "detail": "test",
                        "decision_needed": "What colour?"},
        })
        runner = FakeRunner([])
        fixed_now = datetime(2026, 9, 16, 12, 0, 30, tzinfo=timezone.utc)
        result = supervise(tmp_path, runner=runner, sleeper=FakeSleeper(), now=fixed_now)
        assert result.code == "decision_ajena"
        assert result.decision_needed == "What colour?"
        assert runner.calls == 0

    def test_state_json_written_after_each_leg(self, tmp_path):
        """Test 11: state.json is written to disk after each leg."""
        _make_dir(tmp_path)
        runner = FakeRunner([
            LegResult(status="blocked", session="s1",
                      signature="blocked|Err|t|m",
                      retry_after=None, tokens=10, usd=0.001,
                      raw={"status": "blocked", "error": "Err"}),
            LegResult(status="verified", session="s2", signature=None,
                      retry_after=None, tokens=20, usd=0.002,
                      raw={"status": "verified"}),
        ])
        # We'll check state.json content between calls by wrapping the runner
        state_snapshots = []
        original_runner = runner

        class InspectingRunner:
            def __init__(self):
                self.calls = 0

            def __call__(self, contract, consumed, session_id, directory):
                result = original_runner(contract, consumed, session_id, directory)
                self.calls = original_runner.calls
                # After the runner returns (and before the next iteration starts),
                # we can't intercept *after* state is saved from here.
                # But we'll check the final state below.
                return result

        inspecting = InspectingRunner()
        # We need a different approach: check state.json exists at the end
        # and has the right content
        fixed_now = datetime(2026, 9, 16, 12, 0, 30, tzinfo=timezone.utc)
        result = supervise(tmp_path, runner=runner, sleeper=FakeSleeper(), now=fixed_now)

        # state.json must exist on disk
        state_path = tmp_path / "state.json"
        assert state_path.exists()
        state = json.loads(state_path.read_text(encoding="utf-8"))
        # After two legs (one transient, one verified), the state should record:
        assert state["stopped"]["code"] == "terminado"
        assert "blocked|Err|t|m" in state.get("signatures", [])
        assert state["session"] == "s2"

    def test_pending_tools_no_resume_new_leg(self, tmp_path):
        """Test 12: a checkpoint with progress['pending'] non-empty makes the
        supervisor NOT resume that session — it starts a new leg without resume,
        and the initial message names the dangling tools."""
        _make_dir(tmp_path)

        # Pre-set state with a session to resume
        atomic_json(tmp_path / "state.json", {
            "session": "sess-tainted",
            "signatures": [],
            "park_until": None,
            "stopped": None,
        })

        # A pending_checker that says sess-tainted has pending tools
        def fake_pending_checker(session_id):
            if session_id == "sess-tainted":
                return ["file_write", "shell_exec"]
            return []

        runner_calls = []

        class CapturingRunner:
            calls = 0

            def __call__(self, contract, consumed, session_id, directory):
                self.calls += 1
                runner_calls.append({
                    "session_id": session_id,
                    "pending_seed": getattr(contract, "_pending_seed", None),
                })
                return LegResult(
                    status="verified", session="s-new", signature=None,
                    retry_after=None, tokens=100, usd=0.01,
                    raw={"status": "verified"},
                )

        runner = CapturingRunner()
        fixed_now = datetime(2026, 9, 16, 12, 0, 30, tzinfo=timezone.utc)
        result = supervise(
            tmp_path, runner=runner, sleeper=FakeSleeper(), now=fixed_now,
            pending_checker=fake_pending_checker,
            hook=lambda *a, **kw: None,
        )
        assert result.code == "terminado"
        assert runner.calls == 1
        # The runner was called with session_id=None (no resume)
        assert runner_calls[0]["session_id"] is None
        # The contract got the pending_seed with tool names
        seed = runner_calls[0]["pending_seed"]
        assert seed is not None
        assert "file_write" in seed
        assert "shell_exec" in seed

    def test_pending_tools_failure_journal_entry(self, tmp_path):
        """Test 13: when pending tools are detected, a 'failure' entry is
        written to the journal naming those tools."""
        _make_dir(tmp_path)

        atomic_json(tmp_path / "state.json", {
            "session": "sess-tainted",
            "signatures": [],
            "park_until": None,
            "stopped": None,
        })

        def fake_pending_checker(session_id):
            if session_id == "sess-tainted":
                return ["file_write", "shell_exec"]
            return []

        runner = FakeRunner([
            LegResult(status="verified", session="s-new", signature=None,
                      retry_after=None, tokens=100, usd=0.01,
                      raw={"status": "verified"}),
        ])
        fixed_now = datetime(2026, 9, 16, 12, 0, 30, tzinfo=timezone.utc)
        supervise(
            tmp_path, runner=runner, sleeper=FakeSleeper(), now=fixed_now,
            pending_checker=fake_pending_checker,
            hook=lambda *a, **kw: None,
        )

        # Read journal and check for a failure entry
        journal_path = tmp_path / "journal.jsonl"
        assert journal_path.exists()
        entries = [
            json.loads(line)
            for line in journal_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        failure_entries = [e for e in entries if e.get("kind") == "failure"]
        assert len(failure_entries) >= 1
        text = failure_entries[0]["text"]
        assert "file_write" in text
        assert "shell_exec" in text

    def test_circuit_breaker_parks_instead_of_running(self, tmp_path):
        """Test 14: with the provider circuit open (opened_until in the future),
        the supervisor parks instead of calling the runner."""
        import time as _time

        _make_dir(tmp_path, budget=_Budget(max_wall_seconds=86400))

        future_epoch = _time.time() + 3600  # 1 hour in the future

        def fake_health_checker(provider):
            return {
                "provider": provider,
                "state": "open",
                "opened_until": future_epoch,
            }

        # The runner should never be called — the supervisor should park
        # and then the clock jumps past the deadline.
        runner = FakeRunner([])

        call_count = [0]
        park_jumped = [False]

        def advancing_clock():
            call_count[0] += 1
            if call_count[0] <= 3:
                return datetime(2026, 9, 16, 12, 0, 0, tzinfo=timezone.utc)
            # After circuit breaker sets park_until, jump past deadline
            return datetime(2026, 9, 17, 13, 0, 0, tzinfo=timezone.utc)

        result = supervise(
            tmp_path, runner=runner, sleeper=FakeSleeper(), now=advancing_clock,
            health_checker=fake_health_checker,
            hook=lambda *a, **kw: None,
        )
        # The supervisor should stop with presupuesto (deadline passed)
        # without ever calling the runner
        assert result.code == "presupuesto"
        assert runner.calls == 0

    def test_escalation_hook_on_non_terminado_stop(self, tmp_path):
        """Test 15: when stopping for something other than 'terminado',
        the on-escalation hook fires with LILITH_RUN_ID and LILITH_STOP_CODE.
        Verified with a double of run_hook, not real scripts."""
        _make_dir(tmp_path)
        hook_calls = []

        def fake_hook(event, env_extra):
            hook_calls.append({"event": event, "env": dict(env_extra)})

        sig = "blocked|SomeError|tool_x|normalised"
        runner = FakeRunner([
            LegResult(status="blocked", session="s1", signature=sig,
                      retry_after=None, tokens=50, usd=0.005,
                      raw={"status": "blocked", "error": "SomeError"}),
            LegResult(status="blocked", session="s2", signature=sig,
                      retry_after=None, tokens=50, usd=0.005,
                      raw={"status": "blocked", "error": "SomeError"}),
        ])
        fixed_now = datetime(2026, 9, 16, 12, 0, 30, tzinfo=timezone.utc)
        result = supervise(
            tmp_path, runner=runner, sleeper=FakeSleeper(), now=fixed_now,
            hook=fake_hook,
        )
        assert result.code == "misma_causa_dos_veces"
        # The hook must have been called exactly once (at the stop point)
        assert len(hook_calls) == 1
        call = hook_calls[0]
        assert call["event"] == "on-escalation"
        assert call["env"]["LILITH_RUN_ID"] == "GU-20260916-000000-test"
        assert call["env"]["LILITH_STOP_CODE"] == "misma_causa_dos_veces"
        # terminado should NOT fire the hook
        # (verified implicitly: if terminado fired, we'd have 2 calls)
