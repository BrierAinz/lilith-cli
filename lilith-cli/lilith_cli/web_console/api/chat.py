from __future__ import annotations

from contextlib import suppress
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.websocket("")
async def websocket_endpoint(websocket: WebSocket) -> None:
    protocols = websocket.headers.get("sec-websocket-protocol", "")
    subprotocol = "lilith-auth" if "lilith-auth" in protocols else None
    await websocket.accept(subprotocol=subprotocol)

    from lilith_cli.agent_console import prepare_console
    from lilith_cli.config import load_config
    from lilith_cli.machine_output import _run_agent_stream
    from lilith_cli.session_runtime import create_session

    cfg = load_config(getattr(websocket.app.state, "config_path", None))
    session = create_session(cfg)
    root = Path(websocket.app.state.workspace).resolve()
    prepare_console(session, root)
    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") != "chat":
                await websocket.send_json({
                    "type": "error",
                    "message": "Only chat messages are accepted by the web console.",
                })
                continue
            content = str(data.get("content") or "").strip()
            if not content:
                await websocket.send_json({"type": "error", "message": "Empty message."})
                continue
            await websocket.send_json({"type": "status", "status": "running"})
            result = await _run_agent_stream(session, content)
            await websocket.send_json({
                "type": "chat_result",
                "content": result.get("response", ""),
                "usage": result.get("usage") or {},
                "tool_errors": result.get("tool_errors") or [],
                "cancelled": bool(result.get("cancelled")),
            })
    except WebSocketDisconnect:
        pass
    finally:
        with suppress(Exception):
            await session.provider.close()
