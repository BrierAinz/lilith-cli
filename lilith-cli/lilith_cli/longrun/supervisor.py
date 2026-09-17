"""Supervisor: the long-running mission loop.

Sustains an autonomous mission for >24 hours without a human present,
surviving quota exhaustion, process restarts, and transient failures.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..task_workspace import atomic_json
from ..provider_health import ProviderHealthRegistry
from ..hooks import run_hook as _run_hook


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class LegResult:
    status: str                # "verified"|"blocked"|"cancelled"|"crashed"|"paused"
    session: str | None
    signature: str | None      # normalised failure cause, or None
    retry_after: str | None    # ISO-Z if the cause is quota
    tokens: int = 0
    usd: float = 0.0
    raw: dict = field(default_factory=dict)


@dataclass
class StopReason:
    code: str                  # terminado | decision_ajena | irreversible |
                               # misma_causa_dos_veces | presupuesto |
                               # parada_solicitada
    detail: str
    decision_needed: str | None = None   # one sentence, if an operator decision is needed


# ---------------------------------------------------------------------------
# failure_signature
# ---------------------------------------------------------------------------

_NORMALISE_RE = re.compile(
    r"("
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[^\s]*"   # ISO timestamps
    r"|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"  # UUIDs
    r"|[A-Z]:\\(?:[^\s\\]+\\)+"                           # Windows paths
    r"|/(?:tmp|var|home|Users)/[^\s]+"                    # Unix temp paths
    r"|\b\d+\b"                                           # bare numbers (PID, port…)
    r")",
    re.IGNORECASE,
)


def failure_signature(payload: dict) -> str:
    """Normalise a task() failure JSON into a stable, comparable signature."""
    status = payload.get("status", "unknown")
    error = str(payload.get("error", ""))

    # Extract just the exception type name (first token that looks like a
    # class name, e.g. "FileNotFoundError" from "FileNotFoundError: no such
    # file '/tmp/abc/work'").  Falls back to the whole normalised string.
    error_type = error.split(":")[0].split()[0] if error else ""

    # Last tool with an error from progress.activity
    last_tool = ""
    progress = payload.get("progress") or {}
    for entry in reversed(progress.get("activity", [])):
        if entry.get("event") == "tool_result" and entry.get("tool"):
            last_tool = entry["tool"]
            break

    # Build the message portion: first 120 chars of the error text with
    # numbers, paths, UUIDs and timestamps replaced by '#'.
    raw_msg = error[:120]
    normalised_msg = _NORMALISE_RE.sub("#", raw_msg)

    parts = [status]
    if error_type:
        parts.append(error_type)
    if last_tool:
        parts.append(last_tool)
    parts.append(normalised_msg)
    return "|".join(parts)


# ---------------------------------------------------------------------------
# classify
# ---------------------------------------------------------------------------

_QUOTA_PATTERNS = [
    "usage limit",
    "rate limit",
    "429",
    "quota",
    "insufficient_quota",
    "try again at",
]

# "try again at Sep 19th, 2026 2:09 AM"
_RETRY_AT_HUMAN = re.compile(
    r"try again at\s+"
    r"(\w+)\s+(\d+)\w*,?\s+(\d{4})\s+(\d{1,2}):(\d{2})\s*(AM|PM)",
    re.IGNORECASE,
)

_MONTH_NAMES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _parse_retry_after(text: str) -> str | None:
    """Extract a retry-after datetime from error text. Returns ISO-Z or None."""
    # Human format: "try again at Sep 19th, 2026 2:09 AM"
    m = _RETRY_AT_HUMAN.search(text)
    if m:
        month_name, day, year, hour, minute, ampm = m.groups()
        month = _MONTH_NAMES.get(month_name[:3].lower())
        if month is None:
            return None
        h = int(hour)
        if ampm.upper() == "PM" and h != 12:
            h += 12
        elif ampm.upper() == "AM" and h == 12:
            h = 0
        dt = datetime(int(year), month, int(day), h, int(minute), tzinfo=timezone.utc)
        return dt.isoformat()

    # ISO 8601 in the text
    iso_match = re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:[^\s]*)", text)
    if iso_match:
        raw = iso_match.group(0)
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc).isoformat()
        except ValueError:
            pass

    # retry-after header in seconds (bare integer)
    secs_match = re.search(r"retry-after:\s*(\d+)", text, re.IGNORECASE)
    if secs_match:
        delta = int(secs_match.group(1))
        dt = datetime.now(timezone.utc) + __import__("datetime").timedelta(seconds=delta)
        return dt.isoformat()

    return None


def classify(payload: dict) -> str:
    """Classify a task() output payload.

    Returns one of: "ok", "quota", "decision", "irreversible", "transient".
    """
    status = payload.get("status", "")
    error_text = str(payload.get("error", ""))
    response_text = str(payload.get("response", ""))
    progress = payload.get("progress") or {}
    progress_status = progress.get("status", "")

    # --- ok ---
    if status == "verified":
        return "ok"

    # --- quota ---
    combined = (error_text + " " + response_text).lower()
    for pattern in _QUOTA_PATTERNS:
        if pattern in combined:
            return "quota"

    # --- decision ---
    if status == "blocked" or progress_status == "blocked":
        if "ask_operator" in response_text or progress_status == "blocked":
            return "decision"

    # --- irreversible ---
    activity = progress.get("activity", [])
    for entry in activity:
        tool = (entry.get("tool") or "").lower()
        if any(word in tool for word in ("commit", "push", "delete", "publish", "remove")):
            return "irreversible"

    return "transient"


# ---------------------------------------------------------------------------
# run_leg  (real subprocess runner)
# ---------------------------------------------------------------------------

def run_leg(contract, consumed, session_id, directory) -> LegResult:
    """Launch ONE leg of the saga as a subprocess and return its normalised result."""
    from .contract import RunContract  # type: ignore[attr-defined]

    request = {
        "text": contract.goal,
        "root": contract.project_root,
        "max_iterations": contract.budget.max_iterations_per_leg,
        "quiet": True,
        "repair_attempts": 1,
        "control_file": str(directory / "control.json"),
    }
    if contract.verify:
        request["verify"] = contract.verify
    if session_id:
        request["resume"] = session_id

    request_path = directory / "request.json"
    atomic_json(request_path, request)

    env = dict(os.environ, PYTHONUTF8="1")
    result = subprocess.run(
        [sys.executable, "-c",
         "from lilith_cli.work_session import task_from_file; task_from_file()",
         str(request_path)],
        cwd=contract.project_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

    # Find the LAST valid JSON line in stdout (the process also prints progress).
    payload = None
    for line in result.stdout.splitlines():
        try:
            candidate = json.loads(line)
            if isinstance(candidate, dict) and "status" in candidate:
                payload = candidate
        except (ValueError, UnicodeError):
            continue

    if payload is None:
        stderr_tail = "\n".join(result.stderr.splitlines()[-20:])
        sig = failure_signature({
            "status": "crashed",
            "error": stderr_tail,
        })
        return LegResult(
            status="crashed",
            session=None,
            signature=sig,
            retry_after=None,
            raw={"stderr": stderr_tail},
        )

    status = payload.get("status", "blocked")
    usage = payload.get("usage") or {}
    tokens = usage.get("total_tokens", 0) or 0
    usd = usage.get("usd", 0.0) or 0.0
    session = payload.get("session")

    kind = classify(payload)
    sig = None if kind == "ok" else failure_signature(payload)
    retry_after = _parse_retry_after(str(payload.get("error", ""))) if kind == "quota" else None

    return LegResult(
        status=status,
        session=session,
        signature=sig,
        retry_after=retry_after,
        tokens=tokens,
        usd=usd,
        raw=payload,
    )


# ---------------------------------------------------------------------------
# supervise
# ---------------------------------------------------------------------------

def _load_state(directory: Path) -> dict:
    path = directory / "state.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"session": None, "signatures": [], "park_until": None, "stopped": None}


def _save_state(directory: Path, state: dict) -> None:
    atomic_json(directory / "state.json", state)


def _read_control(directory: Path) -> dict:
    path = directory / "control.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _now_utc(now=None) -> datetime:
    if now is not None:
        if callable(now):
            return now()
        return now
    return datetime.now(timezone.utc)


def _check_pending_tools(session_id: str | None) -> list[str]:
    """Return the list of pending tool names from a session checkpoint.

    If the checkpoint doesn't exist or has no pending tools, returns [].
    The checkpoint lives at ``_CONVERSATIONS_DIR / "<session>.json"``.
    """
    if not session_id:
        return []
    try:
        from ..repl import _CONVERSATIONS_DIR
        path = _CONVERSATIONS_DIR / f"{session_id}.json"
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        progress = data.get("progress") or {}
        pending = progress.get("pending") or []
        # Each pending entry may be a dict with a "tool" key, or a string.
        names = []
        for item in pending:
            if isinstance(item, dict):
                names.append(item.get("tool") or item.get("name") or str(item))
            else:
                names.append(str(item))
        return names
    except Exception:
        return []


def _fire_escalation(contract, reason: StopReason, *, hook=_run_hook) -> None:
    """Fire the on-escalation hook when stopping for a non-terminado reason."""
    hook("on-escalation", {
        "LILITH_RUN_ID": getattr(contract, "run_id", ""),
        "LILITH_STOP_CODE": reason.code,
        "LILITH_DECISION": reason.decision_needed or "",
    })


def supervise(
    directory: Path,
    *,
    runner=run_leg,
    sleeper=time.sleep,
    now=None,
    health_checker=None,
    hook=_run_hook,
    pending_checker=_check_pending_tools,
) -> StopReason:
    """The main loop: sustain a long-running mission."""
    # Lazy imports — these modules may not exist yet; we code against the
    # contract signatures so the import will work once they land.
    from .contract import RunContract, Consumed, exhausted  # type: ignore[attr-defined]
    from .journal import Journal  # type: ignore[attr-defined]
    from .evidence import Evidence, report  # type: ignore[attr-defined]

    directory = Path(directory)
    contract = RunContract.load(directory)
    consumed = Consumed.load(directory)
    state = _load_state(directory)
    journal = Journal(directory / "journal.jsonl")
    evidence = Evidence(directory / "evidence.jsonl")

    # --- Already stopped? Return immediately. ---
    if state.get("stopped"):
        stopped = state["stopped"]
        return StopReason(
            code=stopped["code"],
            detail=stopped.get("detail", ""),
            decision_needed=stopped.get("decision_needed"),
        )

    backoff_seconds = 60  # initial backoff for quota retries

    while True:
        current = _now_utc(now)

        # 1. Operator stop
        ctrl = _read_control(directory)
        if ctrl.get("action") == "cancel":
            reason = StopReason("parada_solicitada", "El operador solicitó cancelar.")
            state["stopped"] = {"code": reason.code, "detail": reason.detail}
            _save_state(directory, state)
            journal.append("escalation", reason.detail)
            report_text = report(contract, consumed, journal, evidence,
                                 stop_reason=reason.code, now=current)
            (directory / "INFORME.md").write_text(report_text, encoding="utf-8")
            _fire_escalation(contract, reason, hook=hook)
            return reason

        # 2. Budget
        tag = exhausted(contract, consumed, current)
        if tag:
            reason = StopReason("presupuesto", tag)
            state["stopped"] = {"code": reason.code, "detail": reason.detail}
            _save_state(directory, state)
            journal.append("escalation", f"Presupuesto agotado: {tag}")
            report_text = report(contract, consumed, journal, evidence,
                                 stop_reason=reason.code, now=current)
            (directory / "INFORME.md").write_text(report_text, encoding="utf-8")
            _fire_escalation(contract, reason, hook=hook)
            return reason

        # 3. Parking (quota cooldown)
        park_until_str = state.get("park_until")
        if park_until_str:
            park_dt = datetime.fromisoformat(park_until_str.replace("Z", "+00:00"))
            deadline_dt = datetime.fromisoformat(contract.deadline.replace("Z", "+00:00"))
            while True:
                current = _now_utc(now)
                remaining_park = (park_dt - current).total_seconds()
                remaining_deadline = (deadline_dt - current).total_seconds()
                if remaining_park <= 0 or remaining_deadline <= 0:
                    break
                chunk = min(60, remaining_park, remaining_deadline)
                if chunk <= 0:
                    break
                sleeper(chunk)
                # Re-read control between chunks
                ctrl = _read_control(directory)
                if ctrl.get("action") == "cancel":
                    reason = StopReason("parada_solicitada",
                                        "El operador solicitó cancelar durante aparcamiento.")
                    state["stopped"] = {"code": reason.code, "detail": reason.detail}
                    _save_state(directory, state)
                    journal.append("escalation", reason.detail)
                    report_text = report(contract, consumed, journal, evidence,
                                         stop_reason=reason.code, now=current)
                    (directory / "INFORME.md").write_text(report_text, encoding="utf-8")
                    _fire_escalation(contract, reason, hook=hook)
                    return reason
            state["park_until"] = None

        # Re-check budget after parking (deadline may have passed)
        current = _now_utc(now)
        tag = exhausted(contract, consumed, current)
        if tag:
            reason = StopReason("presupuesto", tag)
            state["stopped"] = {"code": reason.code, "detail": reason.detail}
            _save_state(directory, state)
            journal.append("escalation", f"Presupuesto agotado: {tag}")
            report_text = report(contract, consumed, journal, evidence,
                                 stop_reason=reason.code, now=current)
            (directory / "INFORME.md").write_text(report_text, encoding="utf-8")
            _fire_escalation(contract, reason, hook=hook)
            return reason

        # 4. Circuit breaker — park if the provider is known to be down
        if health_checker is not None:
            provider = getattr(contract, "provider", "default")
            health = health_checker(provider)
            if health.get("state") == "open":
                opened_until = float(health.get("opened_until", 0))
                park_iso = datetime.fromtimestamp(opened_until, tz=timezone.utc).isoformat()
                state["park_until"] = park_iso
                _save_state(directory, state)
                continue  # loop back to the parking block

        # 5. Pending tools — never resume a session with unknown results
        session_id = state.get("session")
        pending = pending_checker(session_id)
        if pending:
            # Log the dangling tools as a failure
            tool_names = ", ".join(pending)
            journal.append("failure",
                           f"Herramientas con resultado desconocido: {tool_names}")
            # Start a NEW leg (no resume). Seed with journal digest + warning.
            digest = journal.digest(4000)
            warning = (
                f"ADVERTENCIA: las siguientes herramientas quedaron con "
                f"resultado desconocido en la sesión anterior: {tool_names}. "
                f"Comprueba su efecto en disco antes de repetirlas."
            )
            state["session"] = None   # drop the tainted session
            _save_state(directory, state)
            # The runner receives session_id=None, so it starts fresh.
            # We stash the seed text in the contract for the runner to pick up
            # via a temporary attribute (it won't persist).
            _seed_text = f"{digest}\n\n{warning}" if digest else warning
            object.__setattr__(contract, "_pending_seed", _seed_text)
            session_id = None

        # 6. Run a leg
        leg = runner(contract, consumed, session_id, directory)

        # Accumulate consumption
        consumed.legs += 1
        consumed.tokens += leg.tokens
        consumed.usd += leg.usd
        consumed.save(directory)

        # Update session
        if leg.session:
            state["session"] = leg.session

        # Journal: log the leg
        journal.append("leg", f"Tramo {consumed.legs}: status={leg.status}, "
                        f"tokens={leg.tokens}, usd={leg.usd}")

        # 5. Classify and decide
        kind = classify(leg.raw) if leg.raw else ("ok" if leg.status == "verified" else "transient")

        if kind == "ok":
            reason = StopReason("terminado", "Misión completada y verificada.")
            state["stopped"] = {"code": reason.code, "detail": reason.detail}
            _save_state(directory, state)
            report_text = report(contract, consumed, journal, evidence,
                                 stop_reason=reason.code, now=_now_utc(now))
            (directory / "INFORME.md").write_text(report_text, encoding="utf-8")
            return reason

        if kind == "decision":
            response_text = str(leg.raw.get("response", ""))
            decision_phrase = response_text[:200].strip() or "Se necesita decisión del operador."
            reason = StopReason("decision_ajena", "El agente necesita una decisión.",
                                decision_needed=decision_phrase)
            state["stopped"] = {"code": reason.code, "detail": reason.detail,
                                "decision_needed": reason.decision_needed}
            _save_state(directory, state)
            journal.append("escalation",
                           f"Decisión requerida: {reason.decision_needed}")
            report_text = report(contract, consumed, journal, evidence,
                                 stop_reason=reason.code, now=_now_utc(now))
            (directory / "INFORME.md").write_text(report_text, encoding="utf-8")
            _fire_escalation(contract, reason, hook=hook)
            return reason

        if kind == "irreversible":
            reason = StopReason("irreversible",
                                "Se detectó actividad irreversible (commit/push/delete).")
            state["stopped"] = {"code": reason.code, "detail": reason.detail}
            _save_state(directory, state)
            journal.append("escalation", reason.detail)
            report_text = report(contract, consumed, journal, evidence,
                                 stop_reason=reason.code, now=_now_utc(now))
            (directory / "INFORME.md").write_text(report_text, encoding="utf-8")
            _fire_escalation(contract, reason, hook=hook)
            return reason

        if kind == "quota":
            # Set park_until from retry_after or exponential backoff
            if leg.retry_after:
                state["park_until"] = leg.retry_after
                backoff_seconds = 60  # reset backoff when we get a concrete time
            else:
                park_dt = _now_utc(now) + __import__("datetime").timedelta(seconds=backoff_seconds)
                state["park_until"] = park_dt.isoformat()
                backoff_seconds = min(backoff_seconds * 2, 1800)  # cap at 30 min
            _save_state(directory, state)
            # NOT a failure — continue the loop
            continue

        # transient
        sig = leg.signature
        if sig and sig in state.get("signatures", []):
            reason = StopReason("misma_causa_dos_veces",
                                f"Misma causa repetida: {sig}")
            state["stopped"] = {"code": reason.code, "detail": reason.detail}
            _save_state(directory, state)
            journal.append("escalation", reason.detail)
            report_text = report(contract, consumed, journal, evidence,
                                 stop_reason=reason.code, now=_now_utc(now))
            (directory / "INFORME.md").write_text(report_text, encoding="utf-8")
            _fire_escalation(contract, reason, hook=hook)
            return reason
        if sig:
            state.setdefault("signatures", []).append(sig)
        if leg.session:
            state["session"] = leg.session
        _save_state(directory, state)
        # continue with next leg
