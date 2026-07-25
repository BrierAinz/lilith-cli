"""Focused tests for ``/todos due``."""

from __future__ import annotations

from datetime import datetime, timedelta
from io import StringIO
from unittest.mock import patch

import pytest
from rich.console import Console


def _render(prints) -> str:
    output = StringIO()
    console = Console(file=output, force_terminal=False, width=200)
    for args in prints:
        console.print(*args)
    return output.getvalue()


@pytest.mark.asyncio
async def test_todos_due_filters_future_and_completed(fake_session, monkeypatch):
    import lilith_cli.extra_commands as commands
    from lilith_tools.base import ToolResult

    today = datetime.now().astimezone().date()
    data = {"todos": [
        {"index": 1, "text": "Vencida", "done": False, "due_date": str(today - timedelta(days=1))},
        {"index": 2, "text": "Para hoy", "done": False, "due_date": str(today)},
        {"index": 3, "text": "Futura", "done": False, "due_date": str(today + timedelta(days=1))},
        {"index": 4, "text": "Terminada", "done": True, "due_date": str(today)},
    ]}

    class FakeList:
        def execute(self):
            return ToolResult(success=True, data=data)

    monkeypatch.setattr(commands, "TodoListTool", FakeList)
    prints = []
    with patch.object(commands.console, "print", side_effect=lambda *args, **_kw: prints.append(args)):
        await commands.run_todos_command(fake_session, "due")

    rendered = _render(prints)
    assert "Vencida" in rendered
    assert "Para hoy" in rendered
    assert "Futura" not in rendered
    assert "Terminada" not in rendered


@pytest.mark.asyncio
async def test_todos_due_falls_back_when_storage_has_no_due_dates(fake_session, monkeypatch):
    import lilith_cli.extra_commands as commands
    from lilith_tools.base import ToolResult

    data = [{"index": 1, "text": "Revisar contrato", "done": False, "created_at": "2026-07-24T10:00:00"}]

    class FakeList:
        def execute(self):
            return ToolResult(success=True, data=data)

    monkeypatch.setattr(commands, "TodoListTool", FakeList)
    prints = []
    with patch.object(commands.console, "print", side_effect=lambda *args, **_kw: prints.append(args)):
        await commands.run_todos_command(fake_session, "due")

    rendered = _render(prints)
    assert "no admite fechas de vencimiento" in rendered
    assert "Revisar contrato" in rendered
    assert "2026-07-24" in rendered
