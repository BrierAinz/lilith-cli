import pytest
from rich.console import Console
from rich.text import Text
from rich.console import Group

from lilith_cli.render import (
    render_diff,
    summarize_tool_result,
    render_tool_line,
    _extract_data
)

def test_render_diff_normal():
    diff = """--- render.py
+++ render.py
@@ -1,3 +1,3 @@
 def foo():
-    pass
+    return True
"""
    result = render_diff(diff, "render.py")
    assert isinstance(result, Group)
    # Group renderables contain the lines
    text_content = "".join([t.plain if hasattr(t, "plain") else "" for t in result.renderables])
    assert "render.py" in text_content
    assert "+1" in text_content
    assert "return True" in text_content
    assert "pass" in text_content

def test_render_diff_empty():
    diff = ""
    result = render_diff(diff)
    assert result == ""

def test_render_diff_not_a_diff():
    text = "Hello world\nThis is not a diff"
    result = render_diff(text)
    assert result == text

def test_render_diff_no_newline():
    diff = r"""--- a
+++ b
@@ -1 +1 @@
-a
\ No newline at end of file
+b
\ No newline at end of file
"""
    result = render_diff(diff)
    text_content = "".join([t.plain if hasattr(t, "plain") else "" for t in result.renderables])
    assert "\\ No newline" not in text_content

def test_render_diff_very_long():
    # Make a diff with 6 hunks to trigger clipping
    diff = "--- file\n+++ file\n"
    for i in range(6):
        diff += f"@@ -{i*10},1 +{i*10},1 @@\n-old\n+new\n"
    
    result = render_diff(diff)
    text_content = "".join([t.plain if hasattr(t, "plain") else "" for t in result.renderables])
    assert "líneas omitidas" in text_content

def test_render_diff_garbage_no_crash():
    try:
        render_diff(None)
        assert True
    except Exception:
        pytest.fail("render_diff crashed on None")

def test_summarize_tool_result_cases():
    # file_read
    res = summarize_tool_result("file_read", "Líneas 1-31 de 505:\ncontent", {"path": "src/render.py"})
    assert res == "render.py — líneas 1-31 de 505"
    
    # grep_files
    data = [{"file": "a.py"}, {"file": "a.py"}, {"file": "b.py"}]
    import json
    res = summarize_tool_result("grep_files", json.dumps(data))
    assert res == "3 coincidencias en 2 ficheros"
    
    # directory_list
    data = [1, 2, 3]
    res = summarize_tool_result("directory_list", json.dumps(data))
    assert res == "3 entradas"
    
    # batch_edit
    data = {"edits": [{"path": "a.py"}, {"path": "b.py"}, {"path": "a.py"}]}
    res = summarize_tool_result("batch_edit", json.dumps(data))
    assert res == "3 ediciones, 2 ficheros"
    
    # coding
    data = {"returncode": 0}
    res = summarize_tool_result("coding", json.dumps(data))
    assert "exit 0" in res

    # todo_list
    data = {"todos": [{"done": True}, {"done": False}]}
    res = summarize_tool_result("todo_list", json.dumps(data))
    assert res == "2 tareas (1 pendiente)"  # concordancia: 1 pendiente, no pendientes
    
    # bg_status
    data = {"processes": [{"alive": True}, {"alive": False}]}
    res = summarize_tool_result("bg_status", json.dumps(data))
    assert res == "1 proceso vivo de 2"  # concordancia: un proceso, no procesos
    
    data = {"name": "test", "status": "running", "alive": True}
    res = summarize_tool_result("bg_status", json.dumps(data))
    assert res == "proceso test: vivo"

    # cli_jobs_recent
    data = {"references": [1, 2, 3]}
    res = summarize_tool_result("cli_jobs_recent", json.dumps(data))
    assert res == "3 delegaciones recientes"

    # watch_status
    data = {"watches": [{"event_count": 2}, {"event_count": 3}]}
    res = summarize_tool_result("watch_status", json.dumps(data))
    assert res == "2 watchers activos (5 eventos)"

    # cli_job_reference
    data = {"agent": "Vor", "job_id": "123", "status": "running"}
    res = summarize_tool_result("cli_job_reference", json.dumps(data))
    assert res == "Vor 123 — running"
    
    data = {"agent": "Huginn", "job_id": "456", "job_returncode": 0}
    res = summarize_tool_result("cli_job_inspect", json.dumps(data))
    assert res == "Huginn 456 — completado con 0"

    # Default fallback json dict (matches key)
    data = {"events": [1, 2, 3, 4]}
    res = summarize_tool_result("unknown_tool", json.dumps(data))
    assert res == "4 events"
    
    # Default fallback json dict (count key)
    data = {"count": 42}
    res = summarize_tool_result("unknown_tool", json.dumps(data))
    assert res == "count: 42"
    
    # Default fallback json dict (keys)
    data = {"a": 1, "b": 2, "c": 3}
    res = summarize_tool_result("unknown_tool", json.dumps(data))
    assert res == "objeto con 3 claves"
    
    # Default fallback json list
    data = [1, 2, 3, 4, 5]
    res = summarize_tool_result("unknown_tool", json.dumps(data))
    assert res == "lista de 5 elementos"

def test_summarize_tool_result_structured_fallback_prefers_evidence():
    import json

    assert summarize_tool_result("unknown_tool", json.dumps({"status": "ok", "items": [1, 2]})) == "2 items"
    assert summarize_tool_result("unknown_tool", json.dumps({"total": 7, "status": "ok"})) == "total: 7"
    assert summarize_tool_result("unknown_tool", json.dumps({"status": "ready", "message": "done"})) == "status: ready"


def test_summarize_tool_result_invalid_json():
    res = summarize_tool_result("batch_edit", "{not valid json}")
    assert res == "{not valid json}"


def test_summarize_tool_result_json_scalars_and_unusual_content_do_not_crash():
    assert summarize_tool_result("unknown_tool", "null") == "(sin salida)"
    assert summarize_tool_result("unknown_tool", "42") == "valor estructurado: 42"
    assert summarize_tool_result("unknown_tool", "\x00\x01") == "\x00\x01"

def test_summarize_tool_result_empty():
    res = summarize_tool_result("coding", "")
    assert res == "(sin salida)"

def test_summarize_tool_result_long_line():
    long_line = "a" * 100
    res = summarize_tool_result("default", long_line)
    assert len(res) == 61
    assert res.endswith("…")

def test_summarize_tool_result_no_crash():
    try:
        summarize_tool_result("coding", None)
        assert True
    except Exception:
        pytest.fail("summarize_tool_result crashed on None")

def test_render_tool_line():
    # Just checking it doesn't crash
    try:
        render_tool_line("test_tool", "summary", duration=None)
        render_tool_line("test_tool", "summary", duration=0.5)
        assert True
    except Exception:
        pytest.fail("render_tool_line crashed")
