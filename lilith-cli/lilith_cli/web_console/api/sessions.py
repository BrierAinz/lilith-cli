from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.get("")
async def list_sessions() -> list[dict[str, str]]:
    """List canonical saved conversations without exposing their content."""
    from lilith_cli.repl import _CONVERSATIONS_DIR

    root = Path(_CONVERSATIONS_DIR)
    if not root.exists():
        return []
    rows: list[dict[str, str]] = []
    for path in sorted(root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        rows.append({"id": path.stem, "file": path.name})
        if len(rows) >= 100:
            break
    return rows


@router.post("")
@router.delete("/{session_id}")
async def session_mutation_is_disabled(session_id: str = "") -> None:
    raise HTTPException(
        status_code=403,
        detail="Session mutation must go through Lilith's canonical session controls.",
    )
