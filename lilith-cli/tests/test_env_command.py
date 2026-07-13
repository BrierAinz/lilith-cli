"""Tests for the /env slash command."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest


def _strings(prints) -> str:
    """Flatten captured Rich renderables (and strings) into searchable text."""
    parts: list[str] = []
    for entry in prints:
        for obj in entry:
            if obj is None or obj == "":
                continue
            if isinstance(obj, str):
                parts.append(obj)
                continue
            # Renderable: extract Table / Panel title.
            title = getattr(obj, "title", None)
            if title is not None:
                parts.append(str(title))
            # Per-column cell values.
            for col in getattr(obj, "columns", []):
                cells = getattr(col, "_cells", [])
                for cell in cells:
                    if isinstance(cell, str):
                        parts.append(cell)
                    else:
                        parts.append(repr(cell))
    return "\n".join(parts)


def _tool_result(success: bool = True, data=None, error: str | None = None):
    from lilith_tools.base import ToolResult

    return ToolResult(success=success, data=data, error=error)


@pytest.fixture
def patched_env_tools(monkeypatch):
    """Patch EnvListTool / EnvGetTool / SysInfoTool in the extra_commands module."""
    import lilith_cli.extra_commands as ec

    list_data = {
        "variables": {"PATH": "/usr/bin", "PYTHON": "3.11"},
        "total": 2,
        "returned": 2,
        "prefix": "",
        "limit": 50,
    }
    list_result = _tool_result(success=True, data=list_data)
    info_result = _tool_result(
        success=True,
        data={
            "python_version": "3.11.0",
            "python_implementation": "CPython",
            "os": "Windows",
            "os_version": "10",
            "machine": "x86_64",
            "processor": "AMD64",
            "platform": "win32",
            "node": "test-node",
            "disk": {"free_gb": 100.0, "total_gb": 500.0},
        },
    )
    get_result = _tool_result(success=True, data={"PATH": "/usr/bin"})

    class FakeList:
        def execute(self, **kw):
            return list_result

    class FakeGet:
        def execute(self, **kw):
            return get_result

    class FakeSys:
        def execute(self, **_kw):
            return info_result

    monkeypatch.setattr(ec, "EnvListTool", FakeList)
    monkeypatch.setattr(ec, "EnvGetTool", FakeGet)
    monkeypatch.setattr(ec, "SysInfoTool", FakeSys)

    return {
        "list_result": list_result,
        "info_result": info_result,
        "get_result": get_result,
    }


@pytest.mark.asyncio
async def test_env_json_flag_emits_json(fake_session, patched_env_tools, capsys):
    """/env --json alone must emit a JSON document to stdout."""
    from lilith_cli.extra_commands import run_env_command

    await run_env_command(fake_session, "--json")

    out = capsys.readouterr().out
    payload = json.loads(out.strip())
    assert "variables" in payload
    assert "PATH" in payload["variables"]


@pytest.mark.asyncio
async def test_env_list_renders_table(fake_session, patched_env_tools):
    """/env with no args must render the env-list table."""
    prints = []

    def capture(*args, **kwargs):
        prints.append(args)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        from lilith_cli.extra_commands import run_env_command

        await run_env_command(fake_session, "")

    rendered = _strings(prints)
    assert "Variables de entorno" in rendered
    assert "PATH" in rendered
    assert "PYTHON" in rendered


@pytest.mark.asyncio
async def test_env_list_alias_renders_table(fake_session, patched_env_tools):
    """/env list (and ls / all) must also render the env-list table."""
    prints = []

    def capture(*args, **kwargs):
        prints.append(args)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        from lilith_cli.extra_commands import run_env_command

        await run_env_command(fake_session, "all")

    rendered = _strings(prints)
    assert "Variables de entorno" in rendered


@pytest.mark.asyncio
async def test_env_info_renders_table(fake_session, patched_env_tools):
    """/env info must render the sys-info table with Python / OS / disk rows."""
    prints = []

    def capture(*args, **kwargs):
        prints.append(args)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        from lilith_cli.extra_commands import run_env_command

        await run_env_command(fake_session, "info")

    rendered = _strings(prints)
    assert "Información del sistema" in rendered
    assert "Python" in rendered
    assert "Sistema operativo" in rendered
    assert "Windows" in rendered


@pytest.mark.asyncio
async def test_env_prefix_filters_results(fake_session, monkeypatch, patched_env_tools):
    """/env prefix PYTHON must invoke EnvListTool with the prefix kwarg."""
    import lilith_cli.extra_commands as ec
    from lilith_tools.base import ToolResult

    captured: dict[str, object] = {}

    class FakeList:
        def execute(self, **kw):
            captured.update(kw)
            return ToolResult(success=True, data={"variables": {"PYTHONPATH": "x"}, "total": 1, "returned": 1, "prefix": kw.get("prefix", ""), "limit": 50})

    monkeypatch.setattr(ec, "EnvListTool", FakeList)

    with patch("lilith_cli.extra_commands.console.print"):
        from lilith_cli.extra_commands import run_env_command

        await run_env_command(fake_session, "prefix PYTHON")

    assert captured.get("prefix") == "PYTHON"


@pytest.mark.asyncio
async def test_env_prefix_no_value_reports_error(fake_session, patched_env_tools):
    """/env prefix (no value) must print a usage error and not raise."""
    prints = []

    def capture(*args, **kwargs):
        prints.append(args)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        from lilith_cli.extra_commands import run_env_command

        await run_env_command(fake_session, "prefix")

    combined = "\n".join(str(s) for entry in prints for s in entry if isinstance(s, str))
    assert "Uso:" in combined


@pytest.mark.asyncio
async def test_env_unset_simulates_deletion(fake_session, monkeypatch):
    """/env unset FOO must print a simulation warning WITHOUT mutating os.environ."""
    monkeypatch.setenv("FOO", "bar")

    prints = []

    def capture(*args, **kwargs):
        prints.append(args)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        from lilith_cli.extra_commands import run_env_command

        await run_env_command(fake_session, "unset FOO")

    # FOO must still exist (the command is non-destructive).
    import os

    assert os.environ.get("FOO") == "bar"

    combined = "\n".join(str(s) for entry in prints for s in entry if isinstance(s, str))
    assert "FOO" in combined
    assert "Simulación" in combined or "simulación" in combined.lower() or "Simula" in combined or "elimin" in combined.lower()


@pytest.mark.asyncio
async def test_env_unset_no_name_reports_error(fake_session, monkeypatch):
    """/env unset (no name) must print a usage error."""
    prints = []

    def capture(*args, **kwargs):
        prints.append(args)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        from lilith_cli.extra_commands import run_env_command

        await run_env_command(fake_session, "unset")

    combined = "\n".join(str(s) for entry in prints for s in entry if isinstance(s, str))
    assert "Uso:" in combined


@pytest.mark.asyncio
async def test_env_get_single_var(fake_session, monkeypatch, patched_env_tools):
    """/env PATH must invoke EnvGetTool with name=PATH."""
    import lilith_cli.extra_commands as ec
    from lilith_tools.base import ToolResult

    captured: dict[str, object] = {}

    class FakeGet:
        def execute(self, **kw):
            captured.update(kw)
            return ToolResult(success=True, data={kw.get("name", ""): "/usr/bin"})

    monkeypatch.setattr(ec, "EnvGetTool", FakeGet)

    with patch("lilith_cli.extra_commands.console.print"):
        from lilith_cli.extra_commands import run_env_command

        await run_env_command(fake_session, "PATH")

    assert captured.get("name") == "PATH"


@pytest.mark.asyncio
async def test_env_get_failure_reports_error(fake_session, monkeypatch):
    """/env MISSING when the get tool fails must print a usage error."""
    import lilith_cli.extra_commands as ec
    from lilith_tools.base import ToolResult

    class FakeGet:
        def execute(self, **kw):
            return ToolResult(success=False, data=None, error="not found")

    monkeypatch.setattr(ec, "EnvGetTool", FakeGet)

    prints = []

    def capture(*args, **kwargs):
        prints.append(args)

    with patch("lilith_cli.extra_commands.console.print", side_effect=capture):
        from lilith_cli.extra_commands import run_env_command

        await run_env_command(fake_session, "MISSING_VAR")

    combined = "\n".join(str(s) for entry in prints for s in entry if isinstance(s, str))
    assert "not found" in combined or "MISSING_VAR" in combined
