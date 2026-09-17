from __future__ import annotations

import asyncio
import subprocess

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


async def _git(request: Request, *args: str) -> dict[str, str]:
    workspace = str(request.app.state.workspace)

    def run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=workspace,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    result = await asyncio.to_thread(run)
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=result.stderr[-2000:])
    return {"output": result.stdout[-20000:]}


@router.get("/status")
async def git_status(request: Request) -> dict[str, str]:
    return await _git(request, "status", "--short", "--branch")


@router.get("/log")
async def git_log(request: Request) -> dict[str, str]:
    return await _git(request, "log", "--oneline", "-n", "50")


@router.get("/diff")
async def git_diff(request: Request) -> dict[str, str]:
    return await _git(request, "diff", "--")


@router.post("/commit")
@router.post("/push")
async def mutating_git_is_disabled() -> None:
    raise HTTPException(
        status_code=403,
        detail="Git mutations must go through Lilith's canonical agent/tool policy.",
    )
