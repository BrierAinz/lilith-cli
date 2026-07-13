"""Language-server command registry for the Lilith IDE LSP client."""

from __future__ import annotations

import shutil
from pathlib import Path


# Known language-server launch commands per language ID.
LANGUAGE_SERVERS: dict[str, list[str]] = {
    "python": ["python", "-m", "pylsp"],
    "rust": ["rust-analyzer"],
    "typescript": ["typescript-language-server", "--stdio"],
    "javascript": ["typescript-language-server", "--stdio"],
    "go": ["gopls"],
    "json": ["vscode-json-language-server", "--stdio"],
    "yaml": ["yaml-language-server", "--stdio"],
}


def language_server_command(language: str) -> list[str] | None:
    """Return a runnable command for *language* if a server is installed.

    Returns ``None`` if the language is unsupported or the binary is missing.
    """
    language = language.lower()
    cmd = LANGUAGE_SERVERS.get(language)
    if not cmd:
        return None
    binary = cmd[0]
    if shutil.which(binary):
        return list(cmd)
    # Python modules don't need a separate binary if python is available.
    if binary == "python" and shutil.which("python"):
        return list(cmd)
    return None


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
