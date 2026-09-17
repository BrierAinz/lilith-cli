from __future__ import annotations

import platform
from datetime import UTC, datetime

from fastapi import APIRouter, Request

from lilith_cli import __version__

router = APIRouter()
start_time = datetime.now(UTC)


@router.get("/")
async def health_check(request: Request) -> dict:
    uptime = datetime.now(UTC) - start_time
    return {
        "status": "ok",
        "version": __version__,
        "surface": "canonical_lilith_web_console",
        "runtime": "SessionRuntime",
        "workspace": str(request.app.state.workspace),
        "uptime": str(uptime).split(".")[0],
        "python": platform.python_version(),
        "platform": platform.system(),
    }
