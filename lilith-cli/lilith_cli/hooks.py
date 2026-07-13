"""Lifecycle hooks for Lilith CLI.

Inspired by Aider and Claude Code hooks. Users can define scripts that run
before and after tool calls, or on specific events like session start/end.

Hook config lives in ~/.yggdrasil/hooks/<event>.sh (or .py).

Events:
- pre-tool-call: runs before any tool is invoked (gets tool_name, tool_args via env)
- post-tool-call: runs after any tool returns (gets tool_name, tool_args, result via env)
- on-error: runs when a tool or LLM call fails
- on-cancel: runs when the user cancels (Ctrl+C)
- on-compact: runs before context compaction happens
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

_HOOKS_DIR: Path = Path.home() / ".yggdrasil" / "hooks"

# Events and their env-var contract
_EVENTS: dict[str, list[str]] = {
    "pre-tool-call": ["LILITH_TOOL_NAME", "LILITH_TOOL_ARGS"],
    "post-tool-call": ["LILITH_TOOL_NAME", "LILITH_TOOL_RESULT"],
    "on-error": ["LILITH_ERROR"],
    "on-cancel": ["LILITH_TURN_ID"],
    "on-compact": ["LILITH_OLD_TOKENS", "LILITH_NEW_TOKENS"],
}


def _ensure_hooks_dir() -> Path:
    _HOOKS_DIR.mkdir(parents=True, exist_ok=True)
    return _HOOKS_DIR


def list_hooks() -> dict[str, list[str]]:
    """Return installed hooks per event."""
    _ensure_hooks_dir()
    result: dict[str, list[str]] = {}
    if not _HOOKS_DIR.exists():
        return result
    for event in _EVENTS:
        event_dir = _HOOKS_DIR / event
        if event_dir.exists():
            hooks = []
            for f in sorted(event_dir.iterdir()):
                if f.is_file() and (f.suffix in (".sh", ".py", ".ps1")):
                    hooks.append(f.name)
            result[event] = hooks
    return result


def run_hook(event: str, env_extra: dict[str, str]) -> int:
    """Run all hooks for ``event`` with extra environment variables.

    Returns 0 if all hooks succeeded, 1 otherwise.
    """
    if event not in _EVENTS:
        return 0
    _ensure_hooks_dir()
    event_dir = _HOOKS_DIR / event
    if not event_dir.exists():
        return 0

    env = os.environ.copy()
    env.update({k: str(v) for k, v in env_extra.items()})

    overall_ok = True
    for hook_file in sorted(event_dir.iterdir()):
        if not hook_file.is_file():
            continue
        try:
            if hook_file.suffix == ".py":
                result = subprocess.run(
                    ["python", str(hook_file)],
                    env=env,
                    capture_output=True,
                    timeout=30,
                )
            elif hook_file.suffix == ".sh":
                result = subprocess.run(
                    ["bash", str(hook_file)],
                    env=env,
                    capture_output=True,
                    timeout=30,
                )
            elif hook_file.suffix == ".ps1":
                result = subprocess.run(
                    ["powershell", "-File", str(hook_file)],
                    env=env,
                    capture_output=True,
                    timeout=30,
                )
            else:
                continue
            if result.returncode != 0:
                overall_ok = False
        except subprocess.TimeoutExpired:
            overall_ok = False
        except Exception:
            overall_ok = False
    return 0 if overall_ok else 1


def hooks_dir() -> Path:
    """Return the hooks directory, creating it if it doesn't exist."""
    return _ensure_hooks_dir()
