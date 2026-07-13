"""User preference store — quick access to user preferences."""

from __future__ import annotations

import asyncio
import sqlite3
import time
import uuid
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from pathlib import Path


VALID_PREFERENCE_TYPES = {"explicit", "inferred"}


class PreferenceStore:
    """Stores user preferences separately for quick access.

    Preferences are stored in a dedicated SQLite table
    (``user_preferences``) and support both explicit preferences
    (directly stated by the user) and inferred preferences (detected
    from behaviour patterns).

    Convenience accessors are provided for common preference categories
    such as communication style, language, and design preferences.
    """

    def __init__(self, db_path: Path) -> None:
        """Initialise the preference store.

        Args:
            db_path: Path to the SQLite database file.

        """
        self._db_path = db_path
        self._init_db()

    # ------------------------------------------------------------------
    # Database setup
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        """Create the user_preferences table and indexes."""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_preferences (
                    id TEXT PRIMARY KEY,
                    key TEXT NOT NULL UNIQUE,
                    value TEXT NOT NULL,
                    preference_type TEXT NOT NULL DEFAULT 'explicit',
                    confidence REAL NOT NULL DEFAULT 0.7,
                    source TEXT,
                    timestamp REAL NOT NULL
                )
                """,
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_prefs_key ON user_preferences(key)",
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_prefs_type ON user_preferences(preference_type)",
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        """Convert a database row to a dict."""
        return dict(row)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def set(
        self,
        key: str,
        value: str,
        preference_type: str = "explicit",
        source: str | None = None,
        confidence: float = 0.7,
    ) -> str:
        """Set (or update) a user preference.

        If a preference with the same *key* already exists, it is
        updated (upsert behaviour).

        Args:
            key: The preference key (e.g. "communication_style").
            value: The preference value.
            preference_type: Either ``'explicit'`` or ``'inferred'``.
            source: Optional source description.
            confidence: Initial confidence between 0.0 and 1.0.

        Returns:
            The unique identifier of the preference entry.

        Raises:
            ValueError: If *preference_type* is not valid.

        """
        if preference_type not in VALID_PREFERENCE_TYPES:
            raise ValueError(
                f"Invalid preference_type '{preference_type}'. "
                f"Must be one of: {', '.join(sorted(VALID_PREFERENCE_TYPES))}",
            )

        item_id = str(uuid.uuid4())
        now = time.time()

        def _upsert() -> str:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                # Check if key exists
                existing = conn.execute(
                    "SELECT id FROM user_preferences WHERE key = ?",
                    (key,),
                ).fetchone()
                if existing:
                    # Update existing
                    conn.execute(
                        """
                        UPDATE user_preferences
                        SET value = ?, preference_type = ?, confidence = ?,
                            source = ?, timestamp = ?
                        WHERE key = ?
                        """,
                        (value, preference_type, confidence, source, now, key),
                    )
                    item_id_ret = existing[0]
                else:
                    # Insert new
                    conn.execute(
                        """
                        INSERT INTO user_preferences
                            (id, key, value, preference_type, confidence, source, timestamp)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (item_id, key, value, preference_type, confidence, source, now),
                    )
                    item_id_ret = item_id
                conn.commit()
                return item_id_ret

        return await asyncio.to_thread(_upsert)

    async def get(self, key: str) -> dict[str, Any] | None:
        """Retrieve a preference by key.

        Args:
            key: The preference key.

        Returns:
            A dict representing the preference, or ``None`` if not found.

        """

        def _get() -> dict[str, Any] | None:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                row = conn.execute(
                    "SELECT * FROM user_preferences WHERE key = ?",
                    (key,),
                ).fetchone()
                return self._row_to_dict(row) if row else None

        return await asyncio.to_thread(_get)

    async def get_all(self) -> list[dict[str, Any]]:
        """Return all stored preferences.

        Returns:
            A list of dicts representing all preferences.

        """

        def _get_all() -> list[dict[str, Any]]:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                rows = conn.execute(
                    "SELECT * FROM user_preferences ORDER BY timestamp DESC",
                ).fetchall()
                return [self._row_to_dict(row) for row in rows]

        return await asyncio.to_thread(_get_all)

    async def delete(self, key: str) -> bool:
        """Delete a preference by key.

        Args:
            key: The preference key to delete.

        Returns:
            ``True`` if a preference was deleted, ``False`` otherwise.

        """

        def _delete() -> bool:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                cursor = conn.execute(
                    "DELETE FROM user_preferences WHERE key = ?",
                    (key,),
                )
                conn.commit()
                return cursor.rowcount > 0

        return await asyncio.to_thread(_delete)

    async def increase_confidence(self, key: str, delta: float = 0.1) -> bool:
        """Increase the confidence score of a preference.

        Args:
            key: The preference key.
            delta: Amount to add to the current confidence.
                   Result is clamped to [0.0, 1.0].

        Returns:
            ``True`` if the preference was found and updated.

        """

        def _increase() -> bool:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                row = conn.execute(
                    "SELECT confidence FROM user_preferences WHERE key = ?",
                    (key,),
                ).fetchone()
                if row is None:
                    return False
                new_conf = max(0.0, min(1.0, row[0] + delta))
                conn.execute(
                    "UPDATE user_preferences SET confidence = ? WHERE key = ?",
                    (new_conf, key),
                )
                conn.commit()
                return True

        return await asyncio.to_thread(_increase)

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    async def get_communication_style(self) -> str | None:
        """Return the user's preferred communication style, if set."""
        pref = await self.get("communication_style")
        return pref["value"] if pref else None

    async def get_preferred_language(self) -> str | None:
        """Return the user's preferred language, if set."""
        pref = await self.get("preferred_language")
        return pref["value"] if pref else None

    async def get_design_preferences(self) -> dict[str, Any]:
        """Return all preferences whose key starts with ``design_``.

        Returns:
            A dict mapping preference keys to their values.

        """

        def _get_design() -> dict[str, Any]:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                rows = conn.execute(
                    "SELECT * FROM user_preferences WHERE key LIKE 'design_%' ESCAPE '\\'",
                ).fetchall()
                return {row["key"]: self._row_to_dict(row) for row in rows}

        return await asyncio.to_thread(_get_design)
