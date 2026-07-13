"""LSP Manager for the Lilith IDE.

Keeps one ``LSPClient`` per active language, starts servers on demand and
routes editor events (didOpen/didChange/didSave) and user requests
(completion, hover, definition) to the right client.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .client import LSPClient
from .languages import language_server_command


class LSPManager:
    """Manage language-server clients for the current project."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._clients: dict[str, LSPClient] = {}
        self.on_diagnostics: Any = None

    def _on_client_diagnostics(self, uri: str, diagnostics: list[dict[str, Any]]) -> None:
        """Forward server diagnostics to the UI callback."""
        if callable(self.on_diagnostics):
            try:
                self.on_diagnostics(uri, diagnostics)
            except Exception:
                pass

    async def get_client(self, language: str) -> LSPClient | None:
        """Return an active client for *language*, starting it if needed."""
        language = language.lower()
        if language in self._clients:
            return self._clients[language]
        command = language_server_command(language)
        if not command:
            return None
        client = LSPClient(command, self.root)
        client._on_diagnostics = self._on_client_diagnostics
        if await client.start():
            self._clients[language] = client
            return client
        return None

    async def stop_all(self) -> None:
        """Shutdown all active language servers."""
        for client in list(self._clients.values()):
            await client.stop()
        self._clients.clear()

    def status(self) -> dict[str, str]:
        """Return a status string per active language."""
        return {
            language: "running" if client._initialized else "starting"
            for language, client in self._clients.items()
        }

    def diagnostics_for(self, path: Path) -> list[dict[str, Any]]:
        """Return the latest diagnostics for *path* across all clients."""
        uri = path.as_uri()
        results: list[dict[str, Any]] = []
        for client in self._clients.values():
            results.extend(client.get_diagnostics(uri))
        return results

    # ── Document lifecycle ────────────────────────────────────────────

    async def did_open(self, path: Path, language: str, text: str) -> bool:
        client = await self.get_client(language)
        if not client:
            return False
        await client.did_open(path.as_uri(), language, text)
        return True

    async def did_change(self, path: Path, language: str, text: str, version: int) -> bool:
        client = await self.get_client(language)
        if not client:
            return False
        await client.did_change(path.as_uri(), language, text, version)
        return True

    async def did_save(self, path: Path, language: str) -> bool:
        client = await self.get_client(language)
        if not client:
            return False
        await client._notify("textDocument/didSave", {"textDocument": {"uri": path.as_uri()}})
        return True

    # ── User requests ─────────────────────────────────────────────────

    async def completion(
        self,
        path: Path,
        language: str,
        line: int,
        character: int,
    ) -> list[dict[str, Any]]:
        client = await self.get_client(language)
        if not client:
            return []
        return await client.completion(path.as_uri(), line, character)

    async def hover(self, path: Path, language: str, line: int, character: int) -> str:
        client = await self.get_client(language)
        if not client:
            return ""
        return await client.hover(path.as_uri(), line, character)

    async def definition(
        self,
        path: Path,
        language: str,
        line: int,
        character: int,
    ) -> list[dict[str, Any]]:
        client = await self.get_client(language)
        if not client:
            return []
        return await client.definition(path.as_uri(), line, character)
