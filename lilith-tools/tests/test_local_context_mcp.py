"""Integration tests for local_context MCP server.

Uses subprocess + JSON-RPC over stdio (no async client) to verify
the MCP server speaks the protocol correctly. This avoids pytest-asyncio
+ anyio cancel scope issues that occur with the async client.

Each test spawns a fresh server subprocess, sends its request(s),
and parses the response(s). We keep stdin open while reading so the
MCP server's stdio transport can drain its write stream and flush
every response before we close the pipe (Windows stdio EOF otherwise
causes late responses to be lost).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


def _server_command() -> list[str]:
    return [sys.executable, "-m", "lilith_tools.local_context_mcp"]


def _send_request(request: dict, timeout: float = 10.0) -> dict | None:
    """Spawn server, send one JSON-RPC request, parse one response, kill server."""
    proc = subprocess.Popen(
        _server_command(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )
    try:
        assert proc.stdin is not None
        proc.stdin.write((json.dumps(request) + "\n").encode("utf-8"))
        proc.stdin.flush()
        # Read lines until we hit one JSON object (the response) or timeout.
        deadline = time.monotonic() + timeout
        lines: list[bytes] = []
        while time.monotonic() < deadline:
            chunk = proc.stdout.readline()
            if not chunk:
                break
            stripped = chunk.strip()
            if stripped.startswith(b"{"):
                lines.append(chunk)
                break  # first JSON response is what we need
        try:
            proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        stdout = b"".join(lines)
    except subprocess.TimeoutExpired:
        proc.kill()
        return None
    for line in stdout.decode("utf-8", errors="replace").split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None


def _send_requests(requests: list[dict], timeout: float = 15.0) -> list[dict]:
    """Spawn server, send N JSON-RPC requests, parse the responses.

    Notifications (no "id" field) produce no response. The previous
    implementation read exactly len(requests) lines from stdout, which
    caused a 3rd readline() to block forever on Windows because the
    notification has no reply and the MCP stdio server does not close
    its write end on its own.

    Fix: send the request body through communicate() so it writes,
    closes stdin, and drains stdout without flushing a closed pipe.
    Fall back to kill() if the server does not exit in time.
    """
    creationflags = 0
    if sys.platform == "win32":
        # CREATE_NEW_PROCESS_GROUP so kill() reliably cleans up the child
        creationflags = 0x00000200
    proc = subprocess.Popen(
        _server_command(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=creationflags,
    )
    body = "\n".join(json.dumps(r) for r in requests) + "\n"
    raw = b""
    try:
        assert proc.stdin is not None and proc.stdout is not None
        try:
            stdout_bytes, _ = proc.communicate(input=body.encode("utf-8"), timeout=timeout)
            raw = stdout_bytes or b""
        except subprocess.TimeoutExpired:
            # Server is stuck; force-kill and drain whatever it produced.
            proc.kill()
            try:
                stdout_bytes, _ = proc.communicate(timeout=2)
                raw = stdout_bytes or b""
            except subprocess.TimeoutExpired:
                raw = b""
    finally:
        if proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass
    responses: list[dict] = []
    for line in raw.decode("utf-8", errors="replace").split("\n"):
        line = line.strip()
        if line.startswith("{"):
            try:
                responses.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return responses


# ── Initialize handshake ────────────────────────────────────────────────────


def test_initialize_handshake():
    """Server should respond to initialize with capabilities."""
    req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0.1.0"},
        },
    }
    resp = _send_request(req)
    assert resp is not None, "No response from server"
    assert resp.get("id") == 1
    assert "result" in resp
    assert "capabilities" in resp["result"]
    assert "serverInfo" in resp["result"]
    assert resp["result"]["serverInfo"]["name"] == "lilith-local-context"


def test_initialized_notification_no_error():
    """Sending 'initialized' notification should not crash the server.

    After the notification the server should still respond to a follow-up
    tools/list call. Uses the multi-request helper so the list response
    is reliably delivered before stdin closes.
    """
    init = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0.1.0"},
        },
    }
    notif = {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
    list_req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    responses = _send_requests([init, notif, list_req])
    # Find the tools/list response (id=2). The notification has no id and
    # produces no response, so we expect exactly one reply (for list_req).
    tools_resp = next((r for r in responses if r.get("id") == 2), None)
    assert tools_resp is not None, f"Missing tools/list response. Got: {responses}"
    assert "result" in tools_resp
    tools = tools_resp["result"]["tools"]
    names = {t["name"] for t in tools}
    assert "local_python_info" in names
    assert "local_processes" in names
    assert "local_git_status" in names
    assert "local_env" in names


# ── tools/call roundtrip ────────────────────────────────────────────────────


def _init_and_call(tool_name: str, arguments: dict | None = None):
    """Helper: initialize, send notifications/initialized, then tools/call.

    The MCP server requires the initialized notification before processing
    tools/call — sending only initialize hangs the server indefinitely.
    """
    init = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                   "clientInfo": {"name": "t", "version": "0"}},
    }
    notif = {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
    call = {
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments or {}},
    }
    return _send_requests([init, notif, call])


def test_call_python_info():
    """local_python_info returns Python interpreter info."""
    responses = _init_and_call("local_python_info")
    call_resp = next((r for r in responses if r.get("id") == 2), None)
    assert call_resp is not None, f"No tools/call response. Got: {responses}"
    assert "result" in call_resp
    content = call_resp["result"]["content"]
    assert len(content) >= 1
    payload = json.loads(content[0]["text"])
    assert payload["success"] is True
    assert "executable" in payload["data"]
    assert "version" in payload["data"]


def test_call_disk_usage():
    """local_disk_usage returns disk usage."""
    responses = _init_and_call("local_disk_usage", {"path": os.getcwd()})
    call_resp = next((r for r in responses if r.get("id") == 2), None)
    assert call_resp is not None, f"No tools/call response. Got: {responses}"
    payload = json.loads(call_resp["result"]["content"][0]["text"])
    assert payload["success"] is True
    assert payload["data"]["free_bytes"] > 0


def test_call_unknown_tool_returns_clean_error():
    """Unknown tool should return success=False with descriptive error."""
    responses = _init_and_call("nonexistent_tool")
    call_resp = next((r for r in responses if r.get("id") == 2), None)
    assert call_resp is not None, f"No tools/call response. Got: {responses}"
    payload = json.loads(call_resp["result"]["content"][0]["text"])
    assert payload["success"] is False
    assert "unknown tool" in payload["error"]


def test_call_env_masks_secrets():
    """local_env should mask values for keys with KEY/TOKEN/SECRET/PASSWORD."""
    # Set the env var in the test process before spawning so the
    # subprocess inherits it.
    os.environ["LILITH_MCP_TEST_KEY"] = "supersecretvalue"
    try:
        responses = _init_and_call("local_env", {"name": "LILITH_MCP_TEST_KEY"})
        call_resp = next((r for r in responses if r.get("id") == 2), None)
        assert call_resp is not None, f"No tools/call response. Got: {responses}"
        payload = json.loads(call_resp["result"]["content"][0]["text"])
        assert payload["success"] is True
        assert payload["data"]["value"] == "***MASKED***"
    finally:
        os.environ.pop("LILITH_MCP_TEST_KEY", None)


def test_call_git_status(tmp_path):
    """local_git_status works against a real git repo."""
    subprocess.run(["git", "init"], cwd=str(tmp_path), check=True, capture_output=True)
    (tmp_path / "a.txt").write_text("hi")
    responses = _init_and_call("local_git_status", {"path": str(tmp_path)})
    call_resp = next((r for r in responses if r.get("id") == 2), None)
    assert call_resp is not None, f"No tools/call response. Got: {responses}"
    payload = json.loads(call_resp["result"]["content"][0]["text"])
    assert payload["success"] is True
    assert payload["data"]["is_repo"] is True
    assert payload["data"]["dirty_count"] >= 1
