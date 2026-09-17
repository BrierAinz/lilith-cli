from pathlib import Path

import pytest
from textual.widgets import Input, OptionList
import yaml

from lilith_cli.hearth_ui import HearthApp


def sessions(root):
    return [{"name": "conv_one", "project_root": str(root), "preview": "Revisar parser",
             "model": "fixture", "goal": {"objective": "Cerrar la saga del parser", "status": "active"}},
            {"name": "conv_two", "project_root": str(root), "preview": "Actualizar manual",
             "model": "fixture", "goal": {"objective": "Documentación", "status": "completed"}}]


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(120, 40), (80, 30)])
async def test_search_keyboard_and_responsive_layout(tmp_path, size):
    app = HearthApp(tmp_path, sessions=sessions(tmp_path), preferences=tmp_path / "ui.yaml")
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        search = app.query_one("#search", Input)
        search.value = "parser"
        await pilot.pause()
        assert app.query_one("#sessions", OptionList).option_count == 1
        assert app.query_one("#sessions-panel").region.width > 20
        assert app.query_one("#sessions").region.height > 1
        await pilot.press("enter", "enter")
        await pilot.pause()
    assert app.return_value == ("resume", str(tmp_path), "conv_one")


@pytest.mark.asyncio
async def test_reduced_motion_is_persisted_and_freezes_animation(tmp_path):
    prefs = tmp_path / "ui.yaml"
    app = HearthApp(tmp_path, sessions=[], preferences=prefs)
    async with app.run_test(size=(100, 35)) as pilot:
        await pilot.press("f6")
        await pilot.pause()
        assert app.reduced_motion
        frame = app._frame
        app._animate_ember()
        assert app._frame == frame
        assert yaml.safe_load(prefs.read_text(encoding="utf-8"))["reduced_motion"]
        await pilot.press("ctrl+n")
        from lilith_cli.hearth_screens import TaskScreen
        assert isinstance(app.screen, TaskScreen)
    assert HearthApp(tmp_path, sessions=[], preferences=prefs).reduced_motion


@pytest.mark.asyncio
async def test_project_switch_filters_without_starting_work(tmp_path):
    another = tmp_path / "another"
    another.mkdir()
    app = HearthApp(tmp_path, sessions=sessions(tmp_path), preferences=tmp_path / "ui.yaml")
    async with app.run_test(size=(120, 40)) as pilot:
        field = app.query_one("#project", Input)
        field.value = str(another)
        field.focus()
        await pilot.press("enter")
        await pilot.pause()
        assert app.root == another
        assert app.query_one("#sessions", OptionList).option_count == 0
        assert app.return_value is None


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(120, 40), (80, 30)])
async def test_coordinator_entry_uses_selected_project(tmp_path, size):
    app = HearthApp(tmp_path, sessions=[], preferences=tmp_path / "ui.yaml")
    async with app.run_test(size=size) as pilot:
        await pilot.press("f8")
    assert app.return_value == ("coordinate", str(tmp_path), None)


@pytest.mark.asyncio
async def test_coordinator_rejects_invalid_project_without_exiting(tmp_path):
    app = HearthApp(tmp_path, sessions=[], preferences=tmp_path / "ui.yaml")
    async with app.run_test(size=(100, 35)) as pilot:
        app.query_one("#project", Input).value = str(tmp_path / "missing")
        await pilot.press("f8")
        assert app.return_value is None
