"""Coding workflow tools: run tests, lint, and auto-format files.

These tools are designed to be invoked by the agent during coding tasks.
They run external CLI tools in subprocesses with timeouts and report
structured results so the LLM can reason about failures and next steps.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from .base import BaseTool, ToolResult
from .registry import ToolRegistry
from .undo import UndoManager


# ── Helpers ─────────────────────────────────────────────────────────


def _find_project_root(path: str) -> Path:
    """Return the best working directory for a test/lint command.

    If *path* is a file, use its parent directory. If it is a directory,
    use it directly. expanduser() is applied for convenience.
    """
    p = Path(path).expanduser()
    if p.is_file():
        return p.parent
    return p


def _run_command(
    command: list[str] | str,
    cwd: Path,
    timeout: int,
) -> tuple[str, str, int]:
    """Run *command* in *cwd* with *timeout* and return (stdout, stderr, rc).

    String commands (e.g. '"...python.exe" -m pytest', 'npm test') are split
    with shlex so quoted interpreter paths survive on POSIX too; Windows
    CreateProcess accepted them either way.

    Raises ``subprocess.TimeoutExpired`` on timeout so callers can wrap it
    into a friendly ToolResult error.
    """
    if isinstance(command, str) and os.name != "nt":
        command = shlex.split(command)
    proc = subprocess.run(
        command,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return proc.stdout, proc.stderr, proc.returncode


def _which(cmd: str) -> str | None:
    """Return the absolute path to *cmd* if it exists in PATH, else None."""
    return shutil.which(cmd)


def _detect_test_command(path: str, test_command: str | None) -> str | None:
    """Auto-detect a sensible test command when none is provided.

    Priority:
    1. User-supplied ``test_command`` if given and non-empty.
    2. ``pytest`` when Python files are present or ``pyproject.toml`` exists.
    3. ``npm test`` when ``package.json`` exists.
    4. ``cargo test`` when ``Cargo.toml`` exists.
    5. ``go test ./...`` when ``go.mod`` exists.
    """
    if test_command is not None and test_command.strip():
        return test_command.strip()

    root = _find_project_root(path)

    has_pyproject = (root / "pyproject.toml").exists()
    has_setup = (root / "setup.py").exists() or (root / "setup.cfg").exists()
    has_python = any(root.rglob("*.py")) if root.exists() else False
    if has_pyproject or has_setup or has_python:
        return f'"{sys.executable}" -m pytest'

    if (root / "package.json").exists():
        return "npm test"

    if (root / "Cargo.toml").exists():
        return "cargo test"

    if (root / "go.mod").exists():
        return "go test ./..."

    return None


def _detect_linter(path: str, linter: str | None) -> str | None:
    """Auto-detect a sensible linter when none is provided.

    Priority:
    1. User-supplied ``linter`` if given and non-empty.
    2. ``ruff check .`` for Python projects.
    3. ``eslint .`` for JavaScript/TypeScript projects.
    4. ``cargo clippy`` for Rust projects.
    5. ``golangci-lint run`` for Go projects.
    """
    if linter is not None and linter.strip():
        return linter.strip()

    root = _find_project_root(path)

    if any(root.rglob("*.py")) if root.exists() else False:
        return "ruff check ." if _which("ruff") else "python -m py_compile ."

    if any(root.rglob("*.js")) or any(root.rglob("*.ts")) or (root / "package.json").exists():
        return "eslint ." if _which("eslint") else "npx eslint ."

    if (root / "Cargo.toml").exists():
        return "cargo clippy -- -D warnings" if _which("cargo") else None

    if (root / "go.mod").exists():
        return "golangci-lint run" if _which("golangci-lint") else "go vet ./..."

    return None


def _detect_formatter(path: str, formatter: str | None) -> str | None:
    """Auto-detect a sensible formatter for a single file.

    Priority:
    1. User-supplied ``formatter`` if given and non-empty.
    2. ``black`` for ``.py`` files.
    3. ``prettier --write`` for JS/TS/CSS/HTML/JSON/YAML/Markdown.
    4. ``rustfmt`` for ``.rs`` files.
    5. ``gofmt`` for ``.go`` files.
    """
    if formatter is not None and formatter.strip():
        return formatter.strip()

    p = Path(path).expanduser()
    suffix = p.suffix.lower()

    if suffix == ".py":
        return "black" if _which("black") else "python -m black"
    if suffix in (".js", ".ts", ".jsx", ".tsx", ".css", ".html", ".json", ".yaml", ".yml", ".md"):
        return "prettier --write" if _which("prettier") else "npx prettier --write"
    if suffix == ".rs":
        return "rustfmt" if _which("rustfmt") else "cargo fmt"
    if suffix == ".go":
        return "gofmt -w"

    return None


# ── Test tool ───────────────────────────────────────────────────────


@ToolRegistry.register
class RunTestTool(BaseTool):
    """Run a project's test suite in a subprocess.

    Detects the test command automatically from project markers (``pytest``,
    ``npm test``, ``cargo test``, ``go test ./...``) when ``test_command`` is
    not provided. Honors the configured timeout.
    """

    name = "run_test"
    description = "Ejecuta tests del proyecto en un subproceso con timeout"
    parameters = {
        "path": {
            "type": "string",
            "required": False,
            "default": ".",
            "description": "Ruta al proyecto o archivo de prueba",
        },
        "test_command": {
            "type": "string",
            "required": False,
            "default": "",
            "description": "Comando de test (default: auto-detectar)",
        },
        "timeout": {
            "type": "integer",
            "required": False,
            "default": 60,
            "description": "Timeout en segundos",
        },
    }

    def execute(
        self,
        path: str = ".",
        test_command: str = "",
        timeout: int = 60,
        **_: Any,
    ) -> ToolResult:
        """Ejecuta tests del proyecto."""
        cwd = _find_project_root(path)
        if not cwd.exists():
            return ToolResult(success=False, data=None, error=f"Ruta no encontrada: {cwd}")

        cmd = _detect_test_command(path, test_command or None)
        if cmd is None:
            return ToolResult(
                success=False,
                data=None,
                error="No se pudo detectar un comando de test para el proyecto",
            )

        shell = bool(subprocess.run is not None)  # always use shell for flexible commands
        try:
            # Use the shell so compound commands like "python -m pytest" work without
            # us needing to parse quoted strings.
            stdout, stderr, rc = _run_command(cmd, cwd, timeout)
            return ToolResult(
                success=True,
                data={
                    "stdout": stdout,
                    "stderr": stderr,
                    "returncode": rc,
                    "command": cmd,
                },
            )
        except subprocess.TimeoutExpired:
            return ToolResult(success=False, data=None, error="Test execution exceeded timeout")
        except Exception as e:
            return ToolResult(success=False, data=None, error=str(e))


# ── Linter tool ───────────────────────────────────────────────────────


@ToolRegistry.register
class RunLinterTool(BaseTool):
    """Run a linter on a project and parse the output into a list of issues.

    Detects the linter automatically from project markers when ``linter`` is
    not provided. The returned ``issues`` list is a best-effort parse of the
    linter output; unsupported formats return an empty list while still
    preserving raw stdout/stderr.
    """

    name = "run_linter"
    description = "Ejecuta un linter y devuelve la salida estructurada con issues"
    parameters = {
        "path": {
            "type": "string",
            "required": False,
            "default": ".",
            "description": "Ruta al proyecto o archivo",
        },
        "linter": {
            "type": "string",
            "required": False,
            "default": "",
            "description": "Comando de linter (default: auto-detectar)",
        },
        "timeout": {
            "type": "integer",
            "required": False,
            "default": 60,
            "description": "Timeout en segundos",
        },
    }

    def execute(
        self,
        path: str = ".",
        linter: str = "",
        timeout: int = 60,
        **_: Any,
    ) -> ToolResult:
        """Ejecuta un linter y devuelve issues estructuradas."""
        cwd = _find_project_root(path)
        if not cwd.exists():
            return ToolResult(success=False, data=None, error=f"Ruta no encontrada: {cwd}")

        cmd = _detect_linter(path, linter or None)
        if cmd is None:
            return ToolResult(
                success=False,
                data=None,
                error="No se pudo detectar un linter para el proyecto",
            )

        try:
            stdout, stderr, rc = _run_command(cmd, cwd, timeout)
            combined = f"{stdout}\n{stderr}".strip()
            issues = _parse_linter_output(combined, cmd)
            return ToolResult(
                success=True,
                data={
                    "stdout": stdout,
                    "stderr": stderr,
                    "returncode": rc,
                    "command": cmd,
                    "issues": issues,
                },
            )
        except subprocess.TimeoutExpired:
            return ToolResult(success=False, data=None, error="Linter execution exceeded timeout")
        except Exception as e:
            return ToolResult(success=False, data=None, error=str(e))


# ── Format tool ───────────────────────────────────────────────────────


@ToolRegistry.register
class FormatFileTool(BaseTool):
    """Format a single file in-place using an external formatter.

    Detects the formatter from the file extension when ``formatter`` is not
    provided. The original file is backed up with ``UndoManager.backup``
    before any mutation.
    """

    name = "format_file"
    description = "Formatea un archivo in-place con un formatter externo"
    parameters = {
        "path": {
            "type": "string",
            "required": True,
            "description": "Ruta del archivo a formatear",
        },
        "formatter": {
            "type": "string",
            "required": False,
            "default": "",
            "description": "Comando de formatter (default: auto-detectar por extension)",
        },
        "timeout": {
            "type": "integer",
            "required": False,
            "default": 30,
            "description": "Timeout en segundos",
        },
    }

    def execute(
        self,
        path: str = "",
        formatter: str = "",
        timeout: int = 30,
        **_: Any,
    ) -> ToolResult:
        """Formatea un archivo in-place respaldándolo previamente."""
        if not path:
            return ToolResult(success=False, data=None, error="path es requerido")

        p = Path(path).expanduser()
        if not p.exists():
            return ToolResult(success=False, data=None, error=f"Archivo no encontrado: {p}")
        if not p.is_file():
            return ToolResult(success=False, data=None, error=f"No es un archivo: {p}")

        cmd = _detect_formatter(path, formatter or None)
        if cmd is None:
            return ToolResult(
                success=False,
                data=None,
                error="No se pudo detectar un formatter para el archivo",
            )

        # Back up the original file before formatting.
        UndoManager().backup(p, tool="format_file")

        cwd = p.parent
        try:
            # Append the target file path to the formatter command.
            full_cmd = f"{cmd} {p}"
            stdout, stderr, rc = _run_command(full_cmd, cwd, timeout)
            formatted = rc == 0
            return ToolResult(
                success=True,
                data={
                    "path": str(p.resolve()),
                    "formatted": formatted,
                    "command": full_cmd,
                    "stdout": stdout,
                    "stderr": stderr,
                    "returncode": rc,
                },
            )
        except subprocess.TimeoutExpired:
            return ToolResult(success=False, data=None, error="Format execution exceeded timeout")
        except Exception as e:
            return ToolResult(success=False, data=None, error=str(e))


# ── Linter output parsing ───────────────────────────────────────────


def _parse_linter_output(output: str, command: str) -> list[dict[str, Any]]:
    """Parse linter output into a list of issues.

    Supports a few common formats:
    - Ruff / flake8 / pylint with ``path:line:col: message``
    - ESLint ``path:line:col: message [rule]``
    - Rust / cargo with ``error[code]: message at path:line:col``
    - Generic ``path:line: message``
    """
    issues: list[dict[str, Any]] = []
    seen: set[str] = set()

    if not command:
        return issues

    cmd = command.lower()

    # Regex set for common formats; we try the most specific first.
    import re

    for line in output.splitlines():
        line = line.rstrip()
        if not line:
            continue

        # Avoid duplicate lines from summary output.
        if line in seen:
            continue
        seen.add(line)

        parsed: dict[str, Any] | None = None

        if "ruff" in cmd or "flake8" in cmd or "pylint" in cmd:
            m = re.match(r"^(.*?):(\d+):(\d+):\s+(.*)$", line)
            if m:
                parsed = {
                    "file": m.group(1).strip(),
                    "line": int(m.group(2)),
                    "column": int(m.group(3)),
                    "message": m.group(4).strip(),
                }
        elif "eslint" in cmd:
            m = re.match(r"^(.*?):(\d+):(\d+):\s+(.*?)\s+\[(.*?)\]$", line)
            if m:
                parsed = {
                    "file": m.group(1).strip(),
                    "line": int(m.group(2)),
                    "column": int(m.group(3)),
                    "message": m.group(4).strip(),
                    "rule": m.group(5).strip(),
                }
        elif "clippy" in cmd or "cargo" in cmd:
            m = re.match(r"^(error|warning)\[(.*?)\]:\s+(.*?)\s+at\s+(.*?):(\d+):(\d+)$", line)
            if m:
                parsed = {
                    "file": m.group(4).strip(),
                    "line": int(m.group(5)),
                    "column": int(m.group(6)),
                    "message": f"{m.group(1)}[{m.group(2)}]: {m.group(3).strip()}",
                }
        else:
            # Generic fallback: file:line: message
            m = re.match(r"^(.*?):(\d+):\s+(.*)$", line)
            if m:
                parsed = {
                    "file": m.group(1).strip(),
                    "line": int(m.group(2)),
                    "message": m.group(3).strip(),
                }

        if parsed:
            issues.append(parsed)

    return issues


__all__ = [
    "RunTestTool",
    "RunLinterTool",
    "FormatFileTool",
]
