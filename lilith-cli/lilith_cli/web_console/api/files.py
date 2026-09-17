from __future__ import annotations

from pathlib import Path, PureWindowsPath

import anyio
from fastapi import APIRouter, HTTPException, Request

router = APIRouter()

IGNORE_DIRS = {
    ".git", ".venv", "node_modules", "__pycache__", ".pytest_cache",
    "dist", ".next", ".lilith", ".hermes",
}
SENSITIVE_FILES = {"lilith_memory.db"}


def _workspace(request: Request) -> Path:
    return Path(request.app.state.workspace).resolve()


def _safe_path(base: Path, raw_path: str, *, must_exist: bool = False) -> Path:
    if not isinstance(raw_path, str) or not raw_path or "\x00" in raw_path:
        raise HTTPException(status_code=400, detail="Invalid path")
    candidate_input = Path(raw_path)
    if candidate_input.is_absolute() or PureWindowsPath(raw_path).is_absolute():
        raise HTTPException(status_code=403, detail="Path escapes workspace")
    try:
        candidate = (base / candidate_input).resolve(strict=must_exist)
        candidate.relative_to(base)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=403, detail="Path escapes workspace") from exc
    relative = candidate.relative_to(base)
    if any(part in IGNORE_DIRS for part in relative.parts):
        raise HTTPException(status_code=403, detail="Private path is not exposed")
    if candidate.name in SENSITIVE_FILES:
        raise HTTPException(status_code=403, detail="Private file is not exposed")
    return candidate


@router.get("")
async def list_files(request: Request) -> list[dict[str, str]]:
    base_path = _workspace(request)
    rows: list[dict[str, str]] = []
    for path in base_path.rglob("*"):
        relative = path.relative_to(base_path)
        if path.is_symlink() or any(part in IGNORE_DIRS for part in relative.parts):
            continue
        if path.is_file() and path.name not in SENSITIVE_FILES:
            rows.append({"path": relative.as_posix(), "type": "file"})
            if len(rows) >= 5000:
                break
    return rows


@router.get("/{path:path}")
async def read_file_content(path: str, request: Request) -> dict[str, str]:
    file_path = _safe_path(_workspace(request), path, must_exist=True)
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    content = await anyio.to_thread.run_sync(
        lambda: file_path.read_text(encoding="utf-8", errors="replace")
    )
    return {"content": content[:2_000_000]}


@router.post("/upload")
@router.put("/{path:path}")
@router.delete("/{path:path}")
async def file_mutation_is_disabled(path: str = "") -> None:
    raise HTTPException(
        status_code=403,
        detail="File mutations must go through Lilith's canonical agent/tool policy.",
    )
