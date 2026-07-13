"""Tests for render_git_status() and render_todos() helpers."""

from __future__ import annotations

import io

import pytest


class TestRenderGitStatus:
    """Verify render_git_status renders a Rich table from a status dict."""

    def test_renders_branch_and_files(self) -> None:
        from lilith_cli.render import render_git_status
        # Just verify it doesn't raise.
        render_git_status(
            {
                "branch": "main",
                "files": [
                    {"path": "foo.py", "status": "M"},
                    {"path": "bar.py", "status": "A"},
                    {"path": "baz.py", "status": "??"},
                ],
            }
        )

    def test_empty_status(self) -> None:
        from lilith_cli.render import render_git_status
        render_git_status({"branch": "main", "files": []})

    def test_missing_branch_defaults(self) -> None:
        from lilith_cli.render import render_git_status
        render_git_status({"files": [{"path": "x.py", "status": "M"}]})

    def test_handles_string_files(self) -> None:
        """Verify it doesn't crash on non-dict file entries."""
        from lilith_cli.render import render_git_status
        render_git_status({"branch": "main", "files": ["x.py", "y.py"]})


class TestRenderTodos:
    """Verify render_todos renders a Rich checklist from a list of todo dicts."""

    def test_renders_list_of_dicts(self) -> None:
        from lilith_cli.render import render_todos
        render_todos(
            [
                {"text": "First todo", "done": True},
                {"text": "Second todo", "done": False},
                {"text": "Third todo", "done": False},
            ]
        )

    def test_renders_list_of_objects(self) -> None:
        """Verify it accepts objects with .text and .done attributes."""
        from lilith_cli.render import render_todos

        class FakeTodo:
            def __init__(self, text: str, done: bool) -> None:
                self.text = text
                self.done = done

        render_todos([FakeTodo("a", True), FakeTodo("b", False)])

    def test_empty(self) -> None:
        from lilith_cli.render import render_todos
        render_todos([])

    def test_all_done(self) -> None:
        from lilith_cli.render import render_todos
        render_todos(
            [
                {"text": "a", "done": True},
                {"text": "b", "done": True},
            ]
        )
