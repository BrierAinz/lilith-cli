"""Tests for the Yggdrasil orchestration panel (plan-29 item 7)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from lilith_cli.ide import LilithIDEApp, YggdrasilPanelScreen
from lilith_cli.ide.screens.modals import YggdrasilPanelScreen as ModalYggdrasilPanelScreen


@pytest.fixture
def mock_presets(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict]:
    """Provide deterministic Hlidskjalf presets."""
    presets = {
        "ejecutor-kimi": {"provider": "kimi", "model": "kimi-k2"},
        "investigador-minimax": {"provider": "minimax", "model": "MiniMax-M3"},
    }
    monkeypatch.setattr(
        "lilith_cli.main._load_subagent_presets",
        lambda _config_path=None: presets,
    )
    return presets


@pytest.fixture
def mock_queue_ops(monkeypatch: pytest.MonkeyPatch):
    """Mock queue add/cancel so the panel never touches the real bus."""
    added: list[dict] = []
    cancelled: list[int] = []

    def fake_add(task: str, **kwargs) -> int:
        added.append({"task": task, **kwargs})
        return 0

    def fake_cancel(msg_id: int, **kwargs) -> int:
        cancelled.append(msg_id)
        return 0

    monkeypatch.setattr("lilith_cli.ops_queue.run_queue_add", fake_add)
    monkeypatch.setattr("lilith_cli.ops_queue.run_queue_cancel", fake_cancel)
    return {"added": added, "cancelled": cancelled}


@pytest.fixture
def mock_spawn_ops(monkeypatch: pytest.MonkeyPatch):
    """Mock spawn status/kill so the panel never touches the real bus."""
    killed: list[str] = []

    def fake_status(*, db=None, repo_root=None) -> list[dict]:
        return [
            {
                "goal_id": "g1",
                "agent": "hela",
                "channel": "fake",
                "model": "test-model",
                "task": "active task",
                "started_at": 1.0,
                "topic": "spawn.hela",
            }
        ]

    def fake_kill(agent_name: str, **kwargs) -> int:
        killed.append(agent_name)
        return 0

    monkeypatch.setattr("lilith_cli.ops_spawn.run_spawn_status", fake_status)
    monkeypatch.setattr("lilith_cli.ops_spawn.run_spawn_kill", fake_kill)
    return {"killed": killed}


class TestYggdrasilPanelScreen:
    """Smoke tests for the orchestration modal itself."""

    def test_screen_constructible(self):
        screen = YggdrasilPanelScreen()
        assert screen is not None

    async def test_panel_opens_and_shows_items(
        self,
        fake_session,
        tmp_path,
        mock_presets,
        mock_queue_ops,
        mock_spawn_ops,
    ):
        app = LilithIDEApp(fake_session, root=tmp_path, show_splash=False)
        async with app.run_test(size=(120, 40)) as pilot:
            app.action_open_yggdrasil_panel()
            await pilot.pause()
            screen = app.screen_stack[-1]
            assert isinstance(screen, ModalYggdrasilPanelScreen)
            # The screen should have loaded mocked queue / spawn data.
            assert len(screen._presets) == 2
            assert len(screen._spawn_items) == 1


class TestYggdrasilPanelMixin:
    """Tests for delegation and orchestration actions."""

    async def test_delegate_to_preset_uses_last_user_message(
        self,
        fake_session,
        tmp_path,
        mock_presets,
        mock_queue_ops,
    ):
        fake_session.history.append({"role": "user", "content": "refactor this loop"})
        app = LilithIDEApp(fake_session, root=tmp_path, show_splash=False)
        async with app.run_test(size=(120, 40)) as pilot:
            app._delegate_to_preset("ejecutor-kimi")
            await pilot.pause()

            assert len(mock_queue_ops["added"]) == 1
            call = mock_queue_ops["added"][0]
            assert call["task"] == "refactor this loop"
            assert call["agent"] == "ejecutor-kimi"
            assert call["queued_by"] == "yggdrasil-panel"

    async def test_delegate_slash_command(
        self,
        fake_session,
        tmp_path,
        mock_presets,
        mock_queue_ops,
    ):
        fake_session.history.append({"role": "user", "content": "write tests"})
        app = LilithIDEApp(fake_session, root=tmp_path, show_splash=False)
        async with app.run_test(size=(120, 40)) as pilot:
            app._handle_slash("/delegate investigador-minimax")
            await pilot.pause()

            assert len(mock_queue_ops["added"]) == 1
            call = mock_queue_ops["added"][0]
            assert call["task"] == "write tests"
            assert call["agent"] == "investigador-minimax"

    async def test_delegate_without_preset_name_shows_usage(
        self,
        fake_session,
        tmp_path,
    ):
        app = LilithIDEApp(fake_session, root=tmp_path, show_splash=False)
        async with app.run_test(size=(120, 40)) as pilot:
            app._handle_slash("/delegate")
            await pilot.pause()
            # No task should have been enqueued.

    async def test_unknown_delegate_preset_notifies_error(
        self,
        fake_session,
        tmp_path,
        mock_presets,
        mock_queue_ops,
    ):
        fake_session.history.append({"role": "user", "content": "do something"})
        app = LilithIDEApp(fake_session, root=tmp_path, show_splash=False)
        async with app.run_test(size=(120, 40)) as pilot:
            app._delegate_to_preset("nonexistent")
            await pilot.pause()
            assert len(mock_queue_ops["added"]) == 0


class TestYggdrasilPanelActions:
    """Tests for in-panel keyboard actions."""

    async def test_refresh_reloads_data(
        self,
        fake_session,
        tmp_path,
        mock_presets,
        mock_queue_ops,
        mock_spawn_ops,
    ):
        app = LilithIDEApp(fake_session, root=tmp_path, show_splash=False)
        async with app.run_test(size=(120, 40)) as pilot:
            app.action_open_yggdrasil_panel()
            await pilot.pause()
            screen = app.screen_stack[-1]
            screen.action_refresh()
            await pilot.pause()
            assert len(screen._spawn_items) == 1

    async def test_delegate_hotkey_enqueues_task(
        self,
        fake_session,
        tmp_path,
        mock_presets,
        mock_queue_ops,
    ):
        fake_session.history.append({"role": "user", "content": "hotkey task"})
        app = LilithIDEApp(fake_session, root=tmp_path, show_splash=False)
        async with app.run_test(size=(120, 40)) as pilot:
            app.action_open_yggdrasil_panel()
            await pilot.pause()
            screen = app.screen_stack[-1]
            screen.action_delegate_1()
            await pilot.pause()

            assert len(mock_queue_ops["added"]) == 1
            assert mock_queue_ops["added"][0]["agent"] == "ejecutor-kimi"


class TestRunSpawnStatus:
    """Tests for the spawn status helper used by the panel."""

    def test_active_spawns_pair_start_and_done(self, tmp_path, monkeypatch):
        from lilith_core.bus import LilithBus
        from lilith_cli import ops_spawn

        fake_root = tmp_path / "Yggdrasil"
        fake_root.mkdir()
        bus_db = fake_root / ".ygg" / "lilith_bus.db"
        bus = LilithBus(bus_db)
        try:
            bus.publish(
                "spawn.hela",
                {
                    "goal_id": "g1",
                    "channel": "fake",
                    "model": "test",
                    "task": "active",
                    "started_at": 1.0,
                },
            )
            bus.publish(
                "spawn.mimir",
                {
                    "goal_id": "g2",
                    "channel": "fake",
                    "model": "test",
                    "task": "done",
                    "started_at": 2.0,
                },
            )
            bus.publish(
                "spawn.mimir.done",
                {"goal_id": "g2", "exit_code": 0},
            )
        finally:
            bus.close()

        active = ops_spawn.run_spawn_status(db=bus_db, repo_root=fake_root)
        agents = {s["agent"] for s in active}
        assert agents == {"hela"}
        assert all("task" in s for s in active)


class TestRunQueueCancel:
    """Tests for the queue cancel helper used by the panel."""

    def test_cancel_free_message(self, tmp_path):
        from lilith_core.bus import LilithBus
        from lilith_cli import ops_queue

        fake_root = tmp_path / "Yggdrasil"
        fake_root.mkdir()
        bus_db = fake_root / ".ygg" / "lilith_bus.db"
        bus = LilithBus(bus_db)
        try:
            bus.publish("queue.task", {"task": "to cancel"}, role="worker")
        finally:
            bus.close()

        code = ops_queue.run_queue_cancel(1, db=bus_db, repo_root=fake_root)
        assert code == 0

        bus = LilithBus(bus_db)
        try:
            rows = bus.poll("queue.**", limit=10)
        finally:
            bus.close()
        assert len(rows) == 1
        assert rows[0].claimed_by == "yggdrasil-panel"
        assert rows[0].topic == "queue.task"

    def test_cancel_already_claimed_fails(self, tmp_path):
        from lilith_core.bus import LilithBus
        from lilith_cli import ops_queue

        fake_root = tmp_path / "Yggdrasil"
        fake_root.mkdir()
        bus_db = fake_root / ".ygg" / "lilith_bus.db"
        bus = LilithBus(bus_db)
        try:
            bus.publish("queue.task", {"task": "claimed"}, role="worker")
            bus.claim_any("worker", "skadi")
        finally:
            bus.close()

        code = ops_queue.run_queue_cancel(1, db=bus_db, repo_root=fake_root)
        assert code == 1
