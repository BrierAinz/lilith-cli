"""Tests for ``lilith_tools.loop_ledger`` — per-run loop detection.

The ledger is the observable side of the agentic mini-loop. These tests
pin the two properties the operator actually relies on: the JSONL on
disk outlives the session, and the streak diagnosis fires when a
sub-agent repeats itself while staying quiet when it does not.
"""

from __future__ import annotations

import json

from lilith_tools import loop_ledger
from lilith_tools.loop_ledger import (
    LEDGER_FILENAME,
    LoopLedger,
    args_fingerprint,
    format_report,
    read_ledger,
    resolve_repeat_threshold,
    safe_target,
    summarize_entries,
)


def _turn(turn: int, tool: str, args: object, *, ok: bool = True, error: str = ""):
    """Build a ledger turn entry the way ``LoopLedger.record`` does."""
    entry = {
        "kind": "turn",
        "turn": turn,
        "tool": tool,
        "fingerprint": args_fingerprint(args),
        "target": safe_target(args),
        "ok": ok,
    }
    if error:
        entry["error"] = error
    return entry


# ── Fingerprint / target extraction ────────────────────────────────────


class TestFingerprint:
    def test_key_order_does_not_change_digest(self):
        assert args_fingerprint({"a": 1, "b": 2}) == args_fingerprint({"b": 2, "a": 1})

    def test_different_values_differ(self):
        assert args_fingerprint({"a": 1}) != args_fingerprint({"a": 2})

    def test_unserialisable_values_do_not_raise(self):
        # Mixed key types make ``sort_keys=True`` blow up inside json;
        # the fallback must still produce a usable digest.
        digest = args_fingerprint({1: "a", "b": 2})
        assert len(digest) == 16
        assert all(c in "0123456789abcdef" for c in digest)

    def test_non_dict_input_is_handled(self):
        assert len(args_fingerprint("not-a-dict")) == 16


class TestSafeTarget:
    def test_keeps_path_and_drops_content(self):
        assert safe_target({"path": "a.txt", "content": "secret"}) == "a.txt"

    def test_missing_or_non_string_path(self):
        assert safe_target({"content": "x"}) == ""
        assert safe_target({"path": 42}) == ""
        assert safe_target("nope") == ""

    def test_long_path_is_truncated(self):
        out = safe_target({"path": "x" * 500})
        assert len(out) == 200
        assert out.endswith("...")


# ── Threshold resolution ───────────────────────────────────────────────


class TestThreshold:
    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv(loop_ledger.THRESHOLD_ENV, raising=False)
        assert resolve_repeat_threshold() == 3

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv(loop_ledger.THRESHOLD_ENV, "5")
        assert resolve_repeat_threshold() == 5

    def test_explicit_beats_env(self, monkeypatch):
        monkeypatch.setenv(loop_ledger.THRESHOLD_ENV, "9")
        assert resolve_repeat_threshold(2) == 2

    def test_garbage_and_sub_two_fall_back(self, monkeypatch):
        monkeypatch.setenv(loop_ledger.THRESHOLD_ENV, "banana")
        assert resolve_repeat_threshold() == 3
        monkeypatch.setenv(loop_ledger.THRESHOLD_ENV, "1")
        assert resolve_repeat_threshold() == 3


# ── Diagnosis ──────────────────────────────────────────────────────────


class TestSummarize:
    def test_distinct_calls_are_not_a_loop(self):
        entries = [
            _turn(1, "file_write", {"path": "a.txt", "content": "a"}),
            _turn(2, "file_write", {"path": "b.txt", "content": "b"}),
            _turn(3, "file_read", {"path": "c.txt"}),
        ]
        summary = summarize_entries(entries)
        assert summary["turns"] == 3
        assert summary["distinct_calls"] == 3
        assert summary["repeated_calls"] == 0
        assert summary["max_repeat_streak"] == 0
        assert summary["possible_loop"] is False
        assert summary["loop_signal"] == ""
        assert summary["stuck_on"] is None
        assert summary["loop_warning"] == ""

    def test_identical_args_repeated_fires_identical_args(self):
        args = {"path": "a.txt", "content": "again"}
        entries = [_turn(n, "file_write", args) for n in (1, 2, 3)]
        summary = summarize_entries(entries)
        assert summary["possible_loop"] is True
        assert summary["loop_signal"] == "identical_args"
        assert summary["max_repeat_streak"] == 3
        assert summary["repeated_calls"] == 2  # two of the three were repeats
        assert summary["stuck_on"]["target"] == "a.txt"
        assert summary["stuck_on"]["count"] == 3
        assert summary["stuck_on"]["first_turn"] == 1
        assert summary["stuck_on"]["last_turn"] == 3
        assert "posible bucle" in summary["loop_warning"]

    def test_same_target_different_content_fires_same_target(self):
        entries = [
            _turn(1, "file_write", {"path": "draft.md", "content": "v1"}),
            _turn(2, "file_write", {"path": "draft.md", "content": "v2"}),
            _turn(3, "file_write", {"path": "draft.md", "content": "v3"}),
        ]
        summary = summarize_entries(entries)
        assert summary["possible_loop"] is True
        assert summary["loop_signal"] == "same_target"
        assert summary["max_repeat_streak"] == 0
        assert summary["max_target_streak"] == 3
        assert "contenido distinto" in summary["loop_warning"]

    def test_identical_args_wins_over_same_target(self):
        args = {"path": "a.txt", "content": "same"}
        entries = [_turn(n, "file_write", args) for n in (1, 2, 3)]
        assert summarize_entries(entries)["loop_signal"] == "identical_args"

    def test_interleaved_repeats_are_not_consecutive(self):
        entries = [
            _turn(1, "file_write", {"path": "a.txt", "content": "a"}),
            _turn(2, "file_read", {"path": "b.txt"}),
            _turn(3, "file_write", {"path": "a.txt", "content": "a"}),
        ]
        summary = summarize_entries(entries)
        assert summary["repeated_calls"] == 1
        assert summary["max_repeat_streak"] == 0
        assert summary["possible_loop"] is False

    def test_custom_threshold(self):
        args = {"path": "a.txt", "content": "x"}
        entries = [_turn(n, "file_write", args) for n in (1, 2, 3)]
        assert summarize_entries(entries, repeat_threshold=5)["possible_loop"] is False
        assert summarize_entries(entries, repeat_threshold=2)["possible_loop"] is True

    def test_two_identical_calls_is_the_boundary(self):
        args = {"path": "a.txt", "content": "x"}
        entries = [_turn(n, "file_write", args) for n in (1, 2)]
        summary = summarize_entries(entries)
        assert summary["max_repeat_streak"] == 2
        # Two in a row is below the default threshold (3), so not flagged.
        assert summary["possible_loop"] is False
        assert summarize_entries(entries, repeat_threshold=2)["possible_loop"] is True

    def test_errors_are_counted(self):
        entries = [
            _turn(1, "file_read", {"path": "missing.txt"}, ok=False, error="boom"),
            _turn(2, "file_read", {"path": "other.txt"}, ok=True),
        ]
        summary = summarize_entries(entries)
        assert summary["errors"] == 1

    def test_start_and_end_entries_are_ignored(self):
        entries = [
            {"kind": "start", "preset": "p"},
            _turn(1, "file_read", {"path": "a.txt"}),
            {"kind": "end", "status": "answered"},
        ]
        assert summarize_entries(entries)["turns"] == 1

    def test_empty_input(self):
        summary = summarize_entries([])
        assert summary["turns"] == 0
        assert summary["possible_loop"] is False


# ── The ledger object ──────────────────────────────────────────────────


class TestLoopLedger:
    def test_writes_start_turn_turn_end(self, tmp_path):
        ledger = LoopLedger(tmp_path, preset="p", max_turns=4)
        assert ledger.persisted is True
        ledger.record(1, "file_read", {"path": "a.txt"}, ok=True)
        ledger.record(2, "file_read", {"path": "a.txt"}, ok=False, error="boom")
        summary = ledger.close(status="answered")

        lines = [
            json.loads(line)
            for line in (tmp_path / LEDGER_FILENAME)
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        assert [line["kind"] for line in lines] == ["start", "turn", "turn", "end"]
        assert lines[0]["preset"] == "p"
        assert lines[0]["max_turns"] == 4
        assert lines[0]["repeat_threshold"] == 3
        assert lines[1]["tool"] == "file_read"
        assert lines[1]["target"] == "a.txt"
        assert lines[1]["ok"] is True
        assert lines[2]["ok"] is False
        assert lines[2]["error"] == "boom"
        assert lines[3]["status"] == "answered"
        assert lines[3]["possible_loop"] is False
        assert summary["turns"] == 2
        assert summary["errors"] == 1
        assert summary["status"] == "answered"

    def test_warning_fires_once_per_run(self, tmp_path):
        ledger = LoopLedger(tmp_path, preset="p")
        args = {"path": "a.txt", "content": "x"}
        entries = [
            ledger.record(n, "file_write", args, ok=True) for n in (1, 2, 3, 4)
        ]
        assert "warning" not in entries[0]
        assert "warning" not in entries[1]
        assert "warning" in entries[2]
        assert "warning" not in entries[3]
        assert entries[2]["streak"] == 3
        assert entries[2]["possible_loop"] is True
        assert entries[3]["streak"] == 4

    def test_error_is_truncated(self, tmp_path):
        ledger = LoopLedger(tmp_path, preset="p")
        entry = ledger.record(1, "file_read", {"path": "a"}, ok=False, error="e" * 900)
        assert len(entry["error"]) == 300

    def test_close_is_idempotent_on_status(self, tmp_path):
        ledger = LoopLedger(tmp_path, preset="p")
        ledger.record(1, "file_read", {"path": "a"}, ok=True)
        assert ledger.close(status="answered")["status"] == "answered"
        assert ledger.close(status="error")["status"] == "answered"

    def test_summary_exposes_workdir_and_path(self, tmp_path):
        ledger = LoopLedger(tmp_path, preset="p")
        summary = ledger.summary()
        assert summary["ledger"] == str(tmp_path / LEDGER_FILENAME)
        assert summary["workdir"] == str(tmp_path)
        assert summary["persisted"] is True

    def test_never_persists_file_content(self, tmp_path):
        ledger = LoopLedger(tmp_path, preset="p")
        ledger.record(
            1, "file_write", {"path": "a.txt", "content": "SUPER-SECRET"}, ok=True
        )
        ledger.close()
        raw = (tmp_path / LEDGER_FILENAME).read_text(encoding="utf-8")
        assert "SUPER-SECRET" not in raw
        assert "a.txt" in raw

    def test_unwritable_ledger_degrades_without_raising(self, tmp_path):
        # A directory where the file should be: the write must fail softly.
        (tmp_path / LEDGER_FILENAME).mkdir()
        ledger = LoopLedger(tmp_path, preset="p")
        assert ledger.persisted is False

        args = {"path": "a.txt", "content": "x"}
        for turn in (1, 2, 3):
            ledger.record(turn, "file_write", args, ok=True)
        summary = ledger.close()

        assert summary["persisted"] is False
        assert summary["turns"] == 3
        # In-memory detection keeps working even with no file on disk.
        assert summary["possible_loop"] is True


# ── Reading a ledger back ──────────────────────────────────────────────


class TestReadLedger:
    def test_round_trip_recomputes_diagnosis(self, tmp_path):
        ledger = LoopLedger(tmp_path, preset="ejecutor-kimi", max_turns=6)
        args = {"path": "same.txt", "content": "v"}
        for turn in (1, 2, 3):
            ledger.record(turn, "file_write", args, ok=True)
        ledger.close(status="partial")

        report = read_ledger(tmp_path / LEDGER_FILENAME)
        assert report["exists"] is True
        assert report["status"] == "partial"
        assert report["preset"] == "ejecutor-kimi"
        assert report["max_turns"] == 6
        assert report["malformed_lines"] == 0
        assert report["summary"]["possible_loop"] is True
        assert report["summary"]["loop_signal"] == "identical_args"

    def test_tolerates_truncated_lines(self, tmp_path):
        path = tmp_path / LEDGER_FILENAME
        path.write_text(
            json.dumps({"kind": "start", "preset": "p"})
            + "\n"
            + '{"kind": "turn", "tool":\n'  # killed mid-write
            + json.dumps(_turn(1, "file_read", {"path": "a.txt"}))
            + "\n",
            encoding="utf-8",
        )
        report = read_ledger(path)
        assert report["malformed_lines"] == 1
        assert report["summary"]["turns"] == 1
        # No ``end`` line → the run never closed cleanly.
        assert report["status"] == "running_or_interrupted"

    def test_missing_file(self, tmp_path):
        report = read_ledger(tmp_path / "nope.jsonl")
        assert report["exists"] is False
        assert report["status"] == "empty"
        assert report["summary"]["turns"] == 0

    def test_format_report_flags_and_clears(self, tmp_path):
        args = {"path": "a.txt", "content": "x"}
        ledger = LoopLedger(tmp_path, preset="p")
        for turn in (1, 2, 3):
            ledger.record(turn, "file_write", args, ok=True)
        ledger.close()
        text = format_report(read_ledger(tmp_path / LEDGER_FILENAME))
        assert "POSIBLE BUCLE" in text
        assert LEDGER_FILENAME in text

        clean_dir = tmp_path / "clean"
        clean_dir.mkdir()
        clean = LoopLedger(clean_dir, preset="p")
        clean.record(1, "file_read", {"path": "a.txt"}, ok=True)
        clean.close()
        assert "sin bucle detectado" in format_report(
            read_ledger(clean_dir / LEDGER_FILENAME)
        )


# ── CLI entry point ────────────────────────────────────────────────────


class TestMain:
    def test_exit_codes_and_output(self, tmp_path, capsys):
        args = {"path": "a.txt", "content": "x"}
        ledger = LoopLedger(tmp_path, preset="p")
        for turn in (1, 2, 3):
            ledger.record(turn, "file_write", args, ok=True)
        ledger.close()

        # 1 = a loop was detected (accepts the workdir, not just the file).
        assert loop_ledger._main([str(tmp_path)]) == 1
        assert "POSIBLE BUCLE" in capsys.readouterr().out

        # 2 = nothing to read, and it must complain on stderr.
        assert loop_ledger._main([str(tmp_path / "missing.jsonl")]) == 2
        assert "no existe" in capsys.readouterr().err

        # 2 = no arguments at all (usage text).
        assert loop_ledger._main([]) == 2
