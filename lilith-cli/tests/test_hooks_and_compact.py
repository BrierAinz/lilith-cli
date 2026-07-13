"""Tests for hooks system and compact_history."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest


class TestHooksSystem:
    """Verify hooks module basic operations."""

    def test_hooks_dir_exists_after_ensure(self) -> None:
        from lilith_cli.hooks import hooks_dir, list_hooks

        hdir = hooks_dir()
        assert hdir.exists()
        # list_hooks should return a dict (possibly empty)
        installed = list_hooks()
        assert isinstance(installed, dict)


class TestCompactHistory:
    """Verify compact_history in agent module."""

    def test_compact_history_basic(self) -> None:
        """Verify compact_history reduces message count."""
        from lilith_cli.agent import AgentSession, compact_history

        async def run():
            sess = AgentSession.from_config_path()
            sess.history = [{"role": "user", "content": f"msg {i}"} for i in range(20)]
            removed = await compact_history(sess, ratio=0.5)
            assert removed > 0
            assert len(sess.history) < 20

        asyncio.run(run())

    def test_compact_history_too_short(self) -> None:
        """With < 6 messages, no compaction happens."""
        from lilith_cli.agent import AgentSession, compact_history

        async def run():
            sess = AgentSession.from_config_path()
            sess.history = [{"role": "user", "content": f"msg {i}"} for i in range(3)]
            removed = await compact_history(sess)
            assert removed == 0
            assert len(sess.history) == 3

        asyncio.run(run())
