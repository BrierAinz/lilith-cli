"""Background process manager for long-running dev servers and watchers.

Provides a persistent process manager that stores PID and log metadata in
``~/.yggdrasil/processes/<name>.json``. Processes are spawned with
``subprocess.Popen`` and stdout/stderr redirected to a rolling log file.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


class ProcessManager:
    """Manage long-running background processes for Lilith CLI.

    Parameters
    ----------
    state_dir:
        Directory where process state files and logs are stored. Defaults to
        ``~/.yggdrasil/processes``.

    """

    def __init__(self, state_dir: str | Path | None = None) -> None:
        self._state_dir = Path(state_dir or Path.home() / ".yggdrasil" / "processes")
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._log_dir = self._state_dir / "logs"
        self._log_dir.mkdir(parents=True, exist_ok=True)

    # ── State helpers ──────────────────────────────────────────────────

    def _state_path(self, name: str) -> Path:
        return self._state_dir / f"{name}.json"

    def _log_path(self, name: str) -> Path:
        return self._log_dir / f"{name}.log"

    def _load_state(self, name: str) -> dict[str, Any] | None:
        path = self._state_path(name)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def _save_state(self, name: str, state: dict[str, Any]) -> None:
        path = self._state_path(name)
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def _remove_state(self, name: str) -> None:
        path = self._state_path(name)
        if path.exists():
            path.unlink()

    # ── Process lifecycle ──────────────────────────────────────────────

    def _is_alive(self, pid: int) -> bool:
        """Return True if *pid* is a running process."""
        if pid <= 0:
            return False
        if sys.platform == "win32":
            # NUNCA usar os.kill(pid, 0) en Windows: cualquier señal que no
            # sea CTRL_C/CTRL_BREAK llama a TerminateProcess y MATA el proceso.
            import ctypes

            kernel = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            handle = kernel.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            if not handle:
                return False
            try:
                exit_code = ctypes.c_ulong()
                if not kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return False
                return exit_code.value == STILL_ACTIVE
            finally:
                kernel.CloseHandle(handle)
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def _detect_port(self, pid: int) -> int | None:
        """Try to detect the listening port for *pid* (best effort)."""
        try:
            import psutil

            proc = psutil.Process(pid)
            for conn in proc.connections(kind="inet"):
                if conn.status == psutil.CONN_LISTEN:
                    return conn.laddr.port
        except Exception:
            pass
        return None

    def _kill(self, pid: int) -> bool:
        """Terminate a process group (POSIX) or a single process (Windows)."""
        try:
            if sys.platform == "win32":
                import ctypes

                kernel = ctypes.windll.kernel32
                handle = kernel.OpenProcess(1, False, pid)
                if handle:
                    kernel.TerminateProcess(handle, 0)
                    kernel.CloseHandle(handle)
                    return True
                return False
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except ProcessLookupError:
                os.kill(pid, signal.SIGTERM)
            return True
        except Exception:
            return False

    def _ensure_unique_name(self, name: str) -> str:
        """If *name* is already in use by a dead process, clean it up first."""
        state = self._load_state(name)
        if state and not self._is_alive(state.get("pid", 0)):
            self._remove_state(name)
        return name

    def start(self, name: str, command: str, cwd: str | None = None) -> int | None:
        """Start a long-running background process.

        Args:
            name: Unique human-readable name for the process.
            command: Shell command to execute (e.g. ``python -m http.server``).
            cwd: Optional working directory.

        Returns:
            The PID of the started process, or ``None`` if it could not be started.

        """
        name = self._ensure_unique_name(name)
        if self._load_state(name) is not None:
            return None

        log_file = self._log_path(name)
        # Truncate or create the log file.
        log_file.write_text("", encoding="utf-8")

        stdout_handle = open(log_file, "w", encoding="utf-8", errors="ignore")
        kwargs: dict[str, Any] = {
            "stdout": stdout_handle,
            "stderr": subprocess.STDOUT,
            "shell": True,
            "cwd": cwd,
        }
        if sys.platform != "win32":
            kwargs["start_new_session"] = True

        try:
            proc = subprocess.Popen(command, **kwargs)
        except OSError:
            return None

        # Give the process a moment to fail, and verify it launched.
        time.sleep(0.2)
        if proc.poll() is not None:
            return None

        # Detach stdout so the child can own it without holding our handle.
        # Keep a reference to the open file so we can close it after the process
        # is detached. On Windows, closing the parent's handle is necessary so
        # the child process can write to the log file without buffering issues.
        try:
            proc.stdout.detach()
        except (AttributeError, OSError):
            pass
        finally:
            with contextlib.suppress(Exception):
                stdout_handle.close()

        pid = proc.pid
        state = {
            "name": name,
            "pid": pid,
            "command": command,
            "cwd": cwd,
            "log_file": str(log_file.resolve()),
            "created_at": time.time(),
        }
        self._save_state(name, state)
        return pid

    def status(self, name: str) -> dict[str, Any] | None:
        """Return status for a single process, or ``None`` if unknown."""
        state = self._load_state(name)
        if not state:
            return None
        pid = state.get("pid", 0)
        alive = self._is_alive(pid)
        port = self._detect_port(pid) if alive else None
        return {
            "name": name,
            "pid": pid,
            "alive": alive,
            "port": port,
            "log_file": state.get("log_file", ""),
            "command": state.get("command", ""),
        }

    def stop(self, name: str) -> bool:
        """Stop a background process and clean up its state.

        Args:
            name: Process name.

        Returns:
            ``True`` if the process was stopped or already dead, ``False`` on error.

        """
        state = self._load_state(name)
        if not state:
            return False
        pid = state.get("pid", 0)
        alive = self._is_alive(pid)
        if alive:
            if not self._kill(pid):
                return False
            # Wait briefly for the process to terminate.
            for _ in range(20):
                if not self._is_alive(pid):
                    break
                time.sleep(0.1)
        self._remove_state(name)
        return True

    def list(self) -> list[dict[str, Any]]:
        """Return the status of all registered background processes."""
        processes: list[dict[str, Any]] = []
        for path in sorted(self._state_dir.glob("*.json")):
            name = path.stem
            status = self.status(name)
            if status is not None:
                processes.append(status)
        return processes

    def get_log(self, name: str, lines: int = 50) -> str:
        """Return the last *lines* lines of a process log file.

        Returns an empty string if the process or log file is missing.
        """
        state = self._load_state(name)
        if not state:
            return ""
        log_file = Path(state.get("log_file", self._log_path(name)))
        if not log_file.exists():
            return ""
        try:
            text = log_file.read_text(encoding="utf-8", errors="ignore")
            return "\n".join(text.splitlines()[-lines:])
        except OSError:
            return ""

    def cleanup(self) -> list[str]:
        """Remove stale state files for dead processes.

        Returns the list of names that were cleaned up.
        """
        cleaned: list[str] = []
        for path in list(self._state_dir.glob("*.json")):
            name = path.stem
            state = self._load_state(name)
            if state and not self._is_alive(state.get("pid", 0)):
                self._remove_state(name)
                cleaned.append(name)
        return cleaned
