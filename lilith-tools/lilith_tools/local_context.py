"""Local context tools — expose system state for AI agents.

Inspired by iwomm-mcp (dicoy/iwomm-mcp): MCP server that exposes
local dev context (processes, Docker, git, ports, logs, env files)
so AI agents don't need copy-paste.

Tools provided:
- local_processes: list running processes (filtered)
- local_ports: list listening ports
- local_git_status: git status of a repo
- local_git_log: recent git log
- local_docker_ps: docker ps (if docker available)
- local_env: read env var or list filtered env vars
- local_disk_usage: disk usage for a path
- local_python_info: python interpreter info

All tools return ToolResult(success, data, error) like the rest of lilith-tools.
"""

from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

from .base import BaseTool, ToolResult
from .registry import ToolRegistry


def _run(cmd: list[str], timeout: float = 5.0, cwd: str | None = None) -> tuple[int, str, str]:
    """Run a subprocess safely and return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            cwd=cwd,
            shell=False,
        )
        # Decode with errors='replace' for cross-platform robustness (Windows netstat
        # can output cp1252/mbcs bytes that fail strict utf-8 decode).
        stdout = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
        stderr = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
        return result.returncode, stdout, stderr
    except FileNotFoundError:
        return -1, "", f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return -2, "", f"{cmd[0]} timed out after {timeout}s"
    except Exception as e:
        return -3, "", str(e)


# Los enumeradores del sistema (tasklist, netstat) tardan ~0.6 s en reposo,
# pero se degradan varios segundos cuando la máquina está cargada — por
# ejemplo mientras corre la suite de tests o un ciclo de subagentes. Con el
# default de 5 s fallaban de forma intermitente, así que van con margen.
_ENUM_TIMEOUT = 20.0


@ToolRegistry.register
class LocalProcessesTool(BaseTool):
    """List running processes, optionally filtered by name."""

    name = "local_processes"
    description = "Lista procesos corriendo (filtrable por nombre)"
    parameters = {
        "filter": {"type": "string", "required": False},
        "limit": {"type": "integer", "required": False, "default": 50},
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        flt = (kwargs.get("filter") or "").lower()
        limit = int(kwargs.get("limit") or 50)
        if platform.system() == "Windows":
            cmd = ["tasklist", "/FO", "CSV", "/NH"]
        else:
            cmd = ["ps", "-eo", "pid,comm", "--no-headers"]
        rc, out, err = _run(cmd, timeout=_ENUM_TIMEOUT)
        if rc != 0:
            return ToolResult(success=False, data=None, error=err or "ps failed")
        procs = []
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            # Windows CSV: "name","pid","session","sessionNum","mem"
            if "," in line and line.startswith('"'):
                parts = line.split('","')
                if len(parts) >= 2:
                    name = parts[0].strip('"')
                    pid = parts[1].strip('"')
                    procs.append({"name": name, "pid": pid})
            else:
                parts = line.split(None, 1)
                if len(parts) == 2:
                    procs.append({"pid": parts[0], "name": parts[1]})
        # El filtro va antes de recortar: al revés, buscar un nombre concreto
        # devolvía 0 resultados si no aparecía entre los primeros ``limit``
        # procesos que enumeró el sistema, que llegan en orden arbitrario.
        if flt:
            procs = [p for p in procs if flt in p.get("name", "").lower()]
        procs = procs[:limit]
        return ToolResult(success=True, data={"count": len(procs), "processes": procs})


@ToolRegistry.register
class LocalPortsTool(BaseTool):
    """List listening TCP ports (best-effort)."""

    name = "local_ports"
    description = "Lista puertos TCP escuchando (best-effort)"
    parameters = {}

    def execute(self, **kwargs: Any) -> ToolResult:
        if platform.system() == "Windows":
            cmd = ["netstat", "-an", "-p", "TCP"]
        else:
            # Try ss, fall back to netstat
            cmd = ["ss", "-tln"] if shutil.which("ss") else ["netstat", "-tln"]
        rc, out, err = _run(cmd, timeout=_ENUM_TIMEOUT)
        if rc != 0:
            return ToolResult(success=False, data=None, error=err or "netstat failed")
        ports = []
        for line in out.splitlines():
            line_lower = line.lower()
            if "listen" not in line_lower:
                continue
            # Extract last :PORT
            for tok in line.split():
                if ":" not in tok:
                    continue
                tail = tok.rsplit(":", 1)[-1].rstrip("]")
                if tail.isdigit():
                    ports.append(tail)
        # Dedup, sort
        ports = sorted(set(ports), key=int)
        return ToolResult(success=True, data={"count": len(ports), "ports": ports})


@ToolRegistry.register
class LocalGitStatusTool(BaseTool):
    """Return git status (porcelain) for a repo path."""

    name = "local_git_status"
    description = "Estado git de un repo (path)"
    parameters = {
        "path": {"type": "string", "required": True},
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        path = kwargs.get("path", "")
        if not path:
            return ToolResult(success=False, data=None, error="path is required")
        rc, out, err = _run(["git", "status", "--porcelain"], cwd=path)
        if rc != 0:
            return ToolResult(success=False, data=None, error=err or "git failed")
        entries = []
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            code = line[:2]
            filepath = line[3:] if len(line) > 3 else ""
            entries.append({"status": code, "path": filepath})
        return ToolResult(
            success=True,
            data={
                "path": path,
                "is_repo": True,
                "dirty_count": len(entries),
                "entries": entries,
            },
        )


@ToolRegistry.register
class LocalGitLogTool(BaseTool):
    """Return recent git commits for a repo path."""

    name = "local_git_log"
    description = "Log reciente de un repo (path, limit)"
    parameters = {
        "path": {"type": "string", "required": True},
        "limit": {"type": "integer", "required": False, "default": 10},
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        path = kwargs.get("path", "")
        limit = int(kwargs.get("limit") or 10)
        if not path:
            return ToolResult(success=False, data=None, error="path is required")
        fmt = "%H|%h|%an|%ae|%ad|%s"
        # ISO date, --date=iso-strict for stable format
        rc, out, err = _run(
            ["git", "log", f"--format={fmt}", "--date=iso-strict", f"-n{limit}"],
            cwd=path,
        )
        if rc != 0:
            return ToolResult(success=False, data=None, error=err or "git log failed")
        commits = []
        for line in out.splitlines():
            parts = line.split("|", 5)
            if len(parts) != 6:
                continue
            commits.append(
                {
                    "sha": parts[0],
                    "short_sha": parts[1],
                    "author": parts[2],
                    "email": parts[3],
                    "date": parts[4],
                    "subject": parts[5],
                }
            )
        return ToolResult(success=True, data={"count": len(commits), "commits": commits})


@ToolRegistry.register
class LocalDockerPsTool(BaseTool):
    """List running docker containers (if docker is installed)."""

    name = "local_docker_ps"
    description = "Lista containers docker corriendo (si docker está disponible)"
    parameters = {
        "all": {"type": "boolean", "required": False, "default": False},
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        if not shutil.which("docker"):
            return ToolResult(success=False, data=None, error="docker not installed")
        cmd = ["docker", "ps", "--format", "{{.ID}}|{{.Image}}|{{.Names}}|{{.Status}}|{{.Ports}}"]
        if kwargs.get("all"):
            cmd.append("--all")
        rc, out, err = _run(cmd, timeout=10.0)
        if rc != 0:
            return ToolResult(success=False, data=None, error=err or "docker ps failed")
        containers = []
        for line in out.splitlines():
            parts = line.split("|", 4)
            if len(parts) >= 4:
                containers.append(
                    {
                        "id": parts[0],
                        "image": parts[1],
                        "names": parts[2],
                        "status": parts[3],
                        "ports": parts[4] if len(parts) > 4 else "",
                    }
                )
        return ToolResult(
            success=True, data={"docker_available": True, "count": len(containers), "containers": containers}
        )


@ToolRegistry.register
class LocalEnvTool(BaseTool):
    """Read environment variables (filtered by prefix)."""

    name = "local_env"
    description = "Lee variables de entorno (filtrable por prefijo)"
    parameters = {
        "prefix": {"type": "string", "required": False, "default": ""},
        "name": {"type": "string", "required": False, "default": ""},
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        # Reading single var takes precedence
        name = kwargs.get("name", "")
        if name:
            val = os.environ.get(name)
            if val is None:
                return ToolResult(success=False, data=None, error=f"env var not set: {name}")
            # Mask secret-looking values
            masked = val if not any(s in name.upper() for s in ("KEY", "TOKEN", "SECRET", "PASSWORD")) else "***MASKED***"
            return ToolResult(success=True, data={"name": name, "value": masked})
        prefix = (kwargs.get("prefix") or "").upper()
        envs = {k: v for k, v in os.environ.items() if not prefix or k.upper().startswith(prefix)}
        # Mask secret-looking values in listings
        masked_envs = {}
        for k, v in envs.items():
            ku = k.upper()
            if any(s in ku for s in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
                masked_envs[k] = "***MASKED***"
            else:
                masked_envs[k] = v
        return ToolResult(success=True, data={"count": len(masked_envs), "values": masked_envs})


@ToolRegistry.register
class LocalDiskUsageTool(BaseTool):
    """Return disk usage for a path."""

    name = "local_disk_usage"
    description = "Uso de disco para una ruta"
    parameters = {
        "path": {"type": "string", "required": False, "default": "."},
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        path = kwargs.get("path", ".")
        try:
            usage = shutil.disk_usage(path)
            return ToolResult(
                success=True,
                data={
                    "path": path,
                    "total_bytes": usage.total,
                    "used_bytes": usage.used,
                    "free_bytes": usage.free,
                    "percent_used": round(100.0 * usage.used / usage.total, 2)
                    if usage.total > 0
                    else 0.0,
                },
            )
        except FileNotFoundError:
            return ToolResult(success=False, data=None, error=f"path not found: {path}")
        except Exception as e:
            return ToolResult(success=False, data=None, error=str(e))


@ToolRegistry.register
class LocalPythonInfoTool(BaseTool):
    """Return current Python interpreter info."""

    name = "local_python_info"
    description = "Información del intérprete Python actual"
    parameters = {}

    def execute(self, **kwargs: Any) -> ToolResult:
        info = {
            "executable": sys.executable,
            "version": sys.version,
            "platform": platform.platform(),
            "prefix": sys.prefix,
            "base_prefix": sys.base_prefix,
            "is_venv": sys.prefix != sys.base_prefix,
            "cwd": os.getcwd(),
            "hostname": socket.gethostname(),
        }
        return ToolResult(success=True, data=info)


__all__ = [
    "LocalProcessesTool",
    "LocalPortsTool",
    "LocalGitStatusTool",
    "LocalGitLogTool",
    "LocalDockerPsTool",
    "LocalEnvTool",
    "LocalDiskUsageTool",
    "LocalPythonInfoTool",
]
