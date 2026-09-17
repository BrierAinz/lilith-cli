from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi import HTTPException
from fastapi.testclient import TestClient
from lilith_cli.web_console.api.files import _safe_path
from lilith_cli.web_console.server import create_app
from starlette.websockets import WebSocketDisconnect

TOKEN = "test-token-not-a-secret"


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(
        workspace=str(tmp_path),
        auth_token=TOKEN,
        allowed_origins=["http://localhost:12356"],
    ))


def test_health_is_canonical_and_file_api_requires_token(tmp_path: Path) -> None:
    client = _client(tmp_path)
    health = client.get("/api/health/")
    assert health.status_code == 200
    assert health.json()["surface"] == "canonical_lilith_web_console"
    assert health.json()["runtime"] == "SessionRuntime"
    assert client.get("/api/files").status_code == 401
    assert client.get("/api/files", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200


def test_private_state_and_workspace_escape_are_blocked(tmp_path: Path) -> None:
    (tmp_path / "visible.txt").write_text("visible", encoding="utf-8")
    (tmp_path / ".lilith").mkdir()
    (tmp_path / ".lilith" / "secret.txt").write_text("private", encoding="utf-8")
    response = _client(tmp_path).get(
        "/api/files", headers={"Authorization": f"Bearer {TOKEN}"}
    )
    assert response.json() == [{"path": "visible.txt", "type": "file"}]
    with pytest.raises(HTTPException, match="Path escapes workspace"):
        _safe_path(tmp_path.resolve(), "../outside.txt")


def test_websocket_rejects_missing_token(tmp_path: Path) -> None:
    with (
        pytest.raises(WebSocketDisconnect) as caught,
        _client(tmp_path).websocket_connect("/api/chat"),
    ):
        pass
    assert caught.value.code == 4401


class _Provider:
    async def close(self) -> None:
        return None


class _Session:
    def __init__(self) -> None:
        self.provider = _Provider()
        self.system_prompt = ""

    async def process_message_stream(self, text: str):
        yield {"type": "text", "content": f"echo:{text}"}
        yield {"type": "done", "content": f"echo:{text}", "usage": {"total_tokens": 3}}


def test_authenticated_chat_uses_canonical_runtime(tmp_path: Path, monkeypatch) -> None:
    import lilith_cli.agent_console as console_module
    import lilith_cli.session_runtime as runtime_module

    monkeypatch.setattr(runtime_module, "create_session", lambda _cfg: _Session())
    monkeypatch.setattr(console_module, "prepare_console", lambda session, root: None)

    client = _client(tmp_path)
    with client.websocket_connect(
        "/api/chat",
        headers={"origin": "http://localhost:12356"},
        subprotocols=["lilith-auth", TOKEN],
    ) as socket:
        socket.send_json({"type": "chat", "content": "hello"})
        assert socket.receive_json() == {"type": "status", "status": "running"}
        result = socket.receive_json()
    assert result["type"] == "chat_result"
    assert result["content"] == "echo:hello"
    assert result["usage"]["total_tokens"] == 3
