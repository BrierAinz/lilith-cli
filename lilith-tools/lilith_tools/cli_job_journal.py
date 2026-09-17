"""Durable, non-executable references for CLI delegation attempts."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .orchestration_state import default_state_path

OBSERVATION_KEYS = {
    "status",
    "job_id",
    "job_id_source",
    "job_returncode",
    "returncode",
    "completion_confirmed",
    "effects_unknown",
}


def _validate_observation(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - OBSERVATION_KEYS:
        raise ValueError("Invalid observation fields")
    if value.get("status") not in (
        None,
        "ok",
        "failed",
        "unconfirmed",
        "needs_prime",
        "timeout",
    ):
        raise ValueError("Invalid observation status")
    if value.get("job_id_source") not in (None, "launcher_stdout_unverified"):
        raise ValueError("Invalid job ID source")
    job_id = value.get("job_id")
    if job_id is not None and (
        not isinstance(job_id, str)
        or not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[0-9]{4}", job_id)
    ):
        raise ValueError("Invalid observation job ID")
    for key in ("returncode", "job_returncode"):
        code = value.get(key)
        if code is not None and (type(code) is not int or not -(2**31) <= code < 2**32):
            raise ValueError("Invalid observation exit code")
    for key in ("completion_confirmed", "effects_unknown"):
        if value.get(key) is not None and type(value[key]) is not bool:
            raise ValueError("Invalid observation flag")
    return value


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate observation field")
        result[key] = value
    return result


def _decode_observation(raw: str | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, str) or len(raw) > 4096:
        raise ValueError("Invalid observation size/type")
    return _validate_observation(json.loads(raw, object_pairs_hook=_unique_pairs))


class CliJobJournal:
    def __init__(self, path: Path | None = None):
        self.path = path or default_state_path().with_name("cli_jobs.sqlite3")

    def begin(self, agent: str, task: str, timeout: int) -> str:
        """Persist intent before launching. No queued/executable task is created."""
        return self.reserve(agent, task, timeout, None, "")[0]

    def reserve(
        self,
        agent: str,
        task: str,
        timeout: int,
        request_id: str | None,
        execution_context: str,
    ) -> tuple[str, bool]:
        if not isinstance(agent, str) or agent not in ("Vor", "Huginn"):
            raise ValueError("Unknown collaborator")
        if (
            not isinstance(task, str)
            or type(timeout) is not int
            or not 1 <= timeout <= 86400
        ):
            raise ValueError("Invalid delegation task or timeout")
        if request_id is not None and (
            not isinstance(request_id, str)
            or not re.fullmatch(r"[a-f0-9]{32}", request_id)
        ):
            raise ValueError("request_id must be 32 lowercase hex characters")
        intent = hashlib.sha256(
            json.dumps(
                [agent, task, timeout, execution_context], ensure_ascii=True
            ).encode()
        ).hexdigest()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        reference = uuid.uuid4().hex
        with closing(sqlite3.connect(self.path, timeout=5)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS attempts ("
                "reference TEXT PRIMARY KEY, agent TEXT NOT NULL, "
                "task_sha256 TEXT NOT NULL, timeout INTEGER NOT NULL, "
                "created_at TEXT NOT NULL, observation TEXT)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS requests (request_id TEXT PRIMARY KEY, "
                "intent_sha256 TEXT NOT NULL, reference TEXT NOT NULL)"
            )
            if request_id is not None:
                previous = conn.execute(
                    "SELECT intent_sha256, reference FROM requests WHERE request_id=?",
                    (request_id,),
                ).fetchone()
                if previous is not None:
                    if previous[0] != intent:
                        raise ValueError(
                            "request_id is already bound to different instructions or parameters"
                        )
                    return previous[1], False
            conn.execute(
                "INSERT INTO attempts VALUES (?, ?, ?, ?, ?, NULL)",
                (
                    reference,
                    agent,
                    hashlib.sha256(task.encode("utf-8")).hexdigest(),
                    timeout,
                    datetime.now(UTC).isoformat(),
                ),
            )
            if request_id is not None:
                conn.execute(
                    "INSERT INTO requests VALUES (?, ?, ?)",
                    (request_id, intent, reference),
                )
        return reference, True

    def finish(self, reference: str, data: dict[str, Any]) -> None:
        # Explicit allowlist: no stdout, stderr, prompt, paths or model content.
        observation = {
            key: data.get(key)
            for key in (
                "status",
                "job_id",
                "job_id_source",
                "job_returncode",
                "returncode",
                "completion_confirmed",
                "effects_unknown",
            )
        }
        _validate_observation(observation)
        uri = self.path.resolve().as_uri() + "?mode=rw"
        with closing(sqlite3.connect(uri, uri=True, timeout=5)) as conn, conn:
            changed = conn.execute(
                "UPDATE attempts SET observation=? WHERE reference=? AND observation IS NULL",
                (json.dumps(observation), reference),
            ).rowcount
            if changed != 1:
                raise ValueError("Attempt missing or already has an observation")

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be 1..100")
        if not self.path.exists():
            return []
        uri = self.path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=5)) as conn:
            rows = conn.execute(
                "SELECT substr(reference,1,33), substr(agent,1,7), "
                "substr(created_at,1,65), substr(observation,1,4097) FROM attempts "
                "ORDER BY created_at DESC, reference DESC LIMIT ?",
                (limit,),
            ).fetchall()
        for ref, agent, stamp, _obs in rows:
            if (
                not isinstance(ref, str)
                or not re.fullmatch(r"[a-f0-9]{32}", ref)
                or agent not in ("Vor", "Huginn")
            ):
                raise ValueError("Invalid journal identity")
            if not isinstance(stamp, str) or len(stamp) > 64:
                raise ValueError("Invalid journal timestamp")
            datetime.fromisoformat(stamp)
        return [
            {
                "reference": ref,
                "agent": agent,
                "created_at": stamp,
                "observation": _decode_observation(obs),
                "retry_safe": False,
                "task_verified": False,
            }
            for ref, agent, stamp, obs in rows
        ]

    def lookup(self, reference: str) -> dict[str, Any] | None:
        """Read a specific saved reference; never creates or updates storage."""
        if not isinstance(reference, str) or not re.fullmatch(
            r"[a-f0-9]{32}", reference
        ):
            raise ValueError("Invalid saved reference")
        if not self.path.exists():
            return None
        uri = self.path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=5)) as conn:
            row = conn.execute(
                "SELECT substr(agent,1,7), substr(observation,1,4097) FROM attempts WHERE reference=?",
                (reference,),
            ).fetchone()
        if row is None:
            return None
        agent, raw = row
        if agent not in ("Vor", "Huginn"):
            raise ValueError("Unknown recorded collaborator")
        observation = _decode_observation(raw)
        job_id = observation.get("job_id") if observation is not None else None
        if job_id is not None and (
            not isinstance(job_id, str)
            or not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[0-9]{4}", job_id)
        ):
            raise ValueError("Invalid recorded job ID")
        return {
            "reference": reference,
            "agent": agent,
            "job_id": job_id,
            "retry_safe": False,
            "task_verified": False,
        }
