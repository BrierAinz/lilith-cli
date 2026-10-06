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


def _tokenless_loopback_client(tmp_path: Path, monkeypatch, host: str) -> TestClient:
    monkeypatch.delenv("LILITH_AUTH_TOKEN", raising=False)
    return TestClient(
        create_app(workspace=str(tmp_path), allowed_origins=["http://localhost:12356"]),
        headers={"host": host},
        client=("127.0.0.1", 50000),
    )


@pytest.mark.parametrize("host", ["127.0.0.1:12356", "localhost:12356", "[::1]:12356"])
def test_tokenless_loopback_accepts_local_host_header(
    tmp_path: Path, monkeypatch, host: str
) -> None:
    client = _tokenless_loopback_client(tmp_path, monkeypatch, host)
    assert client.get("/api/files").status_code == 200
    response = client.get("/api/files", headers={"origin": "http://localhost:5173"})
    assert response.status_code == 200


def test_tokenless_loopback_rejects_dns_rebinding_host(tmp_path: Path, monkeypatch) -> None:
    """A rebinding page reaches 127.0.0.1, but its Host header is foreign."""
    (tmp_path / "source.py").write_text("secret = 1\n", encoding="utf-8")
    client = _tokenless_loopback_client(tmp_path, monkeypatch, "attacker.example:12356")
    assert client.get("/api/files").status_code == 401
    assert client.get("/api/files/source.py").status_code == 401


def test_tokenless_loopback_rejects_foreign_origin(tmp_path: Path, monkeypatch) -> None:
    client = _tokenless_loopback_client(tmp_path, monkeypatch, "127.0.0.1:12356")
    response = client.get("/api/files", headers={"origin": "https://attacker.example"})
    assert response.status_code == 401


def test_tokenless_websocket_rejects_dns_rebinding_host(tmp_path: Path, monkeypatch) -> None:
    client = _tokenless_loopback_client(tmp_path, monkeypatch, "attacker.example:12356")
    with (
        pytest.raises(WebSocketDisconnect) as caught,
        client.websocket_connect("/api/terminal"),
    ):
        pass
    assert caught.value.code == 4401


def test_web_dev_reload_uses_an_import_string_factory(tmp_path: Path, monkeypatch) -> None:
    """Regression: uvicorn exits when ``reload=True`` gets an app object."""
    import uvicorn
    from lilith_cli.web_console import server
    from lilith_cli.web_console.serve import web

    for name in ("LILITH_WEB_WORKSPACE", "LILITH_WEB_ORIGINS", "LILITH_WEB_CONFIG"):
        monkeypatch.delenv(name, raising=False)
    calls: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: calls.append((args, kwargs)))

    web(root=str(tmp_path), dev=True)

    (args, kwargs), = calls
    assert args == ("lilith_cli.web_console.server:create_app_from_env",)
    assert kwargs["factory"] is True and kwargs["reload"] is True
    app = server.create_app_from_env()
    assert app.state.workspace == tmp_path.resolve()


def test_web_without_dev_passes_the_app_without_reload(tmp_path: Path, monkeypatch) -> None:
    import uvicorn
    from fastapi import FastAPI
    from lilith_cli.web_console.serve import web

    calls: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: calls.append((args, kwargs)))

    web(root=str(tmp_path))

    (args, kwargs), = calls
    assert isinstance(args[0], FastAPI)
    assert not kwargs.get("reload")


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
