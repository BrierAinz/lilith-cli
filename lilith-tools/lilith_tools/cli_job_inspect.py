"""Read-only observations of existing launcher job markers, never re-dispatch."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, ClassVar

from .base import BaseTool, ToolResult
from .registry import ToolRegistry

JOB_ROOT_ENVS = {
    "Vor": "LILITH_VOR_JOB_ROOT",
    "Huginn": "LILITH_HUGINN_JOB_ROOT",
}

def _job_root_from_env(name: str) -> Path | None:
    raw = os.environ.get(name, "").strip()
    return Path(raw).expanduser() if raw else None


JOB_ROOTS = {agent: _job_root_from_env(env) for agent, env in JOB_ROOT_ENVS.items()}
JOB_ID = re.compile(r"[0-9]{8}-[0-9]{6}-[0-9]{4}")


@ToolRegistry.register
class CliJobsRecentTool(BaseTool):
    name = "cli_jobs_recent"
    description = "Lista referencias globales de delegación guardadas; solo lectura, no inicia ni reintenta workers."
    parameters: ClassVar[dict[str, Any]] = {
        "limit": {
            "type": "integer",
            "required": False,
            "minimum": 1,
            "maximum": 20,
            "description": "Cantidad de referencias (por defecto 3)",
        },
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        from .cli_job_journal import CliJobJournal

        limit = kwargs.get("limit", 3)
        if type(limit) is not int or not 1 <= limit <= 20:
            return ToolResult(False, {"status": "invalid_input"}, "limit must be 1..20")
        try:
            rows = CliJobJournal().recent(limit)
        except (OSError, ValueError, sqlite3.Error):
            return ToolResult(
                False,
                {"status": "journal_unavailable"},
                "Cannot read delegation journal",
            )
        return ToolResult(
            True,
            {
                "references": rows,
                "scope": "global_not_project_filtered",
                "execution_performed": False,
                "retry_safe": False,
            },
        )


@ToolRegistry.register
class CliJobReferenceTool(BaseTool):
    name = "cli_job_reference"
    description = "Consulta por referencia guardada el marcador actual de un trabajo; no relanza ni cancela."
    parameters: ClassVar[dict[str, Any]] = {
        "reference": {"type": "string", "required": True, "pattern": "^[a-f0-9]{32}$"},
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        from .cli_job_journal import CliJobJournal

        try:
            saved = CliJobJournal().lookup(kwargs.get("reference"))
        except (OSError, ValueError, sqlite3.Error):
            return ToolResult(
                False,
                {"status": "reference_unavailable"},
                "Cannot read saved reference",
            )
        if saved is None or saved["job_id"] is None:
            return ToolResult(
                True,
                {
                    "status": "unresolved_reference",
                    "execution_performed": False,
                    "retry_safe": False,
                    "task_verified": False,
                },
            )
        result = CliJobInspectTool().execute(
            agent=saved["agent"], job_id=saved["job_id"]
        )
        result.data["reference"] = saved["reference"]
        return result


def _cleanup_observation(root: Path, job_id: str, code: int) -> str:
    """Read only cleanup evidence; never expose a worker's lease token or PID."""
    path = root / f"{job_id}.done.worker.json"
    try:
        resolved = path.resolve(strict=True)
        if resolved != path or not resolved.is_file():
            return "unreadable"
        with resolved.open("rb") as stream:
            raw = stream.read(16385)
        if len(raw) > 16384:
            return "unreadable"
        from .cli_job_journal import _unique_pairs

        receipt = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_pairs)
        if not isinstance(receipt, dict):
            return "unreadable"
        if type(receipt.get("workerExit")) is not int or receipt["workerExit"] != code:
            return "unreadable"
        if any(
            type(receipt.get(key)) is not bool
            for key in ("workerCompleted", "leaseReleased", "requiresManualRecovery")
        ):
            return "unreadable"
        released = receipt["leaseReleased"]
        if receipt["requiresManualRecovery"] == released or (
            released and not receipt["workerCompleted"]
        ):
            return "unreadable"
        return "reported_released" if released else "manual_recovery_required"
    except FileNotFoundError:
        return "unknown"
    except (OSError, ValueError, RecursionError):
        return "unreadable"


@ToolRegistry.register
class CliJobInspectTool(BaseTool):
    """Observe a named job's .done marker without reading tasks or transcripts."""

    name = "cli_job_inspect"
    description = (
        "Consulta solo lectura de un trabajo Vor/Huginn por ID. No relanza ni "
        "cancela; un marcador ausente no prueba que el proceso siga activo."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "agent": {"type": "string", "required": True, "enum": ["Vor", "Huginn"]},
        "job_id": {"type": "string", "required": True},
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        agent, job_id = kwargs.get("agent"), kwargs.get("job_id")
        if (
            not isinstance(agent, str)
            or agent not in JOB_ROOTS
            or not isinstance(job_id, str)
            or not JOB_ID.fullmatch(job_id)
        ):
            return ToolResult(
                success=False,
                data={"status": "invalid_input"},
                error="Provide agent Vor/Huginn and job_id YYYYMMDD-HHMMSS-NNNN",
            )
        data = {
            "agent": agent,
            "job_id": job_id,
            "status": "unknown",
            "job_returncode": None,
            "process_state": "not_checked",
            "task_verified": False,
            "retry_safe": False,
            "execution_performed": False,
        }
        configured_root = JOB_ROOTS.get(agent)
        if configured_root is None:
            data["status"] = "root_unavailable"
            return ToolResult(
                success=False,
                data=data,
                error=f"Job root is not configured; set {JOB_ROOT_ENVS[agent]}",
            )
        try:
            root = configured_root.resolve(strict=True)
            if not root.is_dir():
                raise ValueError("Job root is not a directory")
        except (OSError, ValueError) as exc:
            data["status"] = "root_unavailable"
            return ToolResult(success=False, data=data, error=str(exc))
        try:
            marker = root / f"{job_id}.done"
            resolved = marker.resolve(strict=True)
            # A symlink/junction must not turn an ID into an arbitrary file read.
            if resolved != marker or not resolved.is_file():
                raise ValueError("Job marker is redirected or not a regular file")
            with resolved.open("rb") as stream:
                raw = stream.read(257)
            if len(raw) > 256:
                raise ValueError("Job marker exceeds size limit")
            text = raw.decode("utf-8-sig").strip()
            if not re.fullmatch(r"-?[0-9]{1,10}", text):
                raise ValueError("Job marker does not contain an integer exit code")
            code = int(text)
            if not -(2**31) <= code < 2**31:
                raise ValueError("Job exit code is outside the launcher range")
        except FileNotFoundError:
            data["reason"] = "marker_missing_job_may_be_active_or_absent"
            return ToolResult(success=True, data=data)
        except (OSError, ValueError) as exc:
            data["status"] = "unreadable"
            return ToolResult(success=False, data=data, error=str(exc))
        data.update(
            status="reported_success" if code == 0 else "reported_failure",
            job_returncode=code,
            evidence="local_done_marker_unverified",
            cleanup_status=_cleanup_observation(root, job_id, code)
            if agent == "Vor"
            else "unknown",
        )
        # Tool success means the observation succeeded, NOT the delegated task.
        return ToolResult(success=True, data=data)
