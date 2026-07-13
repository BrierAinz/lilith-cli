"""Tests for lilith_orchestrator.tracing — Agent Tracing system."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

from lilith_orchestrator.tracing import (
    Span,
    SpanKind,
    Trace,
    TraceStore,
    Tracer,
    configure_tracing,
    get_tracer,
    reset_tracer,
)


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_store(tmp_path: Path) -> TraceStore:
    """Per-test TraceStore backed by tmp_path/traces.jsonl."""
    return TraceStore(tmp_path / "traces.jsonl")


@pytest.fixture
def clean_tracer() -> Tracer:
    """A tracer with no global state — manual configuration per test."""
    return Tracer(service_name="test", enabled=True, store=None)


@pytest.fixture
def reset_global_tracer():
    """Snapshot and restore the global tracer for isolation."""
    reset_tracer()
    yield
    reset_tracer()


# ── Span basics ─────────────────────────────────────────────────────────────


class TestSpanBasics:
    """Span attributes and lifecycle on their own."""

    def test_new_span_has_zero_duration_until_closed(self) -> None:
        span = Span(
            span_id="abc",
            trace_id="xyz",
            parent_id=None,
            name="x",
            kind=SpanKind.REQUEST,
            start_time=time.time(),
        )
        assert span.duration_ms == 0.0
        assert span.end_time is None
        assert span.status == "ok"
        assert span.children == []

    def test_close_sets_end_time_and_duration(self) -> None:
        span = Span(
            span_id="abc",
            trace_id="xyz",
            parent_id=None,
            name="x",
            kind=SpanKind.REQUEST,
            start_time=time.time() - 0.05,
        )
        span.close()
        assert span.end_time is not None
        assert span.duration_ms > 0.0

    def test_set_attribute_is_callable_and_persists(self) -> None:
        span = Span(
            span_id="abc",
            trace_id="xyz",
            parent_id=None,
            name="x",
            kind=SpanKind.AGENT,
            start_time=time.time(),
        )
        span.set_attribute("agent", "Odin")
        span.set_attribute("intent", "research")
        assert span.attributes["agent"] == "Odin"
        assert span.attributes["intent"] == "research"

    def test_set_attributes_bulk(self) -> None:
        span = Span(
            span_id="a",
            trace_id="b",
            parent_id=None,
            name="x",
            kind=SpanKind.TOOL,
            start_time=time.time(),
        )
        span.set_attributes(query="hello", tool="search_files", n=3)
        assert span.attributes == {
            "query": "hello",
            "tool": "search_files",
            "n": 3,
        }

    def test_record_exception_marks_status(self) -> None:
        span = Span(
            span_id="a",
            trace_id="b",
            parent_id=None,
            name="x",
            kind=SpanKind.GATE,
            start_time=time.time(),
        )
        span.record_exception("denied by policy")
        assert span.status == "error"
        assert span.error_message == "denied by policy"

    def test_to_dict_is_json_serializable(self) -> None:
        span = Span(
            span_id="a",
            trace_id="b",
            parent_id=None,
            name="hello",
            kind=SpanKind.REQUEST,
            start_time=time.time(),
        )
        span.set_attribute("k", "v")
        span.close()
        # Must be JSON-serializable without explicit encoder support.
        data = span.to_dict()
        json.dumps(data)
        assert data["name"] == "hello"
        assert data["attributes"]["k"] == "v"
        assert data["status"] == "ok"

    def test_to_dict_recurses_into_children(self) -> None:
        root = Span(
            span_id="root",
            trace_id="t",
            parent_id=None,
            name="r",
            kind=SpanKind.REQUEST,
            start_time=time.time(),
        )
        child = Span(
            span_id="c1",
            trace_id="t",
            parent_id="root",
            name="c",
            kind=SpanKind.TOOL,
            start_time=time.time(),
        )
        child.close()
        root.children.append(child)
        root.close()

        data = root.to_dict()
        assert len(data["children"]) == 1
        assert data["children"][0]["name"] == "c"
        assert data["children"][0]["parent_id"] == "root"


# ── Tracer behavior ─────────────────────────────────────────────────────────


class TestTracerBasics:
    """Tracer-level behavior: span tree assembly, threading, disabled mode."""

    def test_trace_request_emits_root(self, clean_tracer: Tracer) -> None:
        with clean_tracer.trace_request("chat", user_id="u1") as root:
            assert root.kind == SpanKind.REQUEST
            assert root.parent_id is None
            assert root.attributes["user_id"] == "u1"
            assert root.attributes["_service"] == "test"

    def test_nested_spans_attach_to_parent(
        self, clean_tracer: Tracer
    ) -> None:
        with clean_tracer.trace_request("req") as root:
            with clean_tracer.span(SpanKind.AGENT, "odin.dispatch") as agent:
                with clean_tracer.span(SpanKind.TOOL, "search_files") as tool:
                    tool.set_attribute("q", "hello")

        # Verify the tree at close time:
        assert len(root.children) == 1
        agent_span = root.children[0]
        assert agent_span.name == "odin.dispatch"
        assert agent_span.kind == SpanKind.AGENT
        assert len(agent_span.children) == 1
        tool_span = agent_span.children[0]
        assert tool_span.parent_id == agent_span.span_id
        assert tool_span.attributes["q"] == "hello"

    def test_orphan_spans_become_roots(self, clean_tracer: Tracer) -> None:
        with clean_tracer.span(SpanKind.TOOL, "standalone") as orphan:
            assert orphan.parent_id is None
            assert orphan.trace_id != "0"

    def test_tracing_disabled_yields_noop_spans(self) -> None:
        tracer = Tracer(service_name="t", enabled=False)
        with tracer.trace_request("req") as root:
            assert root.attributes["tracing"] == "disabled"
            with tracer.span(SpanKind.AGENT, "a") as child:
                assert child.attributes["tracing"] == "disabled"

    def test_env_disables_tracing(self, monkeypatch) -> None:
        monkeypatch.setenv("LILITH_TRACING_ENABLED", "0")
        tracer = Tracer(service_name="t", enabled=True)
        with tracer.trace_request("req") as root:
            assert root.attributes["tracing"] == "disabled"

    def test_span_with_exception_marks_status(self, clean_tracer: Tracer) -> None:
        with pytest.raises(RuntimeError, match="boom"):
            with clean_tracer.trace_request("req") as root:
                with clean_tracer.span(SpanKind.TOOL, "fail") as span:
                    raise RuntimeError("boom")

        # The root should have observed the exception.
        assert root.status == "error"
        assert root.error_message == "boom"

    def test_active_tracks_thread_isolation(self, clean_tracer: Tracer) -> None:
        """Spans on different threads should produce disjoint trees."""
        results: dict[str, list[str]] = {}

        def worker(name: str) -> None:
            with clean_tracer.trace_request(f"thread-{name}") as root:
                with clean_tracer.span(SpanKind.AGENT, "agent") as child:
                    results[name] = [root.span_id, child.span_id]

        t1 = threading.Thread(target=worker, args=("a",))
        t2 = threading.Thread(target=worker, args=("b",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert results["a"][0] != results["b"][0]
        assert results["a"][1] != results["b"][1]

    def test_persists_trace_via_store(self, tmp_store: TraceStore) -> None:
        tracer = Tracer(service_name="test", enabled=True, store=tmp_store)
        with tracer.trace_request("persisted") as root:
            with tracer.span(SpanKind.AGENT, "odin") as agent:
                agent.set_attribute("intent", "code")

        assert tmp_store.count() == 1
        data = tmp_store.recent_traces(limit=1)[0]
        assert data["root_name"] == "persisted"
        assert data["spans"][0]["attributes"]["_service"] == "test"
        assert data["spans"][0]["children"][0]["name"] == "odin"


# ── TraceStore behavior ─────────────────────────────────────────────────────


class TestTraceStore:
    """TraceStore: append, read, query, clear."""

    def test_write_creates_file_and_caches(self, tmp_path: Path) -> None:
        path = tmp_path / "t.jsonl"
        store = TraceStore(path)
        span = Span(
            span_id="a",
            trace_id="b",
            parent_id=None,
            name="x",
            kind=SpanKind.REQUEST,
            start_time=time.time(),
        )
        span.close()
        store.write(Trace(root=span))

        assert path.exists()
        assert store.count() == 1

    def test_write_appends_jsonl_lines(self, tmp_path: Path) -> None:
        path = tmp_path / "t.jsonl"
        store = TraceStore(path)
        for i in range(3):
            span = Span(
                span_id=str(i),
                trace_id=str(i),
                parent_id=None,
                name=f"trace-{i}",
                kind=SpanKind.REQUEST,
                start_time=time.time(),
            )
            span.close()
            store.write(Trace(root=span))

        lines = path.read_text().strip().split("\n")
        assert len(lines) == 3
        # Newest-first read order:
        names = [json.loads(line)["root_name"] for line in reversed(lines)]
        assert names == ["trace-2", "trace-1", "trace-0"]

    def test_recent_traces_limits(self, tmp_path: Path) -> None:
        path = tmp_path / "t.jsonl"
        store = TraceStore(path)
        for i in range(10):
            span = Span(
                span_id=str(i),
                trace_id=str(i),
                parent_id=None,
                name=f"trace-{i}",
                kind=SpanKind.REQUEST,
                start_time=time.time(),
            )
            span.close()
            store.write(Trace(root=span))

        assert len(store.recent_traces(limit=5)) == 5

    def test_clear_empties_cache_and_file(self, tmp_store: TraceStore) -> None:
        span = Span(
            span_id="a",
            trace_id="b",
            parent_id=None,
            name="x",
            kind=SpanKind.REQUEST,
            start_time=time.time(),
        )
        span.close()
        tmp_store.write(Trace(root=span))
        assert tmp_store.count() == 1

        tmp_store.clear()
        assert tmp_store.count() == 0
        assert not tmp_store.path.exists()

    def test_corrupt_lines_are_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "t.jsonl"
        path.write_text("not-json\n{}\n", encoding="utf-8")
        store = TraceStore(path)
        # Manually inject a valid trace so read_traces has something:
        span = Span(
            span_id="a",
            trace_id="b",
            parent_id=None,
            name="x",
            kind=SpanKind.REQUEST,
            start_time=time.time(),
        )
        span.close()
        store.write(Trace(root=span))
        traces = list(store.read_traces())
        # The valid trace comes through despite a corrupt line being skipped.
        assert any(t["root_name"] == "x" for t in traces)

    def test_cache_bounded_under_load(self, tmp_path: Path) -> None:
        path = tmp_path / "t.jsonl"
        store = TraceStore(path, max_in_memory=10)
        for i in range(20):
            span = Span(
                span_id=str(i),
                trace_id=str(i),
                parent_id=None,
                name=f"trace-{i}",
                kind=SpanKind.REQUEST,
                start_time=time.time(),
            )
            span.close()
            store.write(Trace(root=span))
        # Cache should be bounded, not unbounded.
        assert store.count() <= 10


# ── Global singleton behavior ───────────────────────────────────────────────


class TestGlobalTracer:
    """Global accessor + configure_tracing conveniences."""

    def test_get_tracer_returns_singleton(
        self, reset_global_tracer
    ) -> None:
        a = get_tracer()
        b = get_tracer()
        assert a is b

    def test_reset_tracer_drops_singleton(
        self, reset_global_tracer
    ) -> None:
        first = get_tracer()
        reset_tracer()
        second = get_tracer()
        assert first is not second

    def test_configure_tracing_wires_store(
        self, tmp_store: TraceStore, reset_global_tracer
    ) -> None:
        tracer = configure_tracing(store=tmp_store, enabled=True)
        assert tracer is get_tracer()
        with tracer.trace_request("configured") as root:
            with tracer.span(SpanKind.AGENT, "a") as agent:
                agent.set_attribute("k", "v")

        # The trace should have been persisted via the wired store.
        persisted = tmp_store.recent_traces(limit=1)
        assert len(persisted) == 1
        assert persisted[0]["spans"][0]["children"][0]["name"] == "a"


# ── Realistic end-to-end ────────────────────────────────────────────────────


class TestEndToEndScenario:
    """A realistic multi-agent request — verifies the full tree shape."""

    def test_chat_request_tree(
        self, tmp_store: TraceStore, clean_tracer: Tracer
    ) -> None:
        clean_tracer.set_store(tmp_store)

        with clean_tracer.trace_request(
            "chat", session_id="s1", user_query="hello world"
        ) as root:
            # Gate evaluation phase:
            with clean_tracer.span(SpanKind.GATE, "policy.check") as gate:
                gate.set_attribute("policy", "odin-tool-restrict")
                gate.set_attribute("verdict", "allow")

            # Agent dispatch:
            with clean_tracer.span(
                SpanKind.AGENT, "odin.dispatch"
            ) as odin:
                odin.set_attributes(agent="Odin", intent="research")

                # Tool invocation:
                with clean_tracer.span(
                    SpanKind.TOOL, "search_files"
                ) as search:
                    search.set_attributes(query="hello", n_results=3)

                # Memory recall:
                with clean_tracer.span(SpanKind.MEMORY, "memory.recall") as mem:
                    mem.set_attribute("limit", 5)

            # Self-correction judge:
            with clean_tracer.span(SpanKind.JUDGE, "judge.evaluate") as judge:
                judge.set_attributes(verdict="approved", confidence=0.92)

        # Validate structure
        assert root.attributes["session_id"] == "s1"
        assert len(root.children) == 3  # gate + agent + judge

        odin_span = next(c for c in root.children if c.name == "odin.dispatch")
        assert len(odin_span.children) == 2  # search + memory

        # The whole tree persisted to disk:
        persisted = tmp_store.recent_traces(limit=1)[0]
        assert persisted["root_name"] == "chat"
        # Find the judge span inside the serialized tree:
        judge_node = persisted["spans"][0]["children"][2]
        assert judge_node["kind"] == "judge"
        assert judge_node["attributes"]["verdict"] == "approved"

    def test_span_durations_are_monotonic_and_positive(
        self, clean_tracer: Tracer
    ) -> None:
        with clean_tracer.trace_request("r") as root:
            with clean_tracer.span(SpanKind.AGENT, "a") as a:
                time.sleep(0.01)
                with clean_tracer.span(SpanKind.TOOL, "t") as t:
                    time.sleep(0.005)

        # Children close before root closes.
        assert root.end_time >= a.end_time >= t.end_time
        assert root.duration_ms >= a.duration_ms >= t.duration_ms > 0
