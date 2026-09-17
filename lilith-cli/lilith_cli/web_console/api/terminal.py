from __future__ import annotations

from fastapi import APIRouter, WebSocket

router = APIRouter()


@router.websocket("")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """No second shell authority is exposed by the web console."""
    await websocket.close(
        code=4403,
        reason=(
            "Direct terminal access is disabled. Use Lilith chat so commands pass "
            "through the canonical authority, audit and mission layers."
        ),
    )
