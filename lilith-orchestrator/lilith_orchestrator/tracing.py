"""Agent Tracing — Span-based observability for Lilith Orchestrator.

Inspired by OpenTelemetry traces, LangSmith runs, and Omnigent's request
audit trail. Lilith's hook system fires events, but a centralized,
queryable *span tree* is what makes multi-agent debugging tractable in
production.

A Trace is a tree of Spans. A Span represents a single unit of work::

    Trace                          <- top of a request tree
    └── Span(kind=REQUEST)         <- inbound user request
        └── Span(kind=AGENT)       <- coordinator dispatches an agent
            ├── Span(kind=TOOL)    <- tool execution
            ├── Span(kind=GATE)    <- quality gate evaluation
            └── Span(kind=JUDGE)   <- self-correction judge verdict

Span kinds:
    REQUEST     Inbound user request (one per engine.process call)
    AGENT       Agent dispatch / handoff (Odin → Mimir)
    TOOL        Tool invocation via SmartToolRouter
    GATE        QualityGate evaluation (policy/sandbox/heaven-deny)
    JUDGE       SelfCorrectionLoop Judge verdict
    MEMORY      MemoryStore operation (recall/store/consolidate)
    LLM         Direct LLM call (provider invocation)
    HOOK        Hook firing (debug events)

Usage::

    from lilith_orchestrator.tracing import Tracer, get_tracer, SpanKind

    tracer = get_tracer()              # global tracer singleton
    with tracer.trace_request("chat") as root:
        with tracer.span(SpanKind.AGENT, "odin.dispatch") as span:
            span.set_attribute("agent", "Odin")
            span.set_attribute("intent", "research")
            # ... dispatch agent ...
            with tracer.span(SpanKind.TOOL, "search_files") as tool:
                tool.set_attribute("query", q)
                # ... tool call ...

    # Query persisted traces
    from pathlib import Path
    from lilith_orchestrator.tracing import TraceStore
    store = TraceStore(Path("./traces.jsonl"))
    for trace in store.recent_traces(limit=10):
        print(trace.trace_id, trace.root_name, trace.duration_ms)

Persistence:
    By default ``trace_request()`` keeps spans in memory only. Set
    ``trace_store`` on the global tracer (or pass ``store=...`` to
    ``trace_request``) to flush the span tree to disk on close.

Why this matters:
    Hooks emit events. Spans correlate events into a tree with timing,
    attributes, parent-child edges, and persisted IDs. The result is a
    debugging artifact (`./traces.jsonl`) that an SRE can replay to
    understand *what the multi-agent system did* in production.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger("lilith.orchestrator.tracing")

TRACING_ENABLED_ENV = "LILITH_TRACING_ENABLED"
TRACING_FILE_ENV = "LILITH_TRACING_FILE"


class SpanKind(str, Enum):
    """Discriminator for span types in the trace tree.

    Inheriting ``str`` lets spans serialize cleanly to JSON without an
    enum-aware encoder while still allowing comparison as enums.
    """

    REQUEST = "request"
    AGENT = "agent"
    TOOL = "tool"
    GATE = "gate"
    JUDGE = "judge"
    MEMORY = "memory"
    LLM = "llm"
    HOOK = "hook"


# ── Span / Trace data classes ────────────────────────────────────────────────


@dataclass
class Span:
    """A single unit of work inside a trace.

    Spans form a tree via ``parent_id``. The Trace object owns the root
    and is the entry point for serialization.
    """

    span_id: str
    trace_id: str
    parent_id: str | None
    name: str
    kind: SpanKind
    start_time: float
    end_time: float | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    status: str = "ok"  # ok | error | cancelled
    error_message: str | None = None
    children: list["Span"] = field(default_factory=list)

    @property
    def duration_ms(self) -> float:
        """Total span duration in milliseconds. Zero until closed."""
        if self.end_time is None:
            return 0.0
        return round((self.end_time - self.start_time) * 1000.0, 2)

    def set_attribute(self, key: str, value: Any) -> None:
        """Attach a single key/value pair to the span."""
        self.attributes[key] = value

    def set_attributes(self, **attrs: Any) -> None:
        """Bulk attach attributes (kwargs form)."""
        self.attributes.update(attrs)

    def record_exception(self, message: str) -> None:
        """Mark the span as errored with a message."""
        self.status = "error"
        self.error_message = message

    def close(self) -> None:
        """Finalize the span — sets end_time if not already set."""
        if self.end_time is None:
            self.end_time = time.time()

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-friendly dict (recursively)."""
        return {
            "span_id": self.span_id,
            "trace_id": self.trace_id,
            "parent_id": self.parent_id,
            "name": self.name,
            "kind": self.kind.value if isinstance(self.kind, SpanKind) else str(self.kind),
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms,
            "attributes": self.attributes,
            "status": self.status,
            "error_message": self.error_message,
            "children": [c.to_dict() for c in self.children],
        }


@dataclass
class Trace:
    """A complete request-level trace.

    A Trace is just a root Span plus convenience metadata. Persistence
    stores the full tree under ``trace_id``.
    """

    root: Span
    created_at: float = field(default_factory=time.time)

    @property
    def trace_id(self) -> str:
        return self.root.trace_id

    @property
    def root_name(self) -> str:
        return self.root.name

    @property
    def duration_ms(self) -> float:
        return self.root.duration_ms

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "root_name": self.root_name,
            "created_at": self.created_at,
            "duration_ms": self.duration_ms,
            "spans": [self.root.to_dict()],
        }


# ── Tracer ──────────────────────────────────────────────────────────────────


class Tracer:
    """In-process tracer that builds Span trees.

    Thread-safe. The active span is tracked per-thread (Trace calls in
    different threads produce disjoint trees). Pass
    ``enabled=False`` to disable span emission without removing all
    context managers.
    """

    def __init__(
        self,
        service_name: str = "lilith",
        enabled: bool = True,
        store: "TraceStore | None" = None,
    ) -> None:
        self.service_name = service_name
        self.enabled = enabled
        self._store = store
        self._local = threading.local()
        self._lock = threading.Lock()
        self._active_traces: dict[str, Trace] = {}

    # ── Active-span stack (per-thread) ──────────────────────────────────────

    def _stack(self) -> list[Span]:
        if not hasattr(self._local, "stack"):
            self._local.stack = []
        return self._local.stack  # type: ignore[no-any-return]

    def _current(self) -> Span | None:
        stack = self._stack()
        return stack[-1] if stack else None

    # ── Configuration ───────────────────────────────────────────────────────

    def set_store(self, store: "TraceStore | None") -> None:
        """Wire a persistent backing store. Pass None to disable."""
        self._store = store

    def is_enabled(self) -> bool:
        """Whether ``trace_request`` actually emits spans."""
        if not self.enabled:
            return False
        return os.environ.get(TRACING_ENABLED_ENV, "1") not in ("0", "false", "False")

    # ── Root request ────────────────────────────────────────────────────────

    @contextmanager
    def trace_request(self, name: str, **attributes: Any) -> Iterator[Span]:
        """Open a root trace and yield the root span.

        The root span becomes the active span for any nested ``span()``
        calls on this thread. On exit the trace is finalized and, if a
        store is configured, persisted.
        """
        if not self.is_enabled():
            # Yielding a no-op span preserves the context-manager API.
            with self.span(SpanKind.REQUEST, name, **attributes) as dummy:
                dummy.set_attribute("tracing", "disabled")
                yield dummy
            return

        trace_id = uuid.uuid4().hex
        span_id = uuid.uuid4().hex
        root = Span(
            span_id=span_id,
            trace_id=trace_id,
            parent_id=None,
            name=name,
            kind=SpanKind.REQUEST,
            start_time=time.time(),
        )
        root.set_attributes(_service=self.service_name, **attributes)
        trace = Trace(root=root)

        with self._lock:
            self._active_traces[trace_id] = trace

        stack = self._stack()
        stack.append(root)
        try:
            yield root
        except Exception as exc:  # noqa: BLE001
            root.record_exception(str(exc))
            raise
        finally:
            root.close()
            stack.pop()
            if self._store is not None:
                try:
                    self._store.write(trace)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("trace persistence failed: %s", exc)
            with self._lock:
                self._active_traces.pop(trace_id, None)

    # ── Nested spans ────────────────────────────────────────────────────────

    @contextmanager
    def span(self, kind: SpanKind, name: str, **attributes: Any) -> Iterator[Span]:
        """Open a nested span. Inherits trace_id from the active parent."""
        if not self.is_enabled():
            zero = Span(
                span_id="0",
                trace_id="0",
                parent_id=None,
                name=name,
                kind=kind,
                start_time=time.time(),
            )
            zero.set_attribute("tracing", "disabled")
            zero.set_attributes(**attributes)
            zero.close()
            yield zero
            return

        parent = self._current()
        if parent is None:
            # Orphan span — treat as a new root with a fresh trace_id.
            trace_id = uuid.uuid4().hex
            parent_id = None
        else:
            trace_id = parent.trace_id
            parent_id = parent.span_id

        new_span = Span(
            span_id=uuid.uuid4().hex,
            trace_id=trace_id,
            parent_id=parent_id,
            name=name,
            kind=kind,
            start_time=time.time(),
        )
        new_span.set_attributes(**attributes)

        if parent is not None:
            parent.children.append(new_span)

        stack = self._stack()
        stack.append(new_span)
        try:
            yield new_span
        except Exception as exc:  # noqa: BLE001
            new_span.record_exception(str(exc))
            raise
        finally:
            new_span.close()
            if stack and stack[-1] is new_span:
                stack.pop()

    # ── Stats / inspection ──────────────────────────────────────────────────

    def active_trace_count(self) -> int:
        """Number of traces currently in flight (debug only)."""
        with self._lock:
            return len(self._active_traces)


# ── TraceStore ──────────────────────────────────────────────────────────────


class TraceStore:
    """Append-only JSONL persistence for traces.

    Each ``write()`` appends one JSON object per line. ``read_traces()``
    yields completed traces; ``recent_traces()`` is the convenience
    cursor used by tests and admin endpoints.

    The store is intentionally simple — append-only file + in-memory
    cache + lightweight query. Swap to SQLite/DuckDB when traffic
    warrants.
    """

    def __init__(self, path: Path | str, max_in_memory: int = 1024) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._cache: list[dict[str, Any]] = []
        self._max_in_memory = max_in_memory

    def write(self, trace: Trace) -> None:
        """Append a trace to disk and the in-memory cache."""
        data = trace.to_dict()
        line = json.dumps(data, default=str)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            self._cache.append(data)
            if len(self._cache) > self._max_in_memory:
                # Drop oldest half — bounded memory under sustained load.
                self._cache = self._cache[self._max_in_memory // 2 :]

    def read_traces(self) -> Iterator[dict[str, Any]]:
        """Stream all persisted traces, newest first."""
        with self._lock:
            if self._cache:
                yield from reversed(self._cache)
        if self.path.exists():
            with self.path.open("r", encoding="utf-8") as fh:
                lines = fh.readlines()
            for raw in reversed(lines):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    yield json.loads(raw)
                except json.JSONDecodeError:
                    continue

    def recent_traces(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return the most-recent N traces (newest first)."""
        out: list[dict[str, Any]] = []
        for trace in self.read_traces():
            out.append(trace)
            if len(out) >= limit:
                break
        return out

    def clear(self) -> None:
        """Reset the store (used by tests)."""
        with self._lock:
            self._cache.clear()
            if self.path.exists():
                self.path.unlink()

    def count(self) -> int:
        """Return the number of cached traces."""
        with self._lock:
            return len(self._cache)


# ── Singleton accessor + context helpers ────────────────────────────────────


_default_tracer: Tracer | None = None
_default_lock = threading.Lock()


def get_tracer() -> Tracer:
    """Return the process-wide default Tracer.

    The first call respects the ``LILITH_TRACING_ENABLED`` environment
    variable. Subsequent calls return the cached instance.
    """
    global _default_tracer
    with _default_lock:
        if _default_tracer is None:
            enabled_env = os.environ.get(TRACING_ENABLED_ENV, "1") not in (
                "0",
                "false",
                "False",
            )
            _default_tracer = Tracer(enabled=enabled_env)
        return _default_tracer


def reset_tracer() -> None:
    """Drop the singleton — useful for tests that need isolation."""
    global _default_tracer
    with _default_lock:
        _default_tracer = None


def configure_tracing(
    store: TraceStore | None = None,
    enabled: bool = True,
) -> Tracer:
    """Wire global tracing state in one call.

    Returns the updated Tracer so callers can chain further config::

        configure_tracing(store=TraceStore("./traces.jsonl"))
    """
    tracer = get_tracer()
    tracer.enabled = enabled
    tracer.set_store(store)
    return tracer


__all__ = [
    "Span",
    "SpanKind",
    "Trace",
    "TraceStore",
    "Tracer",
    "TRACING_ENABLED_ENV",
    "TRACING_FILE_ENV",
    "configure_tracing",
    "get_tracer",
    "reset_tracer",
]
