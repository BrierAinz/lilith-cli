"""Helpers shared by several slash-command modules."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime
from typing import Any

from ..config import CONFIG_DIR
from ..render import console, render_error

# -- Error UX helper ----------------------------------------------------


_ERROR_TIPS = {
    FileNotFoundError: "check the path exists and you have read permissions",
    PermissionError: "try a different file or check permissions",
    IsADirectoryError: "expected a file path, not a directory",
    NotADirectoryError: "expected a directory path, not a file",
    TimeoutError: "the operation took too long, try increasing the timeout",
    ConnectionError: "check your network connection and try again",
    ValueError: "verify the argument format and value range",
    KeyError: "check spelling or see the help text for valid options",
    subprocess.CalledProcessError: "the underlying command failed, check its output above",
    OSError: "check filesystem state and permissions",
}


def _print_error(context, err):
    """Print an error with an optional actionable tip based on the exception type.

    Args:
        context: Short description of what was being attempted.
        err: The exception (or string) that was raised.
    """
    console.print("[error]" + str(context) + ": " + str(err) + "[/error]")
    if isinstance(err, BaseException):
        for exc_type, tip in _ERROR_TIPS.items():
            if isinstance(err, exc_type):
                console.print("[dim]tip: " + tip + "[/dim]")
                break


def _format_history_timestamp(ts: Any) -> str:
    """Return [HH:MM:SS] from an ISO timestamp or [dim]--:--:--[/dim] placeholder.

    Used by /history to render the timestamp prefix. Falls back gracefully
    on malformed or missing timestamps so the command never crashes on bad data.
    """
    if not ts:
        return "--:--:--"
    try:
        if isinstance(ts, datetime):
            dt = ts
        else:
            raw = str(ts).strip()
            # Tolerate trailing Z.
            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            dt = datetime.fromisoformat(raw)
        return dt.strftime("%H:%M:%S")
    except (ValueError, TypeError):
        return "--:--:--"


# ── Shared result printer ─────────────────────────────────────────────


def _print_tool_result(result) -> None:
    """Renderiza el resultado genérico de una herramienta de lilith_tools."""
    if not result.success:
        error = result.error or "Error desconocido ejecutando la herramienta"
        render_error(error)
        return

    data = result.data
    if isinstance(data, dict):
        if "message" in data:
            console.print(f"[success]✓ {data['message']}[/]")
            return
        if "output" in data:
            console.print(data["output"])
            return

    console.print(str(data) if data is not None else "[success]✓ Hecho[/]")

EDITOR_CONFIG_FILE = CONFIG_DIR / "editor.json"
_FROZEN_EDITOR: str | None = None


def _get_editor() -> str | None:
    """Return the preferred editor command.

    Order of precedence:
    1. Runtime override set via /editor set.
    2. EDITOR environment variable.
    3. Fallback editors (vim, vi, nano, notepad).
    """
    if _FROZEN_EDITOR is not None:
        return _FROZEN_EDITOR
    if os.environ.get("EDITOR"):
        return os.environ.get("EDITOR")
    for candidate in ("vim", "vi", "nano", "notepad"):
        if shutil.which(candidate):
            return candidate
    return None


def _set_editor(command: str) -> None:
    """Persist the preferred editor to disk and update the in-memory value."""
    global _FROZEN_EDITOR
    _FROZEN_EDITOR = command
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    EDITOR_CONFIG_FILE.write_text(json.dumps({"command": command}, ensure_ascii=False), encoding="utf-8")


def _load_editor() -> None:
    """Load a previously persisted editor override from disk."""
    global _FROZEN_EDITOR
    if EDITOR_CONFIG_FILE.exists():
        try:
            data = json.loads(EDITOR_CONFIG_FILE.read_text(encoding="utf-8"))
            command = data.get("command", "")
            if command:
                _FROZEN_EDITOR = command
        except (json.JSONDecodeError, OSError):
            pass


# Load persisted editor override on module import.
_load_editor()
