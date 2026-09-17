"""Per-delegation loop ledger for agentic sub-agents.

The agentic mini-loop in :mod:`lilith_tools.delegate` already knows, turn
by turn, which tool the sub-agent called and whether it succeeded — but
that signal only ever reached ``logger.info``, and the project registers
no file handler, so it evaporated. From the outside, "working on a long
task" and "spinning on the same call" looked identical.

This module persists that signal as JSONL inside the run's own workdir
(``subagent_work/<preset>-<n>/loop_ledger.jsonl``) and derives a
diagnosis from it:

* ``max_repeat_streak`` — longest run of consecutive calls with the
  *identical* tool + argument fingerprint.
* ``max_target_streak`` — longest run of consecutive calls hitting the
  same ``tool`` + ``path`` even when the content differs. A sub-agent
  rewriting ``report.md`` eight times is also spinning.
* ``possible_loop`` — one of the two streaks reached the threshold
  (default 3; override with ``LILITH_LOOP_REPEAT_THRESHOLD``).
  ``loop_signal`` names which one fired.

This is *detection only*. Nothing here cancels, kills, retries or
throttles anything: a flagged run still finishes and still returns its
result. The ledger never stores
prompts, credentials or file contents — only the tool name, a hash of
the arguments and the target path.

Read it back with::

    python -m lilith_tools.loop_ledger <workdir>/loop_ledger.jsonl
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LEDGER_FILENAME = "loop_ledger.jsonl"
DEFAULT_REPEAT_THRESHOLD = 3
THRESHOLD_ENV = "LILITH_LOOP_REPEAT_THRESHOLD"

# The target path is the only argument value copied verbatim; cap it so a
# pathological path cannot bloat the ledger.
_MAX_TARGET_CHARS = 200
_MAX_ERROR_CHARS = 300


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def resolve_repeat_threshold(explicit: int | None = None) -> int:
    """Return the repeat threshold, floored at 2.

    Precedence: explicit argument, then ``LILITH_LOOP_REPEAT_THRESHOLD``,
    then :data:`DEFAULT_REPEAT_THRESHOLD`. A threshold below 2 would flag
    every single call, so anything smaller (or unparseable) falls back to
    the default instead of being honoured.
    """
    raw: Any = explicit
    if raw is None:
        env = os.environ.get(THRESHOLD_ENV, "")
        raw = env.strip() or None
    if raw is None:
        return DEFAULT_REPEAT_THRESHOLD
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_REPEAT_THRESHOLD
    return value if value >= 2 else DEFAULT_REPEAT_THRESHOLD


def args_fingerprint(args: Any) -> str:
    """Stable 16-hex-char digest of a tool call's arguments.

    Key order must not change the digest: two identical ``file_write``
    calls that serialise their arguments differently are still the same
    call as far as loop detection is concerned. Unserialisable values
    fall back to ``repr`` rather than raising — a ledger that breaks the
    run it observes is worse than no ledger.
    """
    if isinstance(args, dict):
        try:
            payload = json.dumps(args, sort_keys=True, default=str, ensure_ascii=False)
        except (TypeError, ValueError):
            payload = repr(sorted(args.items(), key=lambda kv: str(kv[0])))
    else:
        try:
            payload = json.dumps(args, default=str, ensure_ascii=False)
        except (TypeError, ValueError):
            payload = repr(args)
    return hashlib.sha256(payload.encode("utf-8", "replace")).hexdigest()[:16]


def safe_target(args: Any) -> str:
    """Return the call's ``path`` argument (truncated) or ``""``.

    Every tool in the agentic allow-list takes ``path``. Content-bearing
    arguments are deliberately *not* recorded, so the ledger cannot leak
    a payload it happened to observe.
    """
    if not isinstance(args, dict):
        return ""
    raw = args.get("path")
    if not isinstance(raw, str):
        return ""
    raw = raw.strip()
    if len(raw) > _MAX_TARGET_CHARS:
        return raw[: _MAX_TARGET_CHARS - 3] + "..."
    return raw


def _turn_entries(entries: Iterable[Any]) -> list[dict[str, Any]]:
    return [
        e
        for e in entries
        if isinstance(e, dict) and e.get("kind") == "turn"
    ]


def _worst_streak(
    turns: list[dict[str, Any]], key_of
) -> dict[str, Any] | None:
    """Longest run of consecutive turns sharing ``key_of(turn)``.

    Returns ``None`` when no two consecutive turns share a key. Ties keep
    the *last* streak seen, so the reported window is the most recent one
    — that is the one the operator is staring at right now.
    """
    worst: dict[str, Any] | None = None
    streak = 0
    prev_key: str | None = None
    first_turn: Any = None

    for turn in turns:
        key = key_of(turn) or ""
        if key and key == prev_key:
            streak += 1
        else:
            streak = 1
            first_turn = turn.get("turn")
        prev_key = key
        if key and streak >= 2 and (worst is None or streak >= worst["count"]):
            worst = {
                "tool": str(turn.get("tool") or ""),
                "target": str(turn.get("target") or ""),
                "fingerprint": str(turn.get("fingerprint") or ""),
                "count": streak,
                "first_turn": first_turn,
                "last_turn": turn.get("turn"),
            }
    return worst


def summarize_entries(
    entries: Iterable[Any], *, repeat_threshold: int | None = None
) -> dict[str, Any]:
    """Fold a list of ledger entries into a loop diagnosis.

    Accepts the in-memory turn dicts or the parsed contents of a ledger
    file; non-``turn`` entries (``start``/``end``) are ignored.
    """
    threshold = resolve_repeat_threshold(repeat_threshold)
    turns = _turn_entries(entries)
    total = len(turns)

    errors = 0
    distinct: set[str] = set()
    repeated_calls = 0
    for turn in turns:
        if turn.get("ok") is False:
            errors += 1
        fp = str(turn.get("fingerprint") or "")
        if not fp:
            continue
        if fp in distinct:
            repeated_calls += 1
        else:
            distinct.add(fp)

    worst_repeat = _worst_streak(turns, lambda t: str(t.get("fingerprint") or ""))
    worst_target = _worst_streak(
        turns,
        lambda t: (
            f"{t.get('tool')}|{t.get('target')}"
            if t.get("target")
            else ""
        ),
    )

    max_repeat_streak = int(worst_repeat["count"]) if worst_repeat else 0
    max_target_streak = int(worst_target["count"]) if worst_target else 0

    if max_repeat_streak >= threshold:
        signal = "identical_args"
        stuck_on = worst_repeat
    elif max_target_streak >= threshold:
        signal = "same_target"
        stuck_on = worst_target
    else:
        signal = ""
        stuck_on = None

    possible_loop = bool(signal)
    loop_warning = ""
    if stuck_on is not None:
        window = f"turnos {stuck_on['first_turn']}-{stuck_on['last_turn']}"
        where = f" sobre '{stuck_on['target']}'" if stuck_on["target"] else ""
        if signal == "identical_args":
            loop_warning = (
                f"posible bucle: {stuck_on['tool']}{where} repetido "
                f"{stuck_on['count']} veces con argumentos idénticos ({window})"
            )
        else:
            loop_warning = (
                f"posible bucle: {stuck_on['count']} llamadas consecutivas a "
                f"{stuck_on['tool']}{where} con contenido distinto ({window})"
            )

    return {
        "turns": total,
        "distinct_calls": len(distinct),
        "repeated_calls": repeated_calls,
        "errors": errors,
        "max_repeat_streak": max_repeat_streak,
        "max_target_streak": max_target_streak,
        "repeat_threshold": threshold,
        "possible_loop": possible_loop,
        "loop_signal": signal,
        "stuck_on": stuck_on,
        "loop_warning": loop_warning,
    }


class LoopLedger:
    """Append-only JSONL ledger for one agentic delegation.

    Every failure path degrades instead of raising: if the ledger file
    cannot be opened or written, ``persisted`` stays ``False`` and the
    in-memory diagnosis keeps working. Observing a run must never be able
    to break the run.
    """

    def __init__(
        self,
        workdir: Path | str,
        *,
        preset: str = "",
        max_turns: int | None = None,
        repeat_threshold: int | None = None,
    ) -> None:
        self.workdir = Path(workdir)
        self.preset = str(preset or "")
        self.max_turns = max_turns
        self.repeat_threshold = resolve_repeat_threshold(repeat_threshold)
        self.path = self.workdir / LEDGER_FILENAME
        self.persisted = False
        self.status = "running"
        self._turns: list[dict[str, Any]] = []
        self._warned = False
        self._open()

    # ── File plumbing ─────────────────────────────────────────────────

    def _append(self, record: dict[str, Any]) -> bool:
        """Append one JSON line. Returns False instead of raising.

        The file is opened, written and closed per record rather than
        kept open: turns are few, and an unflushed buffer would defeat
        the whole point of ``tail -f`` on a still-running sub-agent.
        """
        try:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            return True
        except (OSError, ValueError):
            return False

    def _open(self) -> None:
        self.persisted = self._append(
            {
                "kind": "start",
                "ts": _utc_now(),
                "preset": self.preset,
                "max_turns": self.max_turns,
                "repeat_threshold": self.repeat_threshold,
                "workdir": str(self.workdir),
            }
        )

    # ── Recording ─────────────────────────────────────────────────────

    def record(
        self,
        turn: int,
        tool: str,
        args: Any,
        *,
        ok: bool,
        error: str = "",
    ) -> dict[str, Any]:
        """Record one tool call and return its entry plus loop state.

        The returned dict is the plain entry extended with ``streak``,
        ``target_streak``, ``possible_loop`` and — only on the turn where
        the threshold is first crossed — ``warning``.
        """
        entry: dict[str, Any] = {
            "kind": "turn",
            "ts": _utc_now(),
            "turn": int(turn),
            "tool": str(tool),
            "fingerprint": args_fingerprint(args),
            "target": safe_target(args),
            "ok": bool(ok),
        }
        if error:
            entry["error"] = str(error)[:_MAX_ERROR_CHARS]
        self._turns.append(entry)
        if self.persisted:
            self.persisted = self._append(entry)

        summary = self.summary()
        result = dict(entry)
        result["streak"] = summary["max_repeat_streak"]
        result["target_streak"] = summary["max_target_streak"]
        result["possible_loop"] = summary["possible_loop"]
        if summary["possible_loop"] and not self._warned:
            self._warned = True
            result["warning"] = summary["loop_warning"]
        return result

    def close(self, status: str = "finished") -> dict[str, Any]:
        """Write the trailing ``end`` record and return the diagnosis."""
        if self.status == "running":
            self.status = str(status or "finished")
        summary = self.summary()
        if self.persisted:
            self._append(
                {
                    "kind": "end",
                    "ts": _utc_now(),
                    "status": self.status,
                    "turns": summary["turns"],
                    "distinct_calls": summary["distinct_calls"],
                    "errors": summary["errors"],
                    "max_repeat_streak": summary["max_repeat_streak"],
                    "max_target_streak": summary["max_target_streak"],
                    "possible_loop": summary["possible_loop"],
                    "loop_signal": summary["loop_signal"],
                }
            )
        return summary

    # ── Reporting ─────────────────────────────────────────────────────

    def summary(self) -> dict[str, Any]:
        diag = summarize_entries(self._turns, repeat_threshold=self.repeat_threshold)
        diag.update(
            {
                "preset": self.preset,
                "workdir": str(self.workdir),
                # Always exposed, even when the write failed, so a missing
                # ledger can be diagnosed. ``persisted`` is the truth.
                "ledger": str(self.path),
                "persisted": self.persisted,
                "status": self.status,
            }
        )
        return diag


def read_ledger(path: Path | str) -> dict[str, Any]:
    """Parse a ledger file and recompute its diagnosis.

    Used after the fact (or from ``python -m lilith_tools.loop_ledger``)
    when the in-memory summary is gone because the session died. Broken
    lines are counted, not raised: a truncated write must not make the
    rest of the file unreadable.
    """
    p = Path(path)
    entries: list[dict[str, Any]] = []
    malformed = 0
    if p.is_file():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if isinstance(obj, dict):
                entries.append(obj)
            else:
                malformed += 1

    status = "empty"
    preset = ""
    max_turns: Any = None
    repeat_threshold: Any = None
    for entry in entries:
        if entry.get("kind") == "start":
            preset = str(entry.get("preset") or "")
            max_turns = entry.get("max_turns")
            repeat_threshold = entry.get("repeat_threshold")
            status = "running_or_interrupted"
    for entry in reversed(entries):
        if entry.get("kind") == "end":
            status = str(entry.get("status") or "unknown")
            break

    return {
        "ledger": str(p),
        "exists": p.is_file(),
        "status": status,
        "preset": preset,
        "max_turns": max_turns,
        "repeat_threshold": repeat_threshold,
        "malformed_lines": malformed,
        "entries": entries,
        "summary": summarize_entries(
            entries, repeat_threshold=repeat_threshold
        ),
    }


def format_report(report: dict[str, Any]) -> str:
    """Render :func:`read_ledger` output as a few lines of plain text."""
    summary = report.get("summary", {}) or {}
    lines = [
        f"ledger: {report.get('ledger', '?')}",
        f"estado: {report.get('status', '?')}"
        + (f" | preset: {report['preset']}" if report.get("preset") else "")
        + (
            f" | max_turns: {report['max_turns']}"
            if report.get("max_turns") is not None
            else ""
        ),
        (
            f"turnos: {summary.get('turns', 0)}"
            f" | llamadas distintas: {summary.get('distinct_calls', 0)}"
            f" | repetidas: {summary.get('repeated_calls', 0)}"
            f" | errores: {summary.get('errors', 0)}"
        ),
        (
            f"rachas: identicas={summary.get('max_repeat_streak', 0)}"
            f" mismo_target={summary.get('max_target_streak', 0)}"
            f" (umbral {summary.get('repeat_threshold', DEFAULT_REPEAT_THRESHOLD)})"
        ),
    ]
    if summary.get("possible_loop"):
        lines.append(f"dictamen: POSIBLE BUCLE — {summary.get('loop_warning', '')}")
    else:
        lines.append("dictamen: sin bucle detectado")
    if report.get("malformed_lines"):
        lines.append(f"aviso: {report['malformed_lines']} lineas ilegibles ignoradas")
    return "\n".join(lines)


def _main(argv: list[str]) -> int:
    if not argv:
        print(
            "uso: python -m lilith_tools.loop_ledger <ledger.jsonl> [...]\n"
            "     (o un workdir, del que se toma loop_ledger.jsonl)"
        )
        return 2
    exit_code = 0
    for raw in argv:
        candidate = Path(raw)
        if candidate.is_dir():
            candidate = candidate / LEDGER_FILENAME
        report = read_ledger(candidate)
        print(format_report(report))
        if not report["exists"]:
            print(f"aviso: {candidate} no existe", file=sys.stderr)
            exit_code = max(exit_code, 2)
        elif report["summary"].get("possible_loop"):
            exit_code = max(exit_code, 1)
    return exit_code


if __name__ == "__main__":  # pragma: no cover — manual inspection entry point
    raise SystemExit(_main(sys.argv[1:]))
