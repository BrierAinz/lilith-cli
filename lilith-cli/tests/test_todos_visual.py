"""Tests for /todos list Rich Table rendering."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch


def _run(coro):
    return asyncio.run(coro)


def test_todos_list_empty(fake_session, capsys):
    """/todos on empty list shows friendly message."""
    from lilith_cli.extra_commands import _render_todos_table

    _render_todos_table([])
    out = capsys.readouterr().out
    assert "No hay tareas" in out


def test_todos_list_with_pending(fake_session, capsys):
    """/todos with pending items shows them with empty circle."""
    from lilith_cli.extra_commands import _render_todos_table

    todos = [
        {"content": "comprar leche", "status": "pending"},
        {"content": "pasear al perro", "status": "pending"},
    ]
    _render_todos_table(todos)
    out = capsys.readouterr().out
    assert "Tareas pendientes" in out
    assert "comprar leche" in out
    assert "pasear al perro" in out
    assert "2 tarea" in out


def test_todos_list_with_done(fake_session, capsys):
    """/todos with completed items shows checkmark."""
    from lilith_cli.extra_commands import _render_todos_table

    todos = [
        {"content": "done task", "status": "done"},
        {"content": "pending task", "status": "pending"},
    ]
    _render_todos_table(todos)
    out = capsys.readouterr().out
    assert "done task" in out
    assert "pending task" in out


def test_todos_list_with_in_progress(fake_session, capsys):
    """/todos with in_progress items shows yellow dot."""
    from lilith_cli.extra_commands import _render_todos_table

    todos = [
        {"content": "wip task", "status": "in_progress"},
    ]
    _render_todos_table(todos)
    out = capsys.readouterr().out
    assert "wip task" in out


def test_todos_list_string_fallback(fake_session, capsys):
    """/todos handles plain string items (not dicts)."""
    from lilith_cli.extra_commands import _render_todos_table

    todos = ["plain string task 1", "plain string task 2"]
    _render_todos_table(todos)
    out = capsys.readouterr().out
    assert "plain string task 1" in out
    assert "plain string task 2" in out


def test_todos_list_renders_table(fake_session, capsys):
    """/todos uses Rich Table (box-drawing chars)."""
    from lilith_cli.extra_commands import _render_todos_table

    todos = [{"content": "x", "status": "pending"}]
    _render_todos_table(todos)
    out = capsys.readouterr().out
    assert "\u250c" in out or "\u2502" in out


def test_todos_list_numbering(fake_session, capsys):
    """/todos numbers items sequentially."""
    from lilith_cli.extra_commands import _render_todos_table

    todos = [{"content": "a"}, {"content": "b"}, {"content": "c"}]
    _render_todos_table(todos)
    out = capsys.readouterr().out
    # Each row should have its number
    assert "1." in out or "1 " in out
    assert "2." in out or "2 " in out
    assert "3." in out or "3 " in out


def test_todos_command_uses_renderer(fake_session, capsys):
    """/todos (no args) delegates to _render_todos_table."""
    from lilith_cli.extra_commands import run_todos_command

    fake_result = SimpleNamespace(
        success=True,
        error=None,
        data=[
            {"content": "from test", "status": "pending"},
        ],
    )

    with patch("lilith_cli.extra_commands.TodoListTool") as MockTool:
        MockTool.return_value.execute.return_value = fake_result
        _run(run_todos_command(fake_session, ""))

    out = capsys.readouterr().out
    assert "from test" in out
    assert "Tareas pendientes" in out