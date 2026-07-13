"""Tests for the /search slash command and search tools."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from lilith_cli.extra_commands import run_search_command
from lilith_tools.search import (
    SearchAcrossFilesTool,
    SearchHistoryTool,
    SearchInFileTool,
    set_session_history_ref,
)


class DummyConfig:
    def __init__(self):
        self.model = "test"
        self.provider = "test"
        self.providers = {}
        self.api_key = ""
        self.system_prompt = ""

    def model_dump(self):
        return {
            "model": self.model,
            "provider": self.provider,
            "providers": self.providers,
            "api_key": self.api_key,
        }


class DummySession:
    def __init__(self):
        self.config = DummyConfig()
        self.memory = None
        self.history = []
        self.provider = None
        self.system_prompt = ""


def _render_panels_to_text(prints):
    """Render captured Rich renderables (Panels/Tables) to plain text.

    /search uses Rich Panel + Table for visual upgrade; tests must render the
    captured renderables through Rich's pipeline to inspect their content.
    """
    from io import StringIO
    from rich.console import Console

    buf = StringIO()
    c = Console(file=buf, force_terminal=False, width=200, record=True)
    for entry in prints:
        # entry is the args tuple passed to console.print(*args)
        for obj in entry:
            if obj is None or obj == "":
                continue
            try:
                c.print(obj)
            except Exception:
                buf.write(repr(obj))
    return c.export_text(clear=False)


@pytest.mark.asyncio
async def test_search_history_command_finds_user_prompt():
    """/search <query> encuentra un mensaje previo del usuario en el historial."""
    history = [
        {"role": "user", "content": "Hola Lilith, arreglame el bug"},
        {"role": "assistant", "content": "Hola, con gusto"},
    ]
    set_session_history_ref(history)
    try:
        session = DummySession()
        prints = []

        def capture(*args, **kwargs):
            prints.append(args)

        with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
            await run_search_command(session, "bug")

        rendered = _render_panels_to_text(prints)
        assert "bug" in rendered
        assert "Coincidencias" in rendered
        assert "Historial" in rendered
    finally:
        set_session_history_ref(None)


@pytest.mark.asyncio
async def test_search_in_file_command_finds_line(tmp_path, monkeypatch):
    """/search in <path> <query> encuentra una linea en un archivo."""
    monkeypatch.chdir(tmp_path)
    test_file = tmp_path / "sample.txt"
    test_file.write_text("linea uno\nlinea con bug\nlinea tres\n", encoding="utf-8")
    session = DummySession()
    prints = []

    def capture(*args, **kwargs):
        prints.append(args)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        await run_search_command(session, f"in {test_file} bug")

    rendered = _render_panels_to_text(prints)
    assert "bug" in rendered
    assert "Linea" in rendered
    assert "En archivo" in rendered


@pytest.mark.asyncio
async def test_search_across_files_command_finds_match(tmp_path, monkeypatch):
    """/search across <pattern> [path] encuentra coincidencias en varios archivos."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    pass\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def bar():\n    pass\n", encoding="utf-8")
    session = DummySession()
    prints = []

    def capture(*args, **kwargs):
        prints.append(args)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        await run_search_command(session, f"across def.*foo {tmp_path}")

    rendered = _render_panels_to_text(prints)
    assert "foo" in rendered
    assert "Archivo" in rendered
    assert "En archivos" in rendered


@pytest.mark.asyncio
async def test_search_history_tool_returns_matches():
    """search_history devuelve coincidencias del historial."""
    set_session_history_ref([
        {"role": "user", "content": "mensaje uno"},
        {"role": "assistant", "content": "respuesta uno"},
        {"role": "user", "content": "mensaje dos con error"},
    ])
    try:
        tool = SearchHistoryTool()
        result = tool.execute(query="error")
        assert result.success
        data = result.data
        assert data["count"] == 1
        assert data["matches"][0]["role"] == "user"
        assert "error" in data["matches"][0]["content"]
    finally:
        set_session_history_ref(None)


@pytest.mark.asyncio
async def test_search_in_file_tool_returns_context():
    """search_in_file devuelve contexto de lineas."""
    path = Path("test_file.txt")
    path.write_text("linea uno\nlinea dos\nlinea tres\n", encoding="utf-8")
    try:
        tool = SearchInFileTool()
        result = tool.execute(path=str(path), query="dos", context_lines=1)
        assert result.success
        data = result.data
        assert data["count"] == 1
        match = data["matches"][0]
        assert match["line_number"] == 2
        line_numbers = [ln for ln, _ in match["context"]]
        assert 1 in line_numbers and 2 in line_numbers and 3 in line_numbers
    finally:
        path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_search_across_files_tool_returns_matches(tmp_path, monkeypatch):
    """search_across_files encuentra coincidencias en multiples archivos."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "alpha.py").write_text("alpha value\n", encoding="utf-8")
    (tmp_path / "beta.py").write_text("beta value\n", encoding="utf-8")
    (tmp_path / "gamma.txt").write_text("gamma value\n", encoding="utf-8")
    tool = SearchAcrossFilesTool()
    result = tool.execute(pattern="value", path=str(tmp_path), file_glob="*.py")
    assert result.success
    data = result.data
    assert data["count"] == 2
    files = {m["file"] for m in data["matches"]}
    assert any("alpha.py" in f for f in files)
    assert any("beta.py" in f for f in files)


@pytest.mark.asyncio
async def test_search_history_tool_filters_by_role():
    """search_history filtra por rol."""
    set_session_history_ref([
        {"role": "user", "content": "hola"},
        {"role": "assistant", "content": "hola"},
    ])
    try:
        tool = SearchHistoryTool()
        result = tool.execute(query="hola", role="assistant")
        assert result.success
        assert all(m["role"] == "assistant" for m in result.data["matches"])
    finally:
        set_session_history_ref(None)


@pytest.mark.asyncio
async def test_search_in_file_tool_invalid_regex():
    """search_in_file devuelve error con regex invalido."""
    path = Path("regex_test.txt")
    path.write_text("contenido", encoding="utf-8")
    try:
        tool = SearchInFileTool()
        result = tool.execute(path=str(path), query="[invalid")
        assert not result.success
        assert "invalido" in result.error.lower()
    finally:
        path.unlink(missing_ok=True)