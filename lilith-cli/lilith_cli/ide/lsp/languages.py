"""Language-server command registry for the Lilith IDE LSP client.

Resolution order (per language):

1. Try the preferred server (e.g. ``pyright-langserver``) by checking the
   binary on PATH via :func:`shutil.which`.
2. Fall back to a Python module launch (``python -m <module>``) when the
   module can be imported; this lets ``pylsp`` work even when its console
   script is not on PATH (common in venvs).
3. Return ``None`` so callers can degrade gracefully — the IDE never
   assumes that a server is available.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

# Preferred command per language. Each entry is a list of argv tokens that
# the LSP client will pass to ``asyncio.create_subprocess_exec``. The first
# token is the binary the detector tries to resolve on PATH; if that fails
# and the entry looks like a ``python -m <module>`` invocation, the detector
# also accepts the form where ``<module>`` is importable in the current
# interpreter.
PREFERRED_SERVERS: dict[str, list[str]] = {
    "python": ["pyright-langserver", "--stdio"],
    "rust": ["rust-analyzer"],
    "typescript": ["typescript-language-server", "--stdio"],
    "javascript": ["typescript-language-server", "--stdio"],
    "go": ["gopls"],
    "json": ["vscode-json-language-server", "--stdio"],
    "yaml": ["yaml-language-server", "--stdio"],
}

# Hard fallback per language — used when the preferred server is missing.
# Python falls back to ``python -m pylsp`` which works in any venv where
# the ``python-lsp-server`` package is installed.
FALLBACK_SERVERS: dict[str, list[str]] = {
    "python": [sys.executable or "python", "-m", "pylsp"],
}


def _binary_on_path(cmd: list[str]) -> bool:
    """True if the first token of *cmd* resolves on PATH."""
    return bool(cmd) and shutil.which(cmd[0]) is not None


def _python_module_importable(module: str) -> bool:
    """True if *module* can be imported in the current interpreter."""
    return importlib.util.find_spec(module) is not None


def _resolve(cmd: list[str]) -> list[str] | None:
    """Return *cmd* if it is runnable, ``None`` otherwise."""
    if not cmd:
        return None
    if _binary_on_path(cmd):
        return list(cmd)
    # ``python -m <module>`` style — accept if the module is importable.
    if len(cmd) == 3 and cmd[1] == "-m" and _python_module_importable(cmd[2]):
        # Normalize to the interpreter the LSP client itself runs under.
        executable = sys.executable or cmd[0]
        return [executable, "-m", cmd[2]]
    return None


def language_server_command(language: str) -> list[str] | None:
    """Return a runnable command for *language*, or ``None`` if unavailable.

    Resolution order:

    1. Preferred server (e.g. ``pyright-langserver``).
    2. Hard fallback (e.g. ``python -m pylsp``).

    Returns ``None`` if no candidate can be located on disk.
    """
    language = language.lower()
    preferred = PREFERRED_SERVERS.get(language)
    resolved = _resolve(preferred) if preferred else None
    if resolved is not None:
        return resolved
    fallback = FALLBACK_SERVERS.get(language)
    resolved_fallback = _resolve(fallback) if fallback else None
    return resolved_fallback


def detect_language_server(path: Path) -> list[str] | None:
    """Auto-detect a language server for *path* based on its extension."""
    mapping = {
        ".py": "python",
        ".rs": "rust",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".js": "javascript",
        ".jsx": "javascript",
        ".go": "go",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
    }
    language = mapping.get(path.suffix.lower())
    if not language:
        return None
    return language_server_command(language)


def preferred_server_for(language: str) -> str | None:
    """Return the preferred server's binary name (for diagnostics messages)."""
    cmd = PREFERRED_SERVERS.get(language.lower())
    return cmd[0] if cmd else None
