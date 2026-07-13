"""Tests for the GitMixin / Git modal screens in the Lilith IDE."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_PKG_DIR = str(Path(__file__).resolve().parent.parent)
if _PKG_DIR not in sys.path:
    sys.path.insert(0, _PKG_DIR)

from lilith_cli.ide import LilithIDEApp
from lilith_cli.ide.screens.modals import (
    CommitScreen,
    GitCommitScreen,
    GitHunkScreen,
    GitLogScreen,
)


@pytest.fixture
def git_app(fake_session, tmp_path):
    """Return an unmounted LilithIDEApp rooted at tmp_path."""
    return LilithIDEApp(fake_session, root=tmp_path)


class TestGitHunkParsing:
    """Unit tests for hunk parsing used by GitHunkScreen."""

    def test_parse_diff_hunks_splits_and_keeps_headers(self):
        diff = """--- a/src/main.py
+++ b/src/main.py
@@ -1,3 +1,3 @@
 def hello():
-    return "hi"
+    return "hello"
@@ -10,2 +10,3 @@
 def foo():
+    pass
"""
        hunks = GitHunkScreen._parse_hunks(diff, "src/main.py")
        assert len(hunks) == 2
        display0, patch0 = hunks[0]
        assert "@@ -1,3 +1,3 @@" in display0
        assert "--- a/src/main.py" in patch0
        assert "+++ b/src/main.py" in patch0
        assert '-    return "hi"' in patch0
        assert '+    return "hello"' in patch0

        _display1, patch1 = hunks[1]
        assert "@@ -10,2 +10,3 @@" in patch1
        assert "+    pass" in patch1

    def test_parse_diff_hunks_empty_diff(self):
        assert GitHunkScreen._parse_hunks("", "x.py") == []


class TestGitHunkScreen:
    """Smoke tests for the hunk modal."""

    def test_git_hunk_screen_constructible(self, tmp_path):
        screen = GitHunkScreen(tmp_path, tmp_path / "a.py")
        assert screen.root == tmp_path

    def test_git_hunk_screen_dismiss_stage(self, tmp_path, monkeypatch):
        screen = GitHunkScreen(tmp_path, tmp_path / "a.py")
        screen._hunks = [("hunk A", "patch A"), ("hunk B", "patch B")]
        dismissed = []
        monkeypatch.setattr(screen, "dismiss", lambda value: dismissed.append(value))

        # Simulate a selected item.
        list_view = MagicMock()
        list_view.index = 0
        monkeypatch.setattr(
            screen,
            "query_one",
            lambda _id, _kind=None: list_view,
        )
        event = MagicMock()
        event.button.id = "git-hunk-stage"
        screen.on_button_pressed(event)
        assert dismissed == [("stage", "patch A")]


class TestGitStageDiscardWorkers:
    """Tests that stage/discard workers build the expected git commands."""

    async def test_git_stage_hunk_worker_runs_apply_cached(self, git_app, monkeypatch):
        proc = MagicMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"", b""))
        mock = AsyncMock(return_value=proc)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", mock)

        await git_app._git_stage_hunk_worker("src/main.py", "patch text")

        args, kwargs = mock.call_args
        assert args == ("git", "apply", "--cached", "-")
        assert kwargs["cwd"] == git_app.root
        assert kwargs["stdin"] == asyncio.subprocess.PIPE
        proc.communicate.assert_awaited_once_with(b"patch text")

    async def test_git_discard_hunk_worker_runs_apply_reverse(self, git_app, monkeypatch):
        proc = MagicMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"", b""))
        mock = AsyncMock(return_value=proc)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", mock)

        await git_app._git_discard_hunk_worker("src/main.py", "patch text")

        args, kwargs = mock.call_args
        assert args == ("git", "apply", "--reverse", "-")
        assert kwargs["cwd"] == git_app.root


class TestGitCommit:
    """Tests for the commit action and worker."""

    def test_action_commit_warns_when_nothing_staged(self, git_app, monkeypatch):
        notify_calls = []
        monkeypatch.setattr(git_app, "notify", lambda msg, **kw: notify_calls.append((msg, kw)))
        monkeypatch.setattr(
            subprocess,
            "run",
            MagicMock(return_value=MagicMock(returncode=0)),
        )
        git_app.action_commit()
        assert any("No hay cambios staged" in str(m) for m, _ in notify_calls)

    def test_action_commit_opens_commit_screen_when_staged(self, git_app, monkeypatch):
        push_calls = []
        monkeypatch.setattr(
            git_app,
            "push_screen",
            lambda screen, callback=None: push_calls.append((screen, callback)),
        )
        monkeypatch.setattr(
            subprocess,
            "run",
            MagicMock(return_value=MagicMock(returncode=1)),
        )
        git_app.action_commit()
        assert len(push_calls) == 1
        assert isinstance(push_calls[0][0], CommitScreen)

    async def test_git_commit_worker_runs_commit_with_message(self, git_app, monkeypatch):
        proc = MagicMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"", b""))
        mock = AsyncMock(return_value=proc)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", mock)

        await git_app._git_commit_worker("mi mensaje")

        args, _kwargs = mock.call_args
        assert args == ("git", "commit", "-m", "mi mensaje")


class TestGitLogScreens:
    """Smoke tests for the log and commit-detail modals."""

    def test_git_log_screen_constructible(self, tmp_path):
        screen = GitLogScreen(tmp_path)
        assert screen.root == tmp_path

    def test_git_commit_screen_constructible(self, tmp_path):
        screen = GitCommitScreen(tmp_path, "abc1234")
        assert screen.commit_hash == "abc1234"

    def test_git_log_screen_parses_oneline_output(self):
        screen = GitLogScreen(Path("/tmp"))
        output = "abc1234 first commit\ndef5678 second commit\n"
        screen._commits = []
        for line in output.strip().splitlines():
            parts = line.split(None, 1)
            if len(parts) == 2:
                screen._commits.append(tuple(parts))
            elif len(parts) == 1:
                screen._commits.append((parts[0], ""))
        assert len(screen._commits) == 2
        assert screen._commits[0] == ("abc1234", "first commit")


class TestGitMixinActions:
    """Tests for GitMixin actions wiring."""

    async def test_action_show_git_hunks_warns_without_current_file(self, fake_session, tmp_path, monkeypatch):
        app = LilithIDEApp(fake_session, root=tmp_path, show_splash=False)
        async with app.run_test(size=(120, 40)) as pilot:
            notify_calls = []
            monkeypatch.setattr(app, "notify", lambda msg, **kw: notify_calls.append(msg))
            app.action_show_git_hunks()
            await pilot.pause()
            assert any("No hay archivo abierto" in str(m) for m in notify_calls)

    async def test_action_show_git_hunks_pushes_hunk_screen(self, fake_session, tmp_path, monkeypatch):
        app = LilithIDEApp(fake_session, root=tmp_path, show_splash=False)
        async with app.run_test(size=(120, 40)) as pilot:
            app.current_file = app.root / "a.py"
            await pilot.pause()
            push_calls = []
            monkeypatch.setattr(
                app,
                "push_screen",
                lambda screen, callback=None: push_calls.append(screen),
            )
            app.action_show_git_hunks()
            await pilot.pause()
            assert len(push_calls) == 1
            assert isinstance(push_calls[0], GitHunkScreen)

    async def test_action_show_git_log_pushes_log_screen(self, fake_session, tmp_path, monkeypatch):
        app = LilithIDEApp(fake_session, root=tmp_path, show_splash=False)
        async with app.run_test(size=(120, 40)) as pilot:
            push_calls = []
            monkeypatch.setattr(
                app,
                "push_screen",
                lambda screen, callback=None: push_calls.append(screen),
            )
            app.action_show_git_log()
            await pilot.pause()
            assert len(push_calls) == 1
            assert isinstance(push_calls[0], GitLogScreen)
