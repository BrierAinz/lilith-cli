"""File watcher tools for real-time file-change monitoring.

Provides tools to start/stop/query file watches and retrieve accumulated
events. The implementation uses ``watchdog`` when available; otherwise it
falls back to a simple polling observer so the tool is always available.
"""

from __future__ import annotations

import fnmatch
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .base import BaseTool, ToolResult
from .registry import ToolRegistry


@dataclass
class _WatchEvent:
    """A single normalized file-system event."""

    timestamp: float
    event_type: str
    path: str


@dataclass
class _WatchEntry:
    """Internal state for a single active watch."""

    watch_id: str
    paths: list[str]
    patterns: list[str]
    ignore_patterns: list[str]
    events: list[_WatchEvent] = field(default_factory=list)
    observer: Any = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def matches(self, relative_path: str) -> bool:
        """Return True if *relative_path* matches at least one include pattern
        and no ignore pattern. When no include patterns are given, only ignores
        are checked.
        """
        if self.ignore_patterns:
            for ignore in self.ignore_patterns:
                if fnmatch.fnmatch(relative_path, ignore) or fnmatch.fnmatch(
                    Path(relative_path).name, ignore
                ):
                    return False
        if not self.patterns:
            return True
        for pattern in self.patterns:
            if fnmatch.fnmatch(relative_path, pattern) or fnmatch.fnmatch(
                Path(relative_path).name, pattern
            ):
                return True
        return False


class _WatchManager:
    """In-memory manager for active file watches."""

    _instance: _WatchManager | None = None
    _lock = threading.Lock()

    def __new__(cls) -> _WatchManager:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._watches: dict[str, _WatchEntry] = {}
                    cls._instance._counter = 0
        return cls._instance

    def _next_id(self) -> str:
        with self._lock:
            self._counter += 1
            return f"watch_{self._counter}"

    def _normalize_paths(self, paths: list[str]) -> list[str]:
        expanded: list[str] = []
        for p in paths:
            p = os.path.expanduser(p)
            expanded.append(str(Path(p).resolve()))
        return expanded

    def _add_event(self, entry: _WatchEntry, event_type: str, path: str) -> None:
        path_obj = Path(path)
        try:
            rel = path_obj.relative_to(Path(entry.paths[0]))
            rel_str = str(rel)
        except ValueError:
            rel_str = path_obj.name

        if not entry.matches(rel_str):
            return

        with entry.lock:
            entry.events.append(_WatchEvent(time.time(), event_type, str(path)))

    def start(
        self,
        paths: list[str],
        patterns: list[str] | None = None,
        ignore_patterns: list[str] | None = None,
    ) -> tuple[str, bool]:
        """Start watching *paths*. Returns (watch_id, success)."""
        patterns = patterns or []
        ignore_patterns = ignore_patterns or []
        resolved = self._normalize_paths(paths)
        for p in resolved:
            if not Path(p).exists():
                return "", False

        watch_id = self._next_id()
        entry = _WatchEntry(
            watch_id=watch_id,
            paths=resolved,
            patterns=patterns,
            ignore_patterns=ignore_patterns,
        )

        with self._lock:
            self._watches[watch_id] = entry

        try:
            observer = _create_observer(entry)
            entry.observer = observer
            observer.start()
        except Exception:
            with self._lock:
                self._watches.pop(watch_id, None)
            return "", False

        return watch_id, True

    def stop(self, watch_id: str) -> bool:
        """Stop a watch and release its resources."""
        with self._lock:
            entry = self._watches.get(watch_id)
            if entry is None:
                return False
            del self._watches[watch_id]

        if entry.observer is not None:
            try:
                entry.observer.stop()
                entry.observer.join(timeout=1.0)
            except Exception:
                pass
        return True

    def status(self) -> dict[str, Any]:
        """Return summary of active watches."""
        with self._lock:
            watches = list(self._watches.values())
        return {
            "watches": [
                {
                    "watch_id": w.watch_id,
                    "paths": w.paths,
                    "patterns": w.patterns,
                    "ignore_patterns": w.ignore_patterns,
                    "event_count": len(w.events),
                }
                for w in watches
            ],
            "count": len(watches),
        }

    def events(self, watch_id: str, since: float = 0) -> list[dict[str, Any]] | None:
        """Return events for *watch_id* with timestamp >= *since*."""
        with self._lock:
            entry = self._watches.get(watch_id)
        if entry is None:
            return None
        with entry.lock:
            return [
                {
                    "timestamp": e.timestamp,
                    "event_type": e.event_type,
                    "path": e.path,
                }
                for e in entry.events
                if e.timestamp >= since
            ]


# ── Observer backends ──────────────────────────────────────────────


def _create_observer(entry: _WatchEntry) -> Any:
    """Create the best available observer for the platform."""
    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler

        class _Handler(FileSystemEventHandler):
            def __init__(self, entry: _WatchEntry) -> None:
                self.entry = entry

            def _record(self, event_type: str, path: str) -> None:
                self.entry._add_event(event_type, path)

            def on_created(self, event):  # type: ignore[no-untyped-def]
                self._record("created", event.src_path)

            def on_modified(self, event):  # type: ignore[no-untyped-def]
                self._record("modified", event.src_path)

            def on_deleted(self, event):  # type: ignore[no-untyped-def]
                self._record("deleted", event.src_path)

            def on_moved(self, event):  # type: ignore[no-untyped-def]
                self._record("moved", event.src_path)

        observer = Observer()
        handler = _Handler(entry)
        for p in entry.paths:
            observer.schedule(handler, p, recursive=True)
        return observer
    except Exception:
        return _PollingObserver(entry)


class _PollingObserver:
    """Minimal fallback observer that polls file mtimes and sizes."""

    def __init__(self, entry: _WatchEntry) -> None:
        self.entry = entry
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._snapshot: dict[str, tuple[float, int]] = {}

    def start(self) -> None:
        self._snapshot = self._build_snapshot()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def _build_snapshot(self) -> dict[str, tuple[float, int]]:
        snap: dict[str, tuple[float, int]] = {}
        for p in self.entry.paths:
            root = Path(p)
            if not root.exists():
                continue
            for item in root.rglob("*"):
                if item.is_file():
                    try:
                        stat = item.stat()
                        snap[str(item.resolve())] = (stat.st_mtime, stat.st_size)
                    except OSError:
                        continue
        return snap

    def _run(self) -> None:
        while not self._stop.is_set():
            time.sleep(1.0)
            new_snapshot = self._build_snapshot()
            for path, (mtime, size) in new_snapshot.items():
                old = self._snapshot.get(path)
                if old is None:
                    self.entry._add_event("created", path)
                elif old != (mtime, size):
                    self.entry._add_event("modified", path)
            for path in self._snapshot:
                if path not in new_snapshot:
                    self.entry._add_event("deleted", path)
            self._snapshot = new_snapshot


# ── Tool implementations ─────────────────────────────────────────────


@ToolRegistry.register
class WatchFilesTool(BaseTool):
    """Start watching one or more filesystem paths for changes."""

    name = "watch_files"
    description = (
        "Inicia un watcher de archivos para monitorear cambios en tiempo real. "
        "Devuelve un watch_id que se usa para consultar o detener el watcher."
    )
    parameters = {
        "paths": {
            "type": "array",
            "required": True,
            "description": "Lista de rutas a observar",
        },
        "patterns": {
            "type": "array",
            "required": False,
            "description": "Patrones de archivo a incluir (ej: *.py, *.js)",
        },
        "ignore_patterns": {
            "type": "array",
            "required": False,
            "description": "Patrones de archivo a ignorar (ej: *.log, node_modules/*)",
        },
    }

    def execute(
        self,
        paths: list[str] | None = None,
        patterns: list[str] | None = None,
        ignore_patterns: list[str] | None = None,
        **_: Any,
    ) -> ToolResult:
        """Inicia un watcher de archivos."""
        if not paths:
            return ToolResult(success=False, data=None, error="paths es requerido")
        if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
            return ToolResult(success=False, data=None, error="paths debe ser una lista de strings")

        watch_id, ok = _WatchManager().start(
            paths, patterns=patterns or [], ignore_patterns=ignore_patterns or []
        )
        if not ok:
            return ToolResult(success=False, data=None, error="No se pudo iniciar el watcher")
        return ToolResult(
            success=True,
            data={"watch_id": watch_id, "paths": paths, "patterns": patterns or []},
        )


@ToolRegistry.register
class WatchStatusTool(BaseTool):
    """Return the status of all active file watchers."""

    name = "watch_status"
    description = "Lista los watchers activos y sus configuraciones"
    parameters = {}

    def execute(self, **_kwargs: Any) -> ToolResult:
        """Lista watchers activos."""
        return ToolResult(success=True, data=_WatchManager().status())


@ToolRegistry.register
class WatchStopTool(BaseTool):
    """Stop a running file watcher by ID."""

    name = "watch_stop"
    description = "Detiene un watcher de archivos por su watch_id"
    parameters = {
        "watch_id": {
            "type": "string",
            "required": True,
            "description": "ID del watcher a detener",
        },
    }

    def execute(self, watch_id: str = "", **_kwargs: Any) -> ToolResult:
        """Detiene un watcher."""
        if not watch_id:
            return ToolResult(success=False, data=None, error="watch_id es requerido")
        ok = _WatchManager().stop(watch_id)
        if not ok:
            return ToolResult(success=False, data=None, error=f"Watcher no encontrado: {watch_id}")
        return ToolResult(success=True, data={"watch_id": watch_id, "stopped": True})


@ToolRegistry.register
class WatchEventsTool(BaseTool):
    """Get events for a watcher since a given timestamp."""

    name = "watch_events"
    description = "Obtiene los eventos de un watcher desde un timestamp"
    parameters = {
        "watch_id": {
            "type": "string",
            "required": True,
            "description": "ID del watcher",
        },
        "since": {
            "type": "number",
            "required": False,
            "default": 0,
            "description": "Timestamp Unix desde el cual obtener eventos",
        },
    }

    def execute(self, watch_id: str = "", since: float = 0, **_kwargs: Any) -> ToolResult:
        """Devuelve eventos de un watcher."""
        if not watch_id:
            return ToolResult(success=False, data=None, error="watch_id es requerido")
        events = _WatchManager().events(watch_id, since=since)
        if events is None:
            return ToolResult(success=False, data=None, error=f"Watcher no encontrado: {watch_id}")
        return ToolResult(success=True, data={"watch_id": watch_id, "events": events})


__all__ = [
    "WatchFilesTool",
    "WatchStatusTool",
    "WatchStopTool",
    "WatchEventsTool",
]
