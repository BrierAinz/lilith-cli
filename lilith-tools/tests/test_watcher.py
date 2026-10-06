"""File watches record the events their observer reports."""

from __future__ import annotations

import time

from lilith_tools.watcher import _WatchEntry, _WatchManager


def _wait_for_events(manager: _WatchManager, watch_id: str, timeout: float = 6.0) -> list:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        events = manager.events(watch_id) or []
        if events:
            return events
        time.sleep(0.1)
    return []


def test_watch_records_a_created_file(tmp_path):
    """Regression: observers called entry._add_event, which did not exist, so
    every event raised AttributeError in the observer thread."""
    manager = _WatchManager()
    watch_id, ok = manager.start([str(tmp_path)])
    assert ok
    try:
        time.sleep(1.2)  # let the polling observer take its first snapshot
        (tmp_path / "created.txt").write_text("x", encoding="utf-8")
        events = _wait_for_events(manager, watch_id)
    finally:
        manager.stop(watch_id)
    assert any(event["path"].endswith("created.txt") for event in events)


def test_entry_filters_events_by_pattern(tmp_path):
    entry = _WatchEntry(
        watch_id="w", paths=[str(tmp_path)], patterns=["*.py"], ignore_patterns=[]
    )
    entry._add_event("created", str(tmp_path / "keep.py"))
    entry._add_event("created", str(tmp_path / "skip.txt"))
    assert [event.path for event in entry.events] == [str(tmp_path / "keep.py")]
