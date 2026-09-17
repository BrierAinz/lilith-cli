"""Versioned, validated session snapshot of delegation request identities."""

from __future__ import annotations

import hashlib
import json
import re


def validate_namespace(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise ValueError("Invalid delegation namespace")
    return value


def automatic_request_id(namespace: str, tool: str, arguments: dict, cwd: str) -> str:
    if validate_namespace(namespace) is None:
        raise ValueError("Missing delegation namespace")
    payload = [
        namespace,
        tool,
        {key: value for key, value in arguments.items() if key != "request_id"},
        cwd,
    ]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=True, allow_nan=False).encode()
    ).hexdigest()[:32]


def decode_keys(value: object) -> dict[tuple[str, str], str]:
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "entries"}
        or type(value["version"]) is not int
        or value["version"] != 1
    ):
        raise ValueError("Invalid delegation identity snapshot")
    rows = value["entries"]
    if not isinstance(rows, list) or len(rows) > 4096:
        raise ValueError("Invalid delegation identity count")
    result = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"tool", "call_id", "request_id"}:
            raise ValueError("Invalid delegation identity record")
        tool, call_id, request_id = row["tool"], row["call_id"], row["request_id"]
        if tool not in ("vor_delegate", "huginn_delegate"):
            raise ValueError("Unknown delegation tool")
        if (
            not isinstance(call_id, str)
            or not 1 <= len(call_id) <= 256
            or any(ord(char) < 32 for char in call_id)
        ):
            raise ValueError("Invalid delegation call ID")
        if not isinstance(request_id, str) or not re.fullmatch(
            r"[a-f0-9]{32}", request_id
        ):
            raise ValueError("Invalid delegation request ID")
        identity = (tool, call_id)
        if identity in result:
            raise ValueError("Duplicate delegation call identity")
        result[identity] = request_id
    return result


def encode_keys(mapping: dict[tuple[str, str], str]) -> dict:
    snapshot = {
        "version": 1,
        "entries": [
            {"tool": tool, "call_id": call_id, "request_id": request_id}
            for (tool, call_id), request_id in mapping.items()
        ],
    }
    decode_keys(snapshot)
    return snapshot
