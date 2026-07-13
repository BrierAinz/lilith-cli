"""Tests for the background ProcessManager."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from lilith_cli.process_manager import ProcessManager


@pytest.fixture
def tmp_manager(tmp_path: Path) -> ProcessManager:
    """Return a ProcessManager backed by a temporary directory."""
    return ProcessManager(state_dir=tmp_path / "processes")


def test_start_status_stop(tmp_manager: ProcessManager) -> None:
    """Start a sleeping Python process, verify status, then stop it."""
    manager = tmp_manager
    name = "test_sleep"
    # Use a short-lived process so tests are fast but long enough to inspect.
    # flush=True so stdout is written to the log file without buffering.
    command = "{} -c \"import time; print('started', flush=True); time.sleep(60)\"".format(sys.executable)

    pid = manager.start(name, command)
    assert pid is not None
    assert pid > 0

    # Allow process to print and manager to persist state.
    time.sleep(0.5)

    status = manager.status(name)
    assert status is not None
    assert status["name"] == name
    assert status["pid"] == pid
    assert status["alive"] is True
    assert status["log_file"] != ""
    assert status["command"] == command

    log = manager.get_log(name, lines=10)
    assert "started" in log

    stopped = manager.stop(name)
    assert stopped is True

    # Wait for termination.
    for _ in range(30):
        if not manager.status(name):
            break
        status = manager.status(name)
        if status and not status["alive"]:
            break
        time.sleep(0.1)

    # After stopping, the process should be gone and state cleaned up.
    assert manager.status(name) is None
    assert manager.stop(name) is False


def test_list_and_cleanup(tmp_manager: ProcessManager) -> None:
    """Verify list() returns started processes and cleanup removes dead ones."""
    manager = tmp_manager
    name = "test_list"
    command = "{} -c \"import time; time.sleep(30)\"".format(sys.executable)

    pid = manager.start(name, command)
    assert pid is not None

    processes = manager.list()
    assert any(p["name"] == name and p["alive"] for p in processes)

    manager.stop(name)

    # Stopping should remove the state file, so list() should be empty.
    assert manager.list() == []

    # Cleanup on an empty manager should be harmless.
    assert manager.cleanup() == []


def test_get_log_empty_for_missing(tmp_manager: ProcessManager) -> None:
    """get_log returns an empty string for unknown processes."""
    manager = tmp_manager
    assert manager.get_log("nonexistent", lines=10) == ""
