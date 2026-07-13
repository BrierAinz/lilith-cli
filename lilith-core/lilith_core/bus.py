"""lilith_core.bus — SQLite-backed pub/sub with role anycast claim.

v1 scope (plan-28 fase 6):
    - publish / poll / claim_any / ack / release
    - dot-hierarchy topic matching: '*' = one segment, '**' = recursive
    - internal threading.Lock + SQLite WAL mode (one writer, many readers)

Deferred to v2 (out of scope here):
    - subscribe_pattern with background poller / handler dispatch
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple, Optional


__all__ = ["BusError", "BusMessage", "LilithBus"]


class BusError(Exception):
    """Storage-layer failure (corrupt payload, invalid input, etc.)."""


class BusMessage(NamedTuple):
    id: int
    topic: str
    payload: dict
    role: Optional[str]
    published_at: str
    claimed_by: Optional[str]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic    TEXT NOT NULL,
    payload  TEXT NOT NULL,
    role             TEXT,
    published_at     TEXT NOT NULL,
    claimed_by       TEXT,
    claimed_at       TEXT,
    delivered_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_role_claimed
    ON messages(role, claimed_by);
CREATE INDEX IF NOT EXISTS idx_messages_topic
    ON messages(topic);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _match(topic: str, pattern: str) -> bool:
    """Match ``topic`` against a dot-hierarchy ``pattern``.

    - ``*``  matches exactly one segment.
    - ``**`` matches zero or more segments (recursive).
    - Other segments must be equal.
    """
    ts = topic.split(".")
    ps = pattern.split(".")

    def walk(i: int, j: int) -> bool:
        if j == len(ps):
            return i == len(ts)
        if ps[j] == "**":
            jj = j
            while jj < len(ps) and ps[jj] == "**":
                jj += 1
            # ''  ... 'tail' — split anywhere from i..len(ts)
            for k in range(i, len(ts) + 1):
                if walk(k, jj):
                    return True
            return False
        if i == len(ts):
            return False
        if ps[j] == "*" or ps[j] == ts[i]:
            return walk(i + 1, j + 1)
        return False

    return walk(0, 0)


class LilithBus:
    """Topic-keyed publish/subscribe bus with role anycast claim.

    Semantics
    ---------
    - ``publish``        appends a row, returns the (monotonic) id.
    - ``poll``           fan-out read; non-destructive, idempotent.
                         Multiple readers see the same message until claim/ack.
    - ``claim_any``      atomic anycast among workers sharing the same ``role``.
                         The selected row gets ``claimed_by`` set; one shot only.
    - ``ack``            finalizes delivery: sets ``delivered_at``. Idempotent
                         for the same claimer; refuses other claimers.
    - ``release``        returns a claim to the unclaimed pool (pre-ack).
                         Refuses to release after ack.

    Concurrency
    -----------
    A single ``LilithBus`` instance is safe to share across threads. All
    mutating calls serialize through ``_lock`` AND through SQLite's per-
    connection writer lock. WAL mode lets pollers read concurrently with a
    writer.
    """

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            str(self.db_path),
            isolation_level=None,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript("PRAGMA journal_mode=WAL;")
            self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.ProgrammingError:
                pass

    def __enter__(self) -> "LilithBus":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---------------- publish ----------------

    def publish(
        self, topic: str, payload: dict, *, role: Optional[str] = None
    ) -> int:
        if not isinstance(topic, str) or not topic:
            raise BusError("topic must be a non-empty string")
        if not isinstance(payload, dict):
            raise BusError("payload must be a dict")
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        ts = _now()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO messages (topic, payload, role, published_at) "
                "VALUES (?, ?, ?, ?)",
                (topic, body, role, ts),
            )
            return int(cur.lastrowid)

    # ---------------- poll (fan-out, non-destructive) ----------------

    def poll(
        self, pattern: str, *, limit: int = 50, since_id: int = 0
    ) -> list[BusMessage]:
        if limit <= 0:
            return []
        # Bound the candidate set; topic cardinality is small so a simple
        # windowed scan + Python-side pattern match is fine for v1.
        window = max(limit * 4, 200)
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, topic, payload, role, published_at, claimed_by "
                "FROM messages WHERE id > ? ORDER BY id ASC LIMIT ?",
                (since_id, window),
            ).fetchall()
        out: list[BusMessage] = []
        for r in rows:
            if not _match(r["topic"], pattern):
                continue
            try:
                payload = json.loads(r["payload"])
            except json.JSONDecodeError as exc:
                raise BusError(f"corrupt payload at id={r['id']}") from exc
            out.append(
                BusMessage(
                    id=r["id"],
                    topic=r["topic"],
                    payload=payload,
                    role=r["role"],
                    published_at=r["published_at"],
                    claimed_by=r["claimed_by"],
                )
            )
            if len(out) >= limit:
                break
        return out

    # ---------------- claim_any (anycast, atomic) ----------------

    def claim_any(self, role: str, claimer: str) -> Optional[BusMessage]:
        if not role or not claimer:
            raise BusError("role and claimer are required")
        ts = _now()
        with self._lock:
            row = self._conn.execute(
                """
                UPDATE messages
                   SET claimed_by = ?, claimed_at = ?
                 WHERE id = (
                       SELECT id FROM messages
                        WHERE claimed_by IS NULL AND role = ?
                        ORDER BY id ASC LIMIT 1
                 )
                   AND claimed_by IS NULL
                RETURNING id, topic, payload, role, published_at,
                          claimed_by, claimed_at
                """,
                (claimer, ts, role),
            ).fetchone()
        if row is None:
            return None
        return BusMessage(
            id=row["id"],
            topic=row["topic"],
            payload=json.loads(row["payload"]),
            role=row["role"],
            published_at=row["published_at"],
            claimed_by=row["claimed_by"],
        )

    def claim_by_id(self, msg_id: int, claimer: str) -> Optional[BusMessage]:
        """Atomically claim a specific message by id.

        Returns the :class:`BusMessage` on success, or ``None`` when the
        message is already claimed, delivered, or does not exist.
        """
        if not claimer:
            raise BusError("claimer is required")
        ts = _now()
        with self._lock:
            row = self._conn.execute(
                """
                UPDATE messages
                   SET claimed_by = ?, claimed_at = ?
                 WHERE id = ?
                   AND claimed_by IS NULL
                   AND delivered_at IS NULL
                RETURNING id, topic, payload, role, published_at,
                          claimed_by, claimed_at
                """,
                (claimer, ts, msg_id),
            ).fetchone()
        if row is None:
            return None
        return BusMessage(
            id=row["id"],
            topic=row["topic"],
            payload=json.loads(row["payload"]),
            role=row["role"],
            published_at=row["published_at"],
            claimed_by=row["claimed_by"],
        )

    # ---------------- ack ----------------

    def ack(self, msg_id: int, claimer: str) -> bool:
        ts = _now()
        with self._lock:
            cur = self._conn.execute(
                """
                UPDATE messages
                   SET delivered_at = ?
                 WHERE id = ? AND claimed_by = ?
                   AND delivered_at IS NULL
                """,
                (ts, msg_id, claimer),
            )
        return cur.rowcount > 0

    # ---------------- release ----------------

    def release(self, msg_id: int, claimer: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                """
                UPDATE messages
                   SET claimed_by = NULL, claimed_at = NULL
                 WHERE id = ? AND claimed_by = ?
                   AND delivered_at IS NULL
                """,
                (msg_id, claimer),
            )
        return cur.rowcount > 0
