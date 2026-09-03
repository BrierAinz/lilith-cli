"""Persistent provider health and circuit-breaker state.

The retry layer answers whether a *single* request is worth retrying.  This
module answers the wider operational question: whether a provider that has
failed repeatedly should receive another request at all.  State is kept in a
small WAL-mode SQLite database so separate Lilith processes share the same
view without racing over a JSON file.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


HEALTH_DB_ENV = "YGGDRASIL_PROVIDER_HEALTH_DB"


class ProviderCircuitOpenError(RuntimeError):
    """Raised when a provider circuit is open and calls are suppressed."""


def default_health_path() -> Path:
    override = os.environ.get(HEALTH_DB_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".yggdrasil" / "provider_health.sqlite3"


class ProviderHealthRegistry:
    """Process-safe health registry with a conventional circuit breaker."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path).expanduser() if path else default_health_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS provider_health (
                    provider TEXT PRIMARY KEY,
                    state TEXT NOT NULL DEFAULT 'closed',
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    successes INTEGER NOT NULL DEFAULT 0,
                    failures INTEGER NOT NULL DEFAULT 0,
                    opened_until REAL NOT NULL DEFAULT 0,
                    last_error TEXT,
                    last_latency_ms INTEGER,
                    updated_at REAL NOT NULL
                )
                """
            )

    @staticmethod
    def _clean_name(provider: str) -> str:
        name = str(provider or "unknown").strip().lower()
        return name or "unknown"

    @staticmethod
    def _row(row: sqlite3.Row | None, provider: str) -> dict[str, Any]:
        if row is None:
            return {
                "provider": provider,
                "state": "closed",
                "consecutive_failures": 0,
                "successes": 0,
                "failures": 0,
                "opened_until": 0.0,
                "last_error": None,
                "last_latency_ms": None,
                "updated_at": 0.0,
            }
        return dict(row)

    def get(self, provider: str) -> dict[str, Any]:
        name = self._clean_name(provider)
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM provider_health WHERE provider = ?", (name,)
            ).fetchone()
        result = self._row(row, name)
        if result["state"] == "open" and float(result["opened_until"]) <= time.time():
            result["state"] = "half_open"
        return result

    def allow(self, provider: str) -> bool:
        """Atomically allow normal traffic or claim one half-open probe."""
        name = self._clean_name(provider)
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT state, opened_until FROM provider_health WHERE provider = ?",
                (name,),
            ).fetchone()
            if row is None or row["state"] == "closed":
                conn.commit()
                return True
            opened_until = float(row["opened_until"])
            if opened_until > now:
                conn.rollback()
                return False
            # The cooldown expired. Move the shared row to half-open and lease
            # exactly one probe; other processes are rejected until it reports
            # success/failure or the probe lease itself expires.
            conn.execute(
                "UPDATE provider_health SET state='half_open', opened_until=?, "
                "updated_at=? WHERE provider=?",
                (now + 30.0, now, name),
            )
            conn.commit()
            return True

    def record_success(self, provider: str, latency_ms: int | None = None) -> dict[str, Any]:
        name = self._clean_name(provider)
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO provider_health (
                    provider, state, consecutive_failures, successes, failures,
                    opened_until, last_error, last_latency_ms, updated_at
                ) VALUES (?, 'closed', 0, 1, 0, 0, NULL, ?, ?)
                ON CONFLICT(provider) DO UPDATE SET
                    state = 'closed',
                    consecutive_failures = 0,
                    successes = successes + 1,
                    opened_until = 0,
                    last_error = NULL,
                    last_latency_ms = excluded.last_latency_ms,
                    updated_at = excluded.updated_at
                """,
                (name, latency_ms, now),
            )
            conn.commit()
        return self.get(name)

    def record_failure(
        self,
        provider: str,
        error: BaseException | str,
        *,
        threshold: int = 2,
        cooldown_seconds: float = 60.0,
        permanent: bool = False,
    ) -> dict[str, Any]:
        name = self._clean_name(provider)
        now = time.time()
        threshold = max(1, int(threshold))
        cooldown = max(1.0, float(cooldown_seconds))
        message = str(error).replace("\n", " ")[:512]
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT consecutive_failures FROM provider_health WHERE provider = ?",
                (name,),
            ).fetchone()
            count = int(row[0]) + 1 if row else 1
            should_open = permanent or count >= threshold
            state = "open" if should_open else "closed"
            opened_until = now + cooldown if should_open else 0.0
            conn.execute(
                """
                INSERT INTO provider_health (
                    provider, state, consecutive_failures, successes, failures,
                    opened_until, last_error, last_latency_ms, updated_at
                ) VALUES (?, ?, ?, 0, 1, ?, ?, NULL, ?)
                ON CONFLICT(provider) DO UPDATE SET
                    state = excluded.state,
                    consecutive_failures = excluded.consecutive_failures,
                    failures = failures + 1,
                    opened_until = excluded.opened_until,
                    last_error = excluded.last_error,
                    updated_at = excluded.updated_at
                """,
                (name, state, count, opened_until, message, now),
            )
            conn.commit()
        return self.get(name)

    def reset(self, provider: str | None = None) -> None:
        with self._lock, self._connect() as conn:
            if provider:
                conn.execute(
                    "DELETE FROM provider_health WHERE provider = ?",
                    (self._clean_name(provider),),
                )
            else:
                conn.execute("DELETE FROM provider_health")

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM provider_health ORDER BY provider"
            ).fetchall()
        return [self.get(str(row["provider"])) for row in rows]


__all__ = [
    "HEALTH_DB_ENV",
    "ProviderCircuitOpenError",
    "ProviderHealthRegistry",
    "default_health_path",
]
