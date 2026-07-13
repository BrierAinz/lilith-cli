"""Agent context helpers for the Lilith IDE.

Provides a ``ContextManager`` that resolves @-mentions such as ``@file``,
``@selection``, ``@folder``, ``@project``, ``@git-diff`` and ``@terminal-output``
into structured snippets that can be injected into the agent prompt.
"""

from __future__ import annotations

import asyncio
import dataclasses
import re
import subprocess
from pathlib import Path
from typing import Any


@dataclasses.dataclass
class ContextItem:
    """A single piece of context to send to the agent."""

    kind: str
    name: str
    content: str
    source: str = ""

    def __str__(self) -> str:
        header = f"<{self.kind}:{self.name}>"
        footer = f"</{self.kind}:{self.name}>"
        return f"{header}\n{self.content}\n{footer}"


class ContextManager:
    """Resolve @-mentions and keep an explicit context list for the current turn."""

    MENTION_RE = re.compile(r"@(\w+)(?::([^\s]+))?")

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._terminal_history: list[str] = []

    # ── Mention parsing ───────────────────────────────────────────────

    def parse_mentions(self, text: str) -> list[tuple[str, str]]:
        """Return list of (kind, argument) tuples found in *text*."""
        results: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for match in self.MENTION_RE.finditer(text):
            kind = match.group(1).lower()
            arg = match.group(2) or ""
            key = (kind, arg)
            if key not in seen:
                seen.add(key)
                results.append((kind, arg))
        return results

    def strip_mentions(self, text: str) -> str:
        """Return *text* with @-mentions removed."""
        return self.MENTION_RE.sub("", text).strip()

    # ── Resolution ────────────────────────────────────────────────────

    async def resolve(
        self,
        kind: str,
        argument: str,
        *,
        current_file: Path | None = None,
        get_selection: Any = None,
    ) -> ContextItem | None:
        """Resolve a single mention into a ``ContextItem``."""
        resolver = getattr(self, f"_resolve_{kind.replace('-', '_')}", None)
        if resolver is None:
            return None
        return await resolver(argument, current_file=current_file, get_selection=get_selection)

    async def resolve_all(
        self,
        text: str,
        *,
        current_file: Path | None = None,
        get_selection: Any = None,
    ) -> list[ContextItem]:
        """Resolve every mention in *text* and return the resulting items."""
        mentions = self.parse_mentions(text)
        items: list[ContextItem] = []
        for kind, argument in mentions:
            item = await self.resolve(
                kind,
                argument,
                current_file=current_file,
                get_selection=get_selection,
            )
            if item:
                items.append(item)
        return items

    # ── Individual resolvers ──────────────────────────────────────────

    async def _resolve_file(
        self,
        argument: str,
        *,
        current_file: Path | None = None,
        get_selection: Any = None,
    ) -> ContextItem | None:
        """@file:<path> or @file (current file)."""
        if argument:
            path = self.root / argument
        else:
            path = current_file
        if not path or not path.exists() or not path.is_file():
            return ContextItem(
                kind="file",
                name=argument or "?",
                content="[Archivo no encontrado]",
                source=str(path) if path else "",
            )
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            content = f"[Error leyendo archivo: {exc}]"
        return ContextItem(
            kind="file",
            name=self._relative(path),
            content=content,
            source=str(path),
        )

    async def _resolve_selection(
        self,
        argument: str,
        *,
        current_file: Path | None = None,
        get_selection: Any = None,
    ) -> ContextItem | None:
        """@selection — current editor selection."""
        name = self._relative(current_file) if current_file else "?"
        content = ""
        if callable(get_selection):
            try:
                content = get_selection()
            except Exception as exc:
                content = f"[Error obteniendo selección: {exc}]"
        if not content:
            content = "[No hay selección activa]"
        return ContextItem(kind="selection", name=name, content=content, source=name)

    async def _resolve_folder(
        self,
        argument: str,
        *,
        current_file: Path | None = None,
        get_selection: Any = None,
    ) -> ContextItem | None:
        """@folder:<path> — recursive file listing."""
        folder = self.root / argument if argument else self.root
        if not folder.exists() or not folder.is_dir():
            return ContextItem(
                kind="folder",
                name=argument or ".",
                content="[Carpeta no encontrada]",
                source=str(folder),
            )
        lines: list[str] = []
        for path in sorted(folder.rglob("*")):
            if path.is_dir():
                lines.append(f"{self._relative(path)}/")
            else:
                lines.append(self._relative(path))
        return ContextItem(
            kind="folder",
            name=self._relative(folder),
            content="\n".join(lines) or "[vacío]",
            source=str(folder),
        )

    async def _resolve_project(
        self,
        argument: str,
        *,
        current_file: Path | None = None,
        get_selection: Any = None,
    ) -> ContextItem | None:
        """@project — concise project tree + optional README snippet."""
        lines: list[str] = [f"Proyecto: {self.root.name}", "Raíz: " + self._relative(self.root)]
        for path in sorted(self.root.rglob("*")):
            rel = self._relative(path)
            if any(part in rel.split("/") for part in (".git", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache")):
                continue
            lines.append(f"{rel}/" if path.is_dir() else rel)
        readme = self.root / "README.md"
        if readme.exists():
            try:
                snippet = readme.read_text(encoding="utf-8", errors="replace")[:2000]
                lines.append("\n--- README.md (primeros 2000 caracteres) ---\n" + snippet)
            except Exception:
                pass
        return ContextItem(kind="project", name=self.root.name, content="\n".join(lines), source=str(self.root))

    async def _resolve_git_diff(
        self,
        argument: str,
        *,
        current_file: Path | None = None,
        get_selection: Any = None,
    ) -> ContextItem | None:
        """@git-diff — current working tree diff."""
        content = await self._run_shell("git diff --no-color")
        if not content:
            content = "[No hay cambios en el working tree]"
        return ContextItem(kind="git-diff", name="working-tree", content=content, source="git diff")

    async def _resolve_terminal_output(
        self,
        argument: str,
        *,
        current_file: Path | None = None,
        get_selection: Any = None,
    ) -> ContextItem | None:
        """@terminal-output — last captured terminal output."""
        content = "\n".join(self._terminal_history[-50:]) or "[Terminal vacía]"
        return ContextItem(kind="terminal-output", name="terminal", content=content, source="terminal")

    # ── Helpers ───────────────────────────────────────────────────────

    def _relative(self, path: Path) -> str:
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return path.as_posix()

    async def _run_shell(self, command: str, *, timeout: float = 10.0) -> str:
        try:
            proc = await asyncio.wait_for(
                asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=self.root,
                ),
                timeout=timeout,
            )
            stdout, stderr = await proc.communicate()
            out = stdout.decode("utf-8", errors="replace").strip()
            err = stderr.decode("utf-8", errors="replace").strip()
            return (out + (f"\n{err}" if err else "")).strip()
        except asyncio.TimeoutError:
            return "[Timeout ejecutando comando]"
        except Exception as exc:
            return f"[Error: {exc}]"

    def record_terminal_output(self, lines: list[str]) -> None:
        """Append terminal output lines to the internal buffer."""
        self._terminal_history.extend(lines)
        if len(self._terminal_history) > 500:
            self._terminal_history = self._terminal_history[-500:]

    def clear_terminal_history(self) -> None:
        """Reset the captured terminal output."""
        self._terminal_history.clear()
