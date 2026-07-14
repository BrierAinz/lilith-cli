"""LSP integration package for the Lilith IDE.

Public surface:

* :class:`LSPClient` — async JSON-RPC 2.0 client over stdio.
* :class:`LSPManager` — one client per language, lazy startup, graceful
  degradation if no server is installed.
* :class:`LSPError` — raised on JSON-RPC error responses.
* :func:`language_server_command` — preferred-then-fallback resolution.
* :func:`detect_language_server` — extension-based auto-detection.

Servers are spawned only on first request for a given language. If the
preferred binary (e.g. ``pyright-langserver``) is not on PATH, the manager
falls back to a Python module launch (``python -m pylsp``).
"""

from .client import LSPClient, LSPError
from .languages import (
    detect_language_server,
    language_server_command,
    preferred_server_for,
)
from .manager import LSPManager

__all__ = [
    "LSPClient",
    "LSPError",
    "LSPManager",
    "detect_language_server",
    "language_server_command",
    "preferred_server_for",
]
