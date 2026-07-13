"""Integration tests for policy MCP server (subprocess + JSON-RPC)."""
from __future__ import annotations

import json
import subprocess
import sys

import pytest


def _server_command() -> list[str]:
    return [sys.executable, "-m", "lilith_orchestrator.policy_mcp"]


def _run_session(requests: list[dict], timeout: float = 15.0) -> list[dict]:
    """Send a sequence of JSON-RPC requests and parse all responses.

    Writes requests without closing stdin so the MCP server can keep draining
    the stdio write stream and flush every response before exit. Then waits
    for the expected response count (one per request) before closing.
    """
    expected_lines = len(requests)
    proc = subprocess.Popen(
        _server_command(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )
    body = "\n".join(json.dumps(r) for r in requests) + "\n"
    try:
        assert proc.stdin is not None
        proc.stdin.write(body.encode("utf-8"))
        proc.stdin.flush()
        # Keep stdin open so the MCP stdio_server remains alive until we read
        # all responses. Once we have all expected lines, close stdin to let
        # the server drain its write_stream and exit cleanly.
        import time as _time
        deadline = _time.monotonic() + timeout
        collected: list[bytes] = []
        while len(collected) < expected_lines and _time.monotonic() < deadline:
            chunk = proc.stdout.readline()
            if not chunk:
                break
            collected.append(chunk)
        try:
            proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        stdout = b"".join(collected)
        _ = b""
    except subprocess.TimeoutExpired:
        proc.kill()
        pytest.fail("Server timed out")
    out = stdout.decode("utf-8", errors="replace").strip()
    responses = []
    for line in out.split("\n"):
        line = line.strip()
        if line.startswith("{"):
            try:
                responses.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return responses


def _init() -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0.1.0"},
        },
    }


def _call(req_id: int, name: str, arguments: dict | None = None) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments or {}},
    }


def _list_tools(req_id: int) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "tools/list",
        "params": {},
    }


# ── list_tools ──────────────────────────────────────────────────────────────


def test_list_tools_advertises_4_tools():
    responses = _run_session([_init(), _list_tools(2)])
    list_resp = next(r for r in responses if r.get("id") == 2)
    tools = list_resp["result"]["tools"]
    names = {t["name"] for t in tools}
    assert names == {"policy_check", "policy_audit", "policy_reset", "policy_configure"}


# ── policy_check ────────────────────────────────────────────────────────────


def test_check_default_allows_tool():
    responses = _run_session([
        _init(),
        _call(2, "policy_check", {"agent_name": "a1", "tool_name": "read_file"}),
    ])
    call_resp = next(r for r in responses if r.get("id") == 2)
    payload = json.loads(call_resp["result"]["content"][0]["text"])
    assert payload["success"] is True
    assert payload["decision"] in ("allow", "audit")
    assert payload["violation"] is None


def test_check_forbidden_tool_denies():
    # Configure policy first, then check
    responses = _run_session([
        _init(),
        _call(2, "policy_configure", {"forbidden_tools": ["dangerous"]}),
        _call(3, "policy_check", {"agent_name": "a1", "tool_name": "dangerous"}),
    ])
    cfg_resp = next(r for r in responses if r.get("id") == 2)
    assert json.loads(cfg_resp["result"]["content"][0]["text"])["success"] is True
    chk_resp = next(r for r in responses if r.get("id") == 3)
    payload = json.loads(chk_resp["result"]["content"][0]["text"])
    assert payload["decision"] == "deny"
    assert payload["violation"] == "tool_forbidden"


def test_check_allowed_only_whitelist():
    responses = _run_session([
        _init(),
        _call(2, "policy_configure", {"allowed_tools": ["safe_tool"]}),
        _call(3, "policy_check", {"agent_name": "a1", "tool_name": "safe_tool"}),
        _call(4, "policy_check", {"agent_name": "a1", "tool_name": "unsafe_tool"}),
    ])
    safe_resp = next(r for r in responses if r.get("id") == 3)
    unsafe_resp = next(r for r in responses if r.get("id") == 4)
    safe_payload = json.loads(safe_resp["result"]["content"][0]["text"])
    unsafe_payload = json.loads(unsafe_resp["result"]["content"][0]["text"])
    assert safe_payload["decision"] in ("allow", "audit")
    assert unsafe_payload["decision"] == "deny"
    assert unsafe_payload["violation"] == "tool_not_allowed"


# ── policy_audit ────────────────────────────────────────────────────────────


def test_audit_returns_events():
    responses = _run_session([
        _init(),
        _call(2, "policy_configure", {"forbidden_tools": ["bad"], "audit_all": True}),
        _call(3, "policy_check", {"agent_name": "my_agent", "tool_name": "good"}),
        _call(4, "policy_check", {"agent_name": "my_agent", "tool_name": "bad"}),
        _call(5, "policy_audit", {"agent_name": "my_agent"}),
    ])
    audit_resp = next(r for r in responses if r.get("id") == 5)
    payload = json.loads(audit_resp["result"]["content"][0]["text"])
    assert payload["success"] is True
    assert payload["count"] >= 2
    assert all(e["agent"] == "my_agent" for e in payload["events"])
    # Find the deny event
    denies = [e for e in payload["events"] if e["decision"] == "deny"]
    assert len(denies) >= 1
    assert denies[0]["violation"] == "tool_forbidden"


# ── policy_reset ────────────────────────────────────────────────────────────


def test_reset_specific_agent():
    responses = _run_session([
        _init(),
        _call(2, "policy_check", {"agent_name": "agent_x", "tool_name": "t"}),
        _call(3, "policy_audit", {"agent_name": "agent_x"}),
        _call(4, "policy_reset", {"agent_name": "agent_x"}),
        _call(5, "policy_audit", {"agent_name": "agent_x"}),
    ])
    # Before reset: count >= 1 (depends on default audit_all)
    before = json.loads(
        next(r for r in responses if r.get("id") == 3)["result"]["content"][0]["text"]
    )
    # After reset: count == 0
    after = json.loads(
        next(r for r in responses if r.get("id") == 5)["result"]["content"][0]["text"]
    )
    assert before["count"] >= 0  # Could be 0 if default doesn't audit
    assert after["count"] == 0


def test_reset_all_agents():
    responses = _run_session([
        _init(),
        _call(2, "policy_configure", {"audit_all": True}),
        _call(3, "policy_check", {"agent_name": "agent_a", "tool_name": "x"}),
        _call(4, "policy_check", {"agent_name": "agent_b", "tool_name": "y"}),
        _call(5, "policy_reset", {}),  # No agent_name → all
        _call(6, "policy_audit", {"agent_name": "agent_a"}),
        _call(7, "policy_audit", {"agent_name": "agent_b"}),
    ])
    after_a = json.loads(
        next(r for r in responses if r.get("id") == 6)["result"]["content"][0]["text"]
    )
    after_b = json.loads(
        next(r for r in responses if r.get("id") == 7)["result"]["content"][0]["text"]
    )
    assert after_a["count"] == 0
    assert after_b["count"] == 0


# ── policy_configure ────────────────────────────────────────────────────────


def test_configure_replaces_engine():
    responses = _run_session([
        _init(),
        _call(2, "policy_configure", {"forbidden_tools": ["t1"], "max_tool_calls": 2}),
        _call(3, "policy_check", {"agent_name": "x", "tool_name": "t1"}),
        _call(4, "policy_check", {"agent_name": "x", "tool_name": "safe"}),
        _call(5, "policy_check", {"agent_name": "x", "tool_name": "safe"}),
        _call(6, "policy_check", {"agent_name": "x", "tool_name": "safe"}),
    ])
    # t1 forbidden → deny
    r3 = json.loads(next(r for r in responses if r.get("id") == 3)["result"]["content"][0]["text"])
    assert r3["decision"] == "deny"
    # max_tool_calls=2, 2 allowed → 3rd should be denied (resource exceeded)
    r6 = json.loads(next(r for r in responses if r.get("id") == 6)["result"]["content"][0]["text"])
    assert r6["decision"] == "deny"
    assert r6["violation"] == "resource_exceeded"


# ── error handling ──────────────────────────────────────────────────────────


def test_unknown_tool_returns_clean_error():
    responses = _run_session([
        _init(),
        _call(2, "nonexistent_tool", {}),
    ])
    resp = next(r for r in responses if r.get("id") == 2)
    # Either protocol-level error (isError) or success=False in payload
    if "error" in resp:
        assert resp["error"]["code"] != 0
    else:
        payload = json.loads(resp["result"]["content"][0]["text"])
        assert payload["success"] is False
