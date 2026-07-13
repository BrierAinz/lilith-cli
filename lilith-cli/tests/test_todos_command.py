"""Tests for the /todos slash command."""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

import pytest
from rich.console import Console


def _render(prints) -> str:
    """Render captured Rich renderables to plain text."""
    buf = StringIO()
    c = Console(file=buf, force_terminal=False, width=200, record=True)
    for entry in prints:
        for obj in entry:
            if obj is None or obj == "":
                continue
            try:
                c.print(obj)
            except Exception:
                buf.write(repr(obj))
    return c.export_text(clear=False)


def _tool_result(success: bool = True, data=None, error: str | None = None):
    """Return a ToolResult-shaped object compatible with the command helpers."""
    from lilith_tools.base import ToolResult

    return ToolResult(success=success, data=data, error=error)


@pytest.fixture
def patched_todo_tools(monkeypatch):
    """Patch all Todo*Tool classes so tests do not touch real todo storage."""
    import lilith_cli.extra_commands as ec
    import lilith_tools.todos as todos_mod

    list_result = _tool_result(success=True, data={"todos": [], "count": 0})
    add_result = _tool_result(success=True, data={"index": 1, "todo": {"text": "demo", "done": False}})
    done_result = _tool_result(success=True, data={"message": "ok"})
    remove_result = _tool_result(success=True, data={"message": "ok"})
    clear_count = 0

    class FakeList:
        def execute(self, **_kw):
            return list_result

    class FakeAdd:
        def execute(self, **_kw):
            return add_result

    class FakeDone:
        def execute(self, **_kw):
            return done_result

    class FakeRemove:
        def execute(self, **_kw):
            return remove_result

    class FakeManager:
        def clear(self):
            return clear_count

    monkeypatch.setattr(ec, "TodoListTool", FakeList)
    monkeypatch.setattr(ec, "TodoAddTool", FakeAdd)
    monkeypatch.setattr(ec, "TodoDoneTool", FakeDone)
    monkeypatch.setattr(ec, "TodoRemoveTool", FakeRemove)
    monkeypatch.setattr(todos_mod, "TodoManager", FakeManager)

    return {
        "list_result": list_result,
        "add_result": add_result,
        "done_result": done_result,
        "remove_result": remove_result,
        "clear_count": clear_count,
    }


@pytest.mark.asyncio
async def test_todos_list_empty(fake_session, patched_todo_tools):
    """/todos with no args on an empty list prints the empty-state message."""
    prints = []

    def capture(*args, **kwargs):
        prints.append(args)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        from lilith_cli.extra_commands import run_todos_command

        await run_todos_command(fake_session, "")

    rendered = _render(prints)
    assert "No hay tareas pendientes" in rendered


@pytest.mark.asyncio
async def test_todos_list_renders_rows(fake_session, monkeypatch):
    """/todos list must render each todo item as a row."""
    import lilith_cli.extra_commands as ec

    sample = [
        {"content": "Comprar leche", "done": False},
        {"content": "Pagar impuestos", "done": True},
    ]

    class FakeList:
        def execute(self, **_kw):
            from lilith_tools.base import ToolResult

            return ToolResult(success=True, data=sample)

    monkeypatch.setattr(ec, "TodoListTool", FakeList)

    prints = []

    def capture(*args, **kwargs):
        prints.append(args)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        from lilith_cli.extra_commands import run_todos_command

        await run_todos_command(fake_session, "list")

    rendered = _render(prints)
    assert "Comprar leche" in rendered
    assert "Pagar impuestos" in rendered


@pytest.mark.asyncio
async def test_todos_add_invokes_add_tool(fake_session, monkeypatch):
    """/todos add <text> must invoke TodoAddTool with the rest text."""
    import lilith_cli.extra_commands as ec
    from lilith_tools.base import ToolResult

    captured_kwargs: dict[str, str] = {}

    class FakeAdd:
        def execute(self, **kw):
            captured_kwargs.update(kw)
            return ToolResult(success=True, data={"index": 1, "todo": {"text": kw.get("text", ""), "done": False}})

    class FakeList:
        def execute(self, **_kw):
            return ToolResult(success=True, data={"todos": [], "count": 0})

    monkeypatch.setattr(ec, "TodoAddTool", FakeAdd)
    monkeypatch.setattr(ec, "TodoListTool", FakeList)

    with patch("lilith_cli.extra_commands.console.print"):
        from lilith_cli.extra_commands import run_todos_command

        await run_todos_command(fake_session, "add comprar pan")

    assert captured_kwargs.get("text") == "comprar pan"


@pytest.mark.asyncio
async def test_todos_done_passes_index(fake_session, patched_todo_tools, monkeypatch):
    """/todos done 2 must invoke TodoDoneTool with index=2."""
    import lilith_cli.extra_commands as ec
    from lilith_tools.base import ToolResult

    captured: dict[str, int] = {}

    class FakeDone:
        def execute(self, **kw):
            captured.update(kw)
            return ToolResult(success=True, data={"message": "ok"})

    monkeypatch.setattr(ec, "TodoDoneTool", FakeDone)

    with patch("lilith_cli.extra_commands.console.print"):
        from lilith_cli.extra_commands import run_todos_command

        await run_todos_command(fake_session, "done 3")

    assert captured["index"] == 3


@pytest.mark.asyncio
async def test_todos_remove_passes_index(fake_session, monkeypatch):
    """/todos remove 4 must invoke TodoRemoveTool with index=4."""
    import lilith_cli.extra_commands as ec
    from lilith_tools.base import ToolResult

    captured: dict[str, int] = {}

    class FakeRemove:
        def execute(self, **kw):
            captured.update(kw)
            return ToolResult(success=True, data={"message": "ok"})

    monkeypatch.setattr(ec, "TodoRemoveTool", FakeRemove)

    with patch("lilith_cli.extra_commands.console.print"):
        from lilith_cli.extra_commands import run_todos_command

        await run_todos_command(fake_session, "remove 4")

    assert captured["index"] == 4


@pytest.mark.asyncio
async def test_todos_clear_reports_count(fake_session, monkeypatch):
    """/todos clear must invoke TodoManager().clear() and report how many were removed."""
    import lilith_tools.todos as todos_mod

    class FakeManager:
        def clear(self):
            return 7

    monkeypatch.setattr(todos_mod, "TodoManager", FakeManager)

    prints = []

    def capture(*args, **kwargs):
        prints.append(args)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        from lilith_cli.extra_commands import run_todos_command

        await run_todos_command(fake_session, "clear")

    combined = "\n".join(str(s) for entry in prints for s in entry if isinstance(s, str))
    assert "7" in combined
    assert "limpiada" in combined.lower() or "elimin" in combined.lower()


@pytest.mark.asyncio
async def test_todos_unknown_subcommand_shows_error(fake_session, capsys):
    """/todos <unknown> must print an error referencing the unknown subcommand.

    Asserts via capsys: error output goes through render_error, whose console
    binding can be swapped by other test modules (e.g. theme tests), so
    patching extra_commands.console.print is order-fragile.
    """
    from lilith_cli.extra_commands import run_todos_command

    await run_todos_command(fake_session, "frobnicate")

    combined = capsys.readouterr().out
    assert "frobnicate" in combined
    assert "Subcomando" in combined or "desconocido" in combined.lower()


@pytest.mark.asyncio
async def test_todos_done_non_integer_reports_error(fake_session, capsys):
    """/todos done abc must print a usage error and not raise."""
    from lilith_cli.extra_commands import run_todos_command

    await run_todos_command(fake_session, "done abc")

    combined = capsys.readouterr().out
    assert "Uso:" in combined or "número" in combined.lower() or "numero" in combined.lower()


@pytest.mark.asyncio
async def test_todos_add_without_text_reports_error(fake_session, capsys):
    """/todos add (with no text) must print a usage error and not raise."""
    from lilith_cli.extra_commands import run_todos_command

    await run_todos_command(fake_session, "add")

    combined = capsys.readouterr().out
    assert "Uso:" in combined
