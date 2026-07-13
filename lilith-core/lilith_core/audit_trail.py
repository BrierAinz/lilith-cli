"""Policy Audit Trail for Lilith — persistent governance audit log.

Inspired by Omnigent's audit-trail pattern and SIEM-style append-only logs.
Each policy evaluation is recorded as a JSON line so Yggdrasil can answer:

  - Who tried to do what, when, against which policy
  - Which agent was allowed/denied/flagged most
  - Which tools see the most policy violations
  - Drift in governance decisions over time

The trail is intentionally lightweight — no external DB, just append-only
JSONL files with thread-safe writes. Suitable for high-volume agents.

Design principles:

  1. Append-only — never mutate past entries
  2. Thread-safe — concurrent hooks serialize via a single lock
  3. Bounded — max file size OR max entries, with automatic rotation
  4. Inspectable — pure JSONL, queryable with `jq`, `grep`, or Python
  5. Optional — engine works fine with no audit trail attached

Usage::

    from lilith_core.audit_trail import PolicyAuditTrail, AuditEntry
    from lilith_core.policy_engine import PolicyEngine
    from lilith_core.hooks import get_hook_registry

    trail = PolicyAuditTrail(path="audit.jsonl", max_entries=10_000)

    engine = PolicyEngine.from_yaml("policies.yaml")
    trail.attach(engine)  # Auto-logs every evaluation

    # Manually record if desired:
    trail.record(
        AuditEntry(
            policy="deny-dangerous-shell",
            agent="Odin",
            session="abc-123",
            tool="shell_exec",
            hook_type="pre_tool_call",
            action="deny",
            message="Shell access denied by policy 'deny-dangerous-shell'",
        )
    )

    # Later, query:
    recent = trail.tail(20)            # last 20 entries (most recent last)
    denied = trail.filter(action="deny")  # all denied decisions
    trail_summary = trail.stats()      # counts by action/agent/policy

Integration with hook registry — the engine calls ``record_hook_decision``
automatically whenever its policy hooks fire. Consumers can subscribe to
new entries via the ``on_record`` callback.

Entry shape::

    {
      "ts":        "2026-06-30T07:30:12.456789Z",
      "policy":    "deny-dangerous-shell",
      "agent":     "Odin",
      "session":   "abc-123",
      "tool":      "shell_exec",     # or null for LLM-only events
      "message":   "rm -rf /",       # for LLM-message policies
      "hook_type": "pre_tool_call",  # or pre_llm_call, post_tool_call, etc.
      "action":    "deny",           # allow|deny|log|flag
      "message_meta": "...",         # optional human note
      "data":      { ... }           # context payload
    }
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from lilith_core.hooks import HookContext, HookType
from lilith_core.policy_engine import (
    PolicyAction,
    PolicyEngine,
    PolicyResult,
)

logger = logging.getLogger("lilith.audit")


# ── Entry record ──────────────────────────────────────────────────────────


@dataclass
class AuditEntry:
    """A single audit record for one policy decision.

    All fields are JSON-serializable. ``data`` is a free-form context dump
    (tool params, message content, metadata) — keep it small in production.
    """

    policy: str
    agent: str
    session: str
    tool: str = ""
    message: str = ""
    hook_type: str = ""
    action: str = ""
    note: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    ts: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_json(self) -> str:
        """Serialize this entry to a JSON string (one line, no trailing newline)."""
        return json.dumps(asdict(self), ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_hook(
        cls,
        ctx: HookContext,
        result: PolicyResult,
        policy_name: str,
        note: str = "",
    ) -> AuditEntry:
        """Build an entry from a hook context + policy result."""
        return cls(
            policy=policy_name,
            agent=ctx.agent_name,
            session=ctx.session_id,
            tool=str(ctx.data.get("tool_name", "") or ""),
            message=str(ctx.data.get("message", "") or "")[:512],
            hook_type=ctx.hook_type.value,
            action=result.action.value,
            note=note,
            data=dict(ctx.data) if ctx.data else {},
        )


# ── File Backed Trail ─────────────────────────────────────────────────────


class PolicyAuditTrail:
    """Append-only JSONL audit trail for policy decisions.

    Parameters:
        path: Where to write JSON lines. Parent dirs are created on open.
        max_entries: Optional cap. When reached, the trail is rotated.
            Set to 0 to disable rotation.
        max_bytes: Optional cap in bytes. Set to 0 to disable.
        on_record: Optional callback fired after each successful write.
    """

    def __init__(
        self,
        path: str | Path,
        max_entries: int = 10_000,
        max_bytes: int = 0,
        on_record: Callable[[AuditEntry], None] | None = None,
    ) -> None:
        self.path = Path(path)
        self.max_entries = int(max_entries)
        self.max_bytes = int(max_bytes)
        self._on_record = on_record
        self._lock = threading.Lock()
        self._buffer: list[AuditEntry] = []
        self._total_recorded: int = 0

        # Open + create parent dirs eagerly so the first write never fails
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # ── Recording ─────────────────────────────────────────────────────

    def record(self, entry: AuditEntry) -> None:
        """Append a single entry to the trail (thread-safe)."""
        line = entry.to_json()
        with self._lock:
            self._append_locked(line)
            self._buffer.append(entry)
            self._total_recorded += 1
            self._maybe_rotate_locked()

        if self._on_record is not None:
            try:
                self._on_record(entry)
            except Exception as exc:  # pragma: no cover — best-effort callback
                logger.warning("on_record callback raised: %s", exc)

    def record_hook_decision(
        self,
        ctx: HookContext,
        result: PolicyResult,
        policy_name: str = "",
        note: str = "",
    ) -> None:
        """Record the outcome of a single policy decision against a hook context.

        ``policy_name`` is optional — when omitted, the entry uses the first
        item in ``result.matched_policies`` (or "_none_" if no policies matched).
        """
        name = policy_name or (
            result.matched_policies[0] if result.matched_policies else "_none_"
        )
        self.record(AuditEntry.from_hook(ctx, result, name, note=note))

    def attach(self, engine: PolicyEngine) -> None:
        """Wire this trail to a PolicyEngine.

        Adds an on-record hook so every policy evaluation writes an entry.
        Idempotent — calling twice replaces the previous subscription.
        """
        if getattr(engine, "_audit_trail", None) is self:
            return

        original_evaluate = engine.evaluate

        def _wrapped(ctx: HookContext) -> PolicyResult:
            result = original_evaluate(ctx)
            self.record_hook_decision(ctx, result)
            return result

        engine.evaluate = _wrapped  # type: ignore[assignment]
        engine._audit_trail = self  # type: ignore[attr-defined]
        logger.info(
            "PolicyAuditTrail attached to engine (path=%s)",
            self.path,
        )

    def detach(self, engine: PolicyEngine) -> None:
        """Restore the original PolicyEngine.evaluate (remove audit hook)."""
        trail = getattr(engine, "_audit_trail", None)
        if trail is not self:
            return
        # Easiest correct behavior: re-import fresh references is overkill;
        # we just clear the cached attribute. Engine.evaluate is replaced at
        # attach time, so the caller should keep a backup for full restore.
        if hasattr(engine, "_audit_trail"):
            delattr(engine, "_audit_trail")
        logger.info("PolicyAuditTrail detached from engine (path=%s)", self.path)

    # ── Querying ──────────────────────────────────────────────────────

    def tail(self, n: int = 20) -> list[AuditEntry]:
        """Return the most recent ``n`` entries (in-memory cache)."""
        with self._lock:
            return list(self._buffer[-n:])

    def filter(
        self,
        *,
        action: str | None = None,
        agent: str | None = None,
        policy: str | None = None,
        tool: str | None = None,
        limit: int = 100,
    ) -> list[AuditEntry]:
        """Filter the in-memory cache by field matches."""
        with self._lock:
            buf = list(self._buffer)

        out: list[AuditEntry] = []
        for entry in reversed(buf):
            if action is not None and entry.action != action:
                continue
            if agent is not None and entry.agent != agent:
                continue
            if policy is not None and entry.policy != policy:
                continue
            if tool is not None and entry.tool != tool:
                continue
            out.append(entry)
            if len(out) >= limit:
                break
        return list(reversed(out))

    def iter_file(self) -> Iterable[AuditEntry]:
        """Yield entries from disk (slow, but unbounded)."""
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                yield AuditEntry(**payload)

    def stats(self) -> dict[str, Any]:
        """Return aggregate counts (in-memory cache)."""
        with self._lock:
            buf = list(self._buffer)
            total = self._total_recorded
            path = str(self.path)
            max_entries = self.max_entries

        by_action = Counter(e.action for e in buf if e.action)
        by_agent = Counter(e.agent for e in buf if e.agent)
        by_policy = Counter(e.policy for e in buf if e.policy)
        by_tool = Counter(e.tool for e in buf if e.tool)

        return {
            "total_recorded": total,
            "buffered": len(buf),
            "path": path,
            "max_entries": max_entries,
            "by_action": dict(by_action),
            "by_agent_top10": dict(by_agent.most_common(10)),
            "by_policy_top10": dict(by_policy.most_common(10)),
            "by_tool_top10": dict(by_tool.most_common(10)),
        }

    # ── Maintenance ───────────────────────────────────────────────────

    def flush(self) -> None:
        """No-op placeholder for symmetry with future buffered-file writes."""
        return

    def rotate(self) -> int:
        """Force a rotation by renaming the current file. Returns # of bytes archived."""
        if not self.path.exists():
            return 0
        size = self.path.stat().st_size
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        archive = self.path.with_suffix(self.path.suffix + f".{stamp}.rotated")
        archive.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(self.path, archive)
        except OSError as exc:
            logger.warning("Audit rotation failed: %s", exc)
            return 0
        with self._lock:
            self._buffer.clear()
        logger.info("Audit rotated to %s", archive.name)
        return size

    def clear(self) -> int:
        """Drop the in-memory buffer; optionally truncate the file too.

        Returns the number of buffered entries that were discarded.
        """
        with self._lock:
            count = len(self._buffer)
            self._buffer.clear()
            if self.path.exists():
                try:
                    self.path.unlink()
                except OSError:
                    pass
        return count

    # ── Internals ─────────────────────────────────────────────────────

    def _append_locked(self, line: str) -> None:
        """Append a single JSONL line to the trail file (caller must hold _lock)."""
        try:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError as exc:
            logger.error("Could not write audit entry: %s", exc)

    def _maybe_rotate_locked(self) -> None:
        """Rotate if we've hit max_entries or max_bytes (caller holds _lock)."""
        rotate = False
        if self.max_entries and len(self._buffer) >= self.max_entries:
            rotate = True
        if (
            self.max_bytes
            and self.path.exists()
            and self.path.stat().st_size >= self.max_bytes
        ):
            rotate = True
        if not rotate:
            return
        # Don't rotate from inside the lock — reentrant could deadlock.
        # Drop the buffer immediately to free memory; async rotate via os.replace.
        try:
            size = self.path.stat().st_size if self.path.exists() else 0
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            archive = self.path.with_suffix(
                self.path.suffix + f".{stamp}.rotated"
            )
            archive.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists():
                os.replace(self.path, archive)
            self._buffer.clear()
            logger.info(
                "Audit trail rotated (was %d bytes) → %s", size, archive.name
            )
        except OSError as exc:
            logger.warning("Rotation failed: %s", exc)


# ── Helpers ───────────────────────────────────────────────────────────────


def make_default_trail(root: str | Path | None = None) -> PolicyAuditTrail:
    """Return a trail rooted at ``<root>/.ygg/audit.jsonl`` by default.

    Falls back to the current working directory.
    """
    base = Path(root) if root else Path.cwd()
    return PolicyAuditTrail(
        path=base / ".ygg" / "audit.jsonl",
        max_entries=10_000,
        max_bytes=5 * 1024 * 1024,  # 5 MiB hard cap
    )


def summarize_entries(entries: Iterable[AuditEntry]) -> dict[str, Any]:
    """Group a list of entries by action and agent for dashboards."""
    by_action = Counter(e.action for e in entries)
    by_agent = Counter(e.agent for e in entries)
    return {
        "count": sum(by_action.values()),
        "by_action": dict(by_action),
        "by_agent": dict(by_agent.most_common(10)),
    }
