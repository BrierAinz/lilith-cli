"""LSP Manager for the Lilith IDE.

Keeps one :class:`LSPClient` per active language, starts servers on demand
and routes editor events (``didOpen``/``didChange``/``didSave``/``didClose``)
and user requests (completion, hover, definition) to the right client.

The manager is intentionally lazy: a language server is only spawned the
first time the IDE asks for completions or other features for a file in
that language. If no server is available, every operation short-circuits
to an empty/default result so the IDE keeps working.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .client import LSPClient
from .languages import language_server_command

LOG = logging.getLogger("lilith.lsp.manager")


class LSPManager:
    """Manage language-server clients for the current project.

    Parameters
    ----------
    root:
        Project root passed to each spawned language server.
    on_diagnostics:
        ``(uri, diagnostics)`` callback invoked as servers publish them.
        Used by ``EditorMixin`` to update the info bar.
    on_log_message:
        ``(level, message)`` callback for ``window/logMessage`` notifications.
    on_show_message:
        ``(level, message)`` callback for ``window/showMessage`` notifications.
    """

    def __init__(
        self,
        root: Path,
        *,
        on_diagnostics: Any = None,
        on_log_message: Any = None,
        on_show_message: Any = None,
    ) -> None:
        self.root = root.resolve()
        self._clients: dict[str, LSPClient] = {}
        self.on_diagnostics = on_diagnostics
        self.on_log_message = on_log_message
        self.on_show_message = on_show_message
        # Track which URIs each client has open so ``did_close`` is sent on
        # the matching close event without us having to inspect the IDE state.
        self._open_documents: dict[LSPClient, set[str]] = {}

    # ── Client management ──────────────────────────────────────────

    def _build_client(self, command: list[str]) -> LSPClient:
        """Construct an LSPClient wired to the manager's callbacks."""
        client = LSPClient(
            command,
            self.root,
            on_diagnostics=self._on_client_diagnostics,
            on_log_message=self._on_client_log_message,
            on_show_message=self._on_client_show_message,
        )
        self._open_documents[client] = set()
        return client

    async def get_client(self, language: str) -> LSPClient | None:
        """Return an active client for *language*, starting it if needed.

        Returns ``None`` if the language has no available server, or the
        server fails to start; in that case the IDE keeps running and
        future requests for the same language will keep returning ``None``.
        """
        language = language.lower()
        if language in self._clients:
            return self._clients[language]
        command = language_server_command(language)
        if not command:
            return None
        client = self._build_client(command)
        try:
            started = await client.start()
        except Exception as exc:
            LOG.debug("LSPManager: start failed for %s: %s", language, exc)
            started = False
        if started:
            self._clients[language] = client
            return client
        # Don't remember failed clients — caller may install pyright later.
        return None

    async def stop_all(self) -> None:
        """Shut down every active server and forget about them."""
        clients = list(self._clients.values())
        self._clients.clear()
        for client in clients:
            try:
                await client.stop()
            except Exception:
                LOG.debug("LSPManager: error stopping %s", client.command)
        self._open_documents.clear()

    def status(self) -> dict[str, str]:
        """Return ``{language: status}`` for every active server."""
        result: dict[str, str] = {}
        for language, client in self._clients.items():
            if client.initialized:
                result[language] = "running"
            else:
                result[language] = "starting"
        return result

    def diagnostics_for(self, path: Path) -> list[dict[str, Any]]:
        """Return the latest diagnostics for *path* across all clients."""
        uri = path.resolve().as_uri()
        results: list[dict[str, Any]] = []
        for client in self._clients.values():
            results.extend(client.get_diagnostics(uri))
        return results

    # ── Callback forwarding ────────────────────────────────────────

    def _on_client_diagnostics(
        self, uri: str, diagnostics: list[dict[str, Any]]
    ) -> None:
        if callable(self.on_diagnostics):
            try:
                self.on_diagnostics(uri, diagnostics)
            except Exception:
                LOG.exception("LSPManager: on_diagnostics raised")

    def _on_client_log_message(self, level: int, message: str) -> None:
        if callable(self.on_log_message):
            try:
                self.on_log_message(level, message)
            except Exception:
                pass

    def _on_client_show_message(self, level: int, message: str) -> None:
        if callable(self.on_show_message):
            try:
                self.on_show_message(level, message)
            except Exception:
                pass

    # ── Document lifecycle ─────────────────────────────────────────

    async def did_open(
        self, path: Path, language: str, text: str, version: int = 1
    ) -> bool:
        client = await self.get_client(language)
        if not client:
            return False
        uri = path.resolve().as_uri()
        try:
            await client.did_open(uri, language, text, version)
        except Exception:
            return False
        # Track the open doc so subsequent did_change stays a no-op on a
        # un-started server and so did_close only fires when we actually
        # called did_open.
        self._open_documents.setdefault(client, set()).add(uri)
        return True

    async def did_change(
        self, path: Path, language: str, text: str, version: int
    ) -> bool:
        client = await self.get_client(language)
        if not client:
            return False
        uri = path.resolve().as_uri()
        try:
            await client.did_change(uri, text, version)
        except Exception:
            return False
        return True

    async def did_save(
        self, path: Path, language: str, text: str | None = None
    ) -> bool:
        client = await self.get_client(language)
        if not client:
            return False
        uri = path.resolve().as_uri()
        try:
            await client.did_save(uri, text)
        except Exception:
            return False
        return True

    async def did_close(self, path: Path, language: str) -> bool:
        """Tell the language server we no longer hold this document.

        Safe to call on paths we never opened — it short-circuits quietly.
        """
        client = await self.get_client(language)
        if not client:
            return False
        uri = path.resolve().as_uri()
        docs = self._open_documents.get(client)
        # Only notify the server if we actually opened this URI on this
        # client.  Skip silently otherwise (e.g. server unavailable).
        if not docs or uri not in docs:
            return False
        try:
            await client.did_close(uri)
        except Exception:
            return False
        docs.discard(uri)
        return True

    # ── User requests ──────────────────────────────────────────────

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
        uri = path.resolve().as_uri()
        try:
            return await client.completion(uri, line, character)
        except Exception:
            return []

    async def hover(
        self, path: Path, language: str, line: int, character: int
    ) -> str:
        client = await self.get_client(language)
        if not client:
            return ""
        uri = path.resolve().as_uri()
        try:
            return await client.hover(uri, line, character)
        except Exception:
            return ""

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
        uri = path.resolve().as_uri()
        try:
            return await client.definition(uri, line, character)
        except Exception:
            return []
