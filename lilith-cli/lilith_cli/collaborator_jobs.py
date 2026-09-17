"""Operator-facing read-only collaborator job observations; no model required."""

from __future__ import annotations

import json
import sqlite3
from typing import Annotated

from cyclopts import App, Parameter

jobs_app = App(name="jobs", help="Consultar trabajos de colaboradores sin relanzarlos.")


@jobs_app.command(name="recent")
def recent_jobs() -> None:
    """Mostrar las últimas 20 referencias guardadas, sin consultar ni ejecutar workers."""
    from lilith_tools.cli_job_journal import CliJobJournal

    try:
        rows = CliJobJournal().recent()
    except (OSError, ValueError, sqlite3.Error):
        print(json.dumps({"schema_version": 1, "error": "journal_unavailable"}))
        raise SystemExit(2) from None
    print(json.dumps({"schema_version": 1, "references": rows}, ensure_ascii=True))


@jobs_app.command(name="inspect")
def inspect_job(
    agent: str,
    job_id: str,
    json_output: Annotated[bool, Parameter(name="--json")] = False,
) -> None:
    """Leer el marcador de un trabajo Vor/Huginn; no ejecuta ni cancela tareas.

    Exit 0: marcador observado (el trabajo puede haber fallado).
    Exit 2: entrada inválida o lectura fallida. Exit 3: estado desconocido.
    """
    from lilith_tools.cli_job_inspect import CliJobInspectTool

    result = CliJobInspectTool().execute(agent=agent, job_id=job_id)
    data = result.data
    if json_output:
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "observation_ok": result.success,
                    "observation": data,
                    "error": result.error,
                },
                ensure_ascii=True,
            )
        )
    elif not result.success:
        print("No se pudo consultar el trabajo. Usa --json para ver el diagnóstico.")
    else:
        descriptions = {
            "reported_success": "El marcador reporta salida 0; la tarea aún requiere revisión.",
            "reported_failure": "El marcador reporta un fallo del colaborador.",
            "unknown": "Estado desconocido: falta el marcador; no prueba que siga activo.",
        }
        print(f"{agent} / {job_id}")
        print(descriptions[data["status"]])
        if data["job_returncode"] is not None:
            print(f"Código reportado: {data['job_returncode']}")
        if data.get("cleanup_status") == "manual_recovery_required":
            print("La reserva de trabajo requiere recuperación manual; no vuelvas a despachar sobre ella.")
        print("No se relanzó ni canceló nada. No se verificó el contenido del trabajo.")
    if not result.success:
        raise SystemExit(2)
    if data["status"] == "unknown":
        raise SystemExit(3)


@jobs_app.command(name="inspect-reference")
def inspect_reference(reference: str) -> None:
    """Consultar una referencia guardada. No repite la delegación; salida JSON."""
    from lilith_tools.cli_job_journal import CliJobJournal

    try:
        saved = CliJobJournal().lookup(reference)
    except (OSError, ValueError, sqlite3.Error):
        print(json.dumps({"schema_version": 1, "error": "reference_unavailable"}))
        raise SystemExit(2) from None
    if saved is None or saved["job_id"] is None:
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "unresolved_reference",
                    "reason": "reference_missing"
                    if saved is None
                    else "worker_id_not_recorded",
                    "retry_safe": False,
                    "execution_performed": False,
                }
            )
        )
        raise SystemExit(3)
    inspect_job(saved["agent"], saved["job_id"], json_output=True)
