"""SQLite-backed tool usage analytics with async-compatible operations."""

from __future__ import annotations

import asyncio
import sqlite3
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel


if TYPE_CHECKING:
    from pathlib import Path


class ToolUsage(BaseModel):
    """A single tool execution record."""

    tool_name: str
    timestamp: float
    success: bool
    duration_ms: float
    error: str = ""


class ToolStats(BaseModel):
    """Aggregated statistics for a tool."""

    tool_name: str
    total_calls: int
    success_rate: float
    avg_duration_ms: float
    last_used: float
    error_count: int


class ToolAnalytics:
    """Record and query tool usage statistics backed by SQLite.

    Pass ``db_path=None`` for an in-memory database (useful for tests).
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path: str | None = str(db_path) if db_path else None
        self._in_memory = db_path is None
        # For in-memory databases, keep a single persistent connection
        self._conn: sqlite3.Connection | None = None
        self._init_done = False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_init(self) -> None:
        """Create the table on first use."""
        if self._init_done:
            return
        self._init_done = True
        conn = self._get_conn()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tool_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tool_name TEXT NOT NULL,
                timestamp REAL NOT NULL,
                success INTEGER NOT NULL,
                duration_ms REAL NOT NULL,
                error TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.commit()
        # For file-backed databases, close the conn after init;
        # for in-memory ones, keep it alive.
        if not self._in_memory:
            conn.close()

    def _get_conn(self) -> sqlite3.Connection:
        """Get a database connection.

        For in-memory databases, returns a single persistent connection
        (with check_same_thread=False for async compatibility).
        For file-backed databases, returns a new connection each time.
        """
        if self._in_memory:
            if self._conn is None:
                self._conn = sqlite3.connect(":memory:", check_same_thread=False)
            return self._conn
        assert self._db_path is not None  # guaranteed when not in-memory
        return sqlite3.connect(self._db_path)

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    async def record_async(self, usage: ToolUsage) -> None:
        """Async-insert a usage record (delegates to thread pool)."""
        await asyncio.to_thread(self.record, usage)

    def record(self, usage: ToolUsage) -> None:
        """Insert a usage record synchronously."""
        self._ensure_init()
        conn = self._get_conn()
        try:
            conn.execute(
                "INSERT INTO tool_usage (tool_name, timestamp, success, duration_ms, error) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    usage.tool_name,
                    usage.timestamp,
                    int(usage.success),
                    usage.duration_ms,
                    usage.error,
                ),
            )
            conn.commit()
        finally:
            if not self._in_memory:
                conn.close()

    # ------------------------------------------------------------------
    # Query operations
    # ------------------------------------------------------------------

    async def get_stats_async(self, tool_name: str | None = None) -> list[ToolStats]:
        """Async variant of :meth:`get_stats`."""
        return await asyncio.to_thread(self.get_stats, tool_name)

    def get_stats(self, tool_name: str | None = None) -> list[ToolStats]:
        """Return aggregated statistics, optionally filtered by *tool_name*."""
        self._ensure_init()
        conn = self._get_conn()
        try:
            if tool_name:
                rows = conn.execute(
                    "SELECT tool_name, COUNT(*) as total_calls, "
                    "SUM(CASE WHEN success=1 THEN 1 ELSE 0 END) as successes, "
                    "AVG(duration_ms) as avg_duration, "
                    "MAX(timestamp) as last_used, "
                    "SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) as error_count "
                    "FROM tool_usage WHERE tool_name=? GROUP BY tool_name",
                    (tool_name,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT tool_name, COUNT(*) as total_calls, "
                    "SUM(CASE WHEN success=1 THEN 1 ELSE 0 END) as successes, "
                    "AVG(duration_ms) as avg_duration, "
                    "MAX(timestamp) as last_used, "
                    "SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) as error_count "
                    "FROM tool_usage GROUP BY tool_name"
                ).fetchall()
        finally:
            if not self._in_memory:
                conn.close()

        stats: list[ToolStats] = []
        for row in rows:
            name, total, successes, avg_dur, last, err_count = row
            success_rate = successes / total if total else 0.0
            stats.append(
                ToolStats(
                    tool_name=name,
                    total_calls=total,
                    success_rate=round(success_rate, 4),
                    avg_duration_ms=round(avg_dur, 2),
                    last_used=last,
                    error_count=err_count,
                )
            )
        return stats

    async def get_slow_tools_async(self, threshold_ms: float = 5000) -> list[ToolStats]:
        """Async variant of :meth:`get_slow_tools`."""
        return await asyncio.to_thread(self.get_slow_tools, threshold_ms)

    def get_slow_tools(self, threshold_ms: float = 5000) -> list[ToolStats]:
        """Return tools whose average duration exceeds *threshold_ms*."""
        all_stats = self.get_stats()
        return [s for s in all_stats if s.avg_duration_ms > threshold_ms]

    async def get_unreliable_tools_async(self, threshold: float = 0.8) -> list[ToolStats]:
        """Async variant of :meth:`get_unreliable_tools`."""
        return await asyncio.to_thread(self.get_unreliable_tools, threshold)

    def get_unreliable_tools(self, threshold: float = 0.8) -> list[ToolStats]:
        """Return tools whose success rate is below *threshold*."""
        all_stats = self.get_stats()
        return [s for s in all_stats if s.success_rate < threshold]

    async def get_recommendations_async(self) -> list[dict[str, Any]]:
        """Async variant of :meth:`get_recommendations`."""
        return await asyncio.to_thread(self.get_recommendations)

    def get_recommendations(self) -> list[dict[str, Any]]:
        """Suggest optimisations based on recorded usage data."""
        all_stats = self.get_stats()
        recs: list[dict[str, Any]] = []

        for stat in all_stats:
            suggestions: list[str] = []
            if stat.success_rate < 0.8:
                suggestions.append(
                    f"Low success rate ({stat.success_rate:.0%}) — consider adding fallbacks"
                )
            if stat.avg_duration_ms > 5000:
                suggestions.append(
                    f"Slow ({stat.avg_duration_ms:.0f} ms avg) "
                    "— consider caching or async execution"
                )
            if stat.error_count > 5:
                suggestions.append(f"High error count ({stat.error_count}) — review error patterns")
            if suggestions:
                recs.append(
                    {"tool": stat.tool_name, "suggestions": suggestions, "stats": stat.model_dump()}
                )

        return recs
