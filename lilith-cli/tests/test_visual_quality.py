"""Offline behavioral acceptance for Lilith's shared visual workspace."""
from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App
from textual.widgets import Button, Checkbox, Input, ListView, TextArea

from lilith_cli.hearth_ui import HearthApp
from lilith_cli.hearth_screens import TaskScreen
from lilith_cli.ide import IDEConfig, LilithIDEApp
from lilith_cli.ide.widgets.command_palette import CommandPaletteScreen, PaletteItem, matches
from lilith_cli.ui_quality import context_text, read_state, safe_display, safe_path, update_state
from lilith_cli.ui_widgets import FollowLog, InspectorScreen, MessageInput, ToolDetailsScreen


@pytest.fixture(autouse=True)
def isolated_ui(monkeypatch):
    monkeypatch.setattr(IDEConfig, "load", classmethod(lambda cls, path=None: cls(auto_reload=False, auto_save=False)))
    monkeypatch.setattr(IDEConfig, "save", lambda *args, **kwargs: None)
    monkeypatch.setattr(LilithIDEApp, "_load_plugins", lambda self: None)


def test_state_merge_and_project_isolation(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    update_state(tmp_path, draft="mensaje", reduced_motion=True)
    update_state(tmp_path, theme="norse-light")
    assert read_state(tmp_path) == {"draft": "mensaje", "reduced_motion": True, "theme": "norse-light"}
    assert read_state(other) == {}
    assert not list((tmp_path / ".ygg/ui").glob(".ui-*"))


@pytest.mark.parametrize("name", ["../escape", "sub/../../escape"])
def test_context_rejects_escape(tmp_path, name):
    with pytest.raises(ValueError):
        safe_path(tmp_path, name)


def test_context_bounds_and_credentials(tmp_path):
    (tmp_path / "readme.md").write_text("hello", encoding="utf-8")
    assert "hello" in context_text(tmp_path, ["readme.md"])
    (tmp_path / "big.txt").write_bytes(b"x" * 32769)
    (tmp_path / ".env").write_text("fixture", encoding="utf-8")
    for name in ["big.txt", ".env"]:
        with pytest.raises(ValueError):
            context_text(tmp_path, [name])
    with pytest.raises(ValueError):
        context_text(tmp_path, ["readme.md"] * 13)


def test_display_masks_credentials_and_controls():
    assert "fictional-value" not in safe_display("api_key=fictional-value")
    assert "fictional-token" not in safe_display("Bearer fictional-token")
    assert "\x1b" not in safe_display("\x1b[31mred")


def test_invalid_visual_state_is_safe(tmp_path):
    (tmp_path / ".ygg/ui").mkdir(parents=True)
    file = tmp_path / ".ygg/ui/workspace.yaml"
    file.write_text("- not-a-map", encoding="utf-8")
    assert read_state(tmp_path) == {}
    file.write_text("x" * 262145, encoding="utf-8")
    assert read_state(tmp_path) == {}


def test_fuzzy_palette_and_unique_bindings():
    item = PaletteItem("Configuración del proyecto", lambda: None)
    assert matches("configuracion", item)
    assert matches("cnfgr", item)
    keys = [binding.key for binding in LilithIDEApp.BINDINGS]
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize("size", [(120, 40), (80, 30), (60, 24)])
async def test_layouts_resize_and_keyboard(fake_session, tmp_path, size):
    app = LilithIDEApp(fake_session, root=tmp_path, show_splash=True)
    async with app.run_test(size=size) as pilot:
        await pilot.press("h", "i", "enter", "x")
        assert app.query_one("#chat-input", MessageInput).text == "hi\nx"
        for key, panel in [("f3", "#editor-panel"), ("f4", "#review-panel"), ("f2", "#chat-panel")]:
            await pilot.press(key)
            await pilot.pause()
            assert app.query_one(panel).display
            assert app.query_one(panel).region.width > 20
            assert app.query_one(panel).region.height > 2
        await pilot.resize_terminal(72, 28)
        await pilot.pause()
        assert app.screen.has_class("narrow")
        await pilot.press("f6")
        await pilot.pause()
        assert app.reduced_motion
        await pilot.press("f9")
        await pilot.pause()
        assert app._compact


async def test_follow_log_does_not_jump(fake_session, tmp_path):
    app = LilithIDEApp(fake_session, root=tmp_path, show_splash=False)
    async with app.run_test(size=(120, 40)) as pilot:
        log = app.query_one("#chat-log", FollowLog)
        for n in range(150):
            log.write(f"line {n}")
        await pilot.pause()
        log.scroll_home(animate=False, immediate=True)
        await pilot.pause()
        before = log.scroll_y
        app._chat_assistant_chunk("new content")
        await pilot.pause()
        assert log.scroll_y == before
        assert log.unread > 0
        assert app.query_one("#new-content", Button).display
        log.latest()
        await pilot.pause()
        assert log.unread == 0
        assert log.is_vertical_scroll_end


async def test_draft_persists_but_context_does_not_auto_attach(fake_session, tmp_path):
    (tmp_path / "readme.md").write_text("public fixture", encoding="utf-8")
    app = LilithIDEApp(fake_session, root=tmp_path, show_splash=False)
    async with app.run_test(size=(120, 40)) as pilot:
        app.query_one("#chat-input", MessageInput).value = "first\nsecond"
        app._set_context(["readme.md"])
        app._save_draft()
        assert read_state(tmp_path)["draft"] == "first\nsecond"
        app._set_context([])
        assert app._context_files == []
    restored = LilithIDEApp(fake_session, root=tmp_path, show_splash=False)
    assert restored._context_files == []
    async with restored.run_test() as pilot:
        assert restored.query_one("#chat-input", MessageInput).text == "first\nsecond"


async def test_busy_keeps_draft(fake_session, tmp_path):
    app = LilithIDEApp(fake_session, root=tmp_path, show_splash=False)
    async with app.run_test() as pilot:
        field = app.query_one("#chat-input", MessageInput)
        field.value = "retain me"
        app._thinking = True
        app._send_message()
        assert field.text == "retain me"
        app._thinking = False


async def test_stream_tool_details_and_inspector(fake_session, tmp_path, monkeypatch):
    async def stream(prompt):
        yield {"type": "tool_call", "name": "read_file", "arguments": {"path": "fixture.txt"}}
        yield {"type": "tool_result", "name": "read_file", "content": "fixture result"}
        yield {"type": "text", "content": "[not markup] fixture answer"}
        yield {"type": "done", "usage": {}}
    monkeypatch.setattr(fake_session, "process_message_stream", stream)
    app = LilithIDEApp(fake_session, root=tmp_path, show_splash=False)
    async with app.run_test(size=(120, 40)) as pilot:
        app.query_one("#chat-input", MessageInput).value = "test objective"
        await pilot.press("ctrl+enter")
        await pilot.pause()
        await app._active_worker.wait()
        assert "pendiente de revisión" in app._execution_stage
        assert "no aprobado" in app._tool_events[0]["state"]
        await pilot.press("f10")
        assert isinstance(app.screen, ToolDetailsScreen)
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("f7")
        await pilot.pause()
        assert isinstance(app.screen, InspectorScreen)
        assert "no verificadas" in app._inspector_text()


async def test_palette_categories_never_offset_action():
    app = App()
    selected = []
    items = [PaletteItem("first", lambda: selected.append(1), category="A"),
             PaletteItem("second", lambda: selected.append(2), category="B")]
    async with app.run_test() as pilot:
        app.push_screen(CommandPaletteScreen(items), lambda cb: cb() if cb else None)
        await pilot.pause()
        await pilot.press("down", "enter")
        await pilot.pause()
        assert selected == [2]


async def test_palette_disabled_and_empty_never_execute():
    app = App()
    selected = []
    async with app.run_test() as pilot:
        screen = CommandPaletteScreen([PaletteItem("blocked", lambda: selected.append(1), disabled_reason="No file")])
        app.push_screen(screen, lambda cb: cb() if cb else None)
        await pilot.pause()
        await pilot.press("enter")
        assert not selected
        screen.query_one("#palette-input", Input).value = "zzzz"
        await pilot.pause()
        await pilot.press("enter")
        assert app.screen is screen and not selected


async def test_hearth_theme_pins_palette_and_hidden_motion(tmp_path):
    app = HearthApp(tmp_path, sessions=[], preferences=tmp_path / "prefs.yaml")
    async with app.run_test(size=(80, 30)) as pilot:
        app.action_pin_project()
        assert read_state(tmp_path)["pinned_projects"] == [str(tmp_path)]
        await pilot.press("ctrl+t")
        assert app.theme == "norse-light"
        frame = app._frame
        app._animate_ember()
        assert app._frame == frame
        await pilot.press("ctrl+shift+p")
        assert isinstance(app.screen, CommandPaletteScreen)


async def test_task_draft_does_not_restore_edit_permission(tmp_path):
    update_state(tmp_path, task_draft={"objective": "draft objective", "files": "a.py", "verify": ""})
    app = HearthApp(tmp_path, sessions=[], preferences=tmp_path / "prefs.yaml")
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.press("ctrl+n")
        await pilot.pause()
        assert isinstance(app.screen, TaskScreen)
        assert app.screen.query_one("#objective", TextArea).text == "draft objective"
        assert not app.screen.query_one("#allow-edit", Checkbox).value
        assert "Registrar" in str(app.screen.query_one("#accept", Button).label)
        assert app.screen.query_one("#activity").region.height > 1
        await pilot.press("f7")
        assert isinstance(app.screen, InspectorScreen)
