import asyncio
import json
from pathlib import Path
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import yaml
from textual.widgets import Input, TextArea, Checkbox, Button, TabbedContent, OptionList, Select

from lilith_cli.hearth_ui import HearthApp
from lilith_cli.hearth_screens import TaskScreen, MemoryScreen
from lilith_cli.task_workspace import TaskRun, TaskSpec, atomic_json


@pytest.mark.asyncio
@pytest.mark.parametrize("control", [None, "pause", "cancel"])
async def test_ui_executes_tool_checks_diff_and_accepts(tmp_path, monkeypatch, control):
    """Full UI -> child CLI -> HTTP transport -> file tool -> tests -> review."""
    target = tmp_path / "module.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    monkeypatch.setenv("YGGDRASIL_PROVIDER_HEALTH_DB", str(tmp_path / "health.sqlite3"))
    monkeypatch.setenv("LILITH_CONVERSATIONS_DIR", str(tmp_path / "conversations"))
    requests = []
    request_started = threading.Event()
    release_response = threading.Event()
    if control is None:
        release_response.set()
    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(payload)
            request_started.set()
            release_response.wait(timeout=5)
            if len(requests) == 1:
                delta = {"tool_calls": [{"index": 0, "id": "write", "type": "function",
                    "function": {"name": "file_write", "arguments": json.dumps({"path": "module.py", "content": "VALUE = 2\n"})}}]}
                finish = "tool_calls"
            else:
                assert any(message.get("role") == "tool" for message in payload["messages"])
                delta, finish = {"content": "Actualicé VALUE a 2."}, "stop"
            body = ("data: " + json.dumps({"choices": [{"delta": delta, "finish_reason": finish}]}) + '\n\ndata: {"choices":[],"usage":{"prompt_tokens":20,"completion_tokens":5,"total_tokens":25}}\n\ndata: [DONE]\n\n').encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.safe_dump({"provider": "local", "model": "fixture", "base_url": f"http://127.0.0.1:{server.server_port}/v1",
        "memory": {"enabled": False}, "retry_max": 0}), encoding="utf-8")
    app = HearthApp(tmp_path, sessions=[], preferences=tmp_path / "prefs.yaml")
    screen = TaskScreen(tmp_path, runner_factory=lambda spec: TaskRun(spec, directory=tmp_path / "run", config_path=cfg))
    try:
        async with app.run_test(size=(130, 48)) as pilot:
            await app.push_screen(screen)
            screen.query_one("#objective", TextArea).load_text("Cambia VALUE a 2 en module.py")
            screen.query_one("#task-files", Input).value = "module.py"
            exe = sys.executable.replace("\\", "/")
            screen.query_one("#verify-command", Input).value = f'"{exe}" -c "import module; assert module.VALUE == 2; print(123)"'
            screen.query_one("#allow-edit", Checkbox).value = True
            screen.execute()
            if control:
                for _ in range(80):
                    await pilot.pause(0.05)
                    if request_started.is_set():
                        break
                assert request_started.is_set()
                screen.pause() if control == "pause" else screen.cancel()
                await pilot.pause(0.3)
                release_response.set()
            for _ in range(150):
                await pilot.pause(0.1)
                if screen.run is not None and screen.run.result:
                    break
            if control == "cancel":
                assert screen.run.result["status"] == "cancelled", screen.run.result
                assert target.read_text() == "VALUE = 1\n"
                return
            if control == "pause":
                assert screen.run.result["status"] == "paused", screen.run.result
                before_resume = target.stat().st_mtime_ns
                screen.resume_task()
                for _ in range(150):
                    await pilot.pause(0.1)
                    if not screen.busy and screen.run.result.get("status") == "verified":
                        break
                assert target.stat().st_mtime_ns == before_resume
            assert screen.run.result["status"] == "verified", screen.run.result
            reopened = TaskRun.reopen(tmp_path / "run/request.json")
            assert reopened.result["status"] == "verified"
            assert reopened.spec.files == ["module.py"]
            assert screen.run.result["usage"]["total_tokens"] == 50
            assert target.read_text() == "VALUE = 2\n"
            assert "-VALUE = 1" in screen.query_one("#diff-view", TextArea).text
            assert screen.query_one("#accept", Button).disabled
            screen.query_one("#work-tabs", TabbedContent).active = "changes-tab"
            await pilot.pause()
            assert not screen.query_one("#accept", Button).disabled
            screen.accept()
            assert json.loads((tmp_path / "run/review.json").read_text())["decision"] == "accepted"
    finally:
        release_response.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.asyncio
async def test_memory_screen_edits_and_forgets_in_selected_scope(tmp_path, monkeypatch):
    from lilith_cli.work_memory import records
    monkeypatch.setenv("LILITH_PREFERENCES_DB", str(tmp_path / "memory.sqlite3"))
    app = HearthApp(tmp_path, sessions=[], preferences=tmp_path / "prefs.yaml")
    async with app.run_test(size=(100, 36)) as pilot:
        screen = MemoryScreen(tmp_path)
        await app.push_screen(screen)
        screen.query_one("#memory-scope", Select).value = "project"
        screen.query_one("#memory-key", Input).value = "style"
        screen.query_one("#memory-value", TextArea).load_text("Nórdico")
        await screen.save()
        assert records(str(tmp_path))[0]["value"] == "Nórdico"
        assert records() == []
        screen.query_one("#memory-value", TextArea).load_text("Anime")
        await screen.save()
        assert records(str(tmp_path))[0]["value"] == "Anime"
        await screen.forget()
        assert records(str(tmp_path)) == []


def test_acceptance_rejects_changes_after_review(tmp_path):
    target = tmp_path / "code.py"
    target.write_text("before")
    run = TaskRun(TaskSpec(tmp_path, "fix", ["code.py"]), directory=tmp_path / "run")
    target.write_text("candidate")
    assert "candidate" in run.diff()
    target.write_text("concurrent change")
    with pytest.raises(ValueError, match="cambió"):
        run.accept()
    assert not (tmp_path / "run/review.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(130, 48), (80, 30)])
async def test_templates_and_navigation_work_at_both_sizes(tmp_path, size):
    from lilith_cli.task_workspace import TEMPLATES
    app = HearthApp(tmp_path, sessions=[], preferences=tmp_path / "prefs.yaml")
    async with app.run_test(size=size) as pilot:
        await pilot.press("ctrl+n")
        screen = app.screen
        assert isinstance(screen, TaskScreen)
        for key, (_, objective) in TEMPLATES.items():
            screen.query_one("#template", Select).value = key
            await pilot.pause()
            assert screen.query_one("#objective", TextArea).text == objective
            assert screen.query_one("#allow-edit", Checkbox).value is False  # Template selection never grants edit authority.
        assert screen.query_one("#task-actions").region.width <= size[0]
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, TaskScreen)
        await pilot.press("ctrl+q")


@pytest.mark.asyncio
async def test_picker_selects_files_and_skips_runtime_directories(tmp_path):
    from lilith_cli.hearth_screens import FilePicker, WorkspaceTree
    from types import SimpleNamespace
    source = tmp_path / "source.py"
    source.write_text("pass")
    vendor = tmp_path / ".venv"
    vendor.mkdir()
    assert list(WorkspaceTree.filter_paths(None, [source, vendor])) == [source]
    app = HearthApp(tmp_path, sessions=[], preferences=tmp_path / "prefs.yaml")
    async with app.run_test(size=(100, 36)) as pilot:
        await pilot.press("ctrl+n")
        screen = app.screen
        screen.browse()
        await pilot.pause()
        picker = app.screen
        assert isinstance(picker, FilePicker)
        picker.selected_file(SimpleNamespace(path=source))
        await pilot.click("#choose")
        await pilot.pause()
        assert screen.query_one("#task-files", Input).value == "source.py"
