"""Canonical task-response schema for agentic sub-agents.

When a sub-agent runs in ``structured=True`` mode, the orchestrator asks it
to finish with a JSON object matching :data:`TASK_SCHEMA`. This module owns
the schema definition and a local validator so callers can lint the response
even when the provider doesn't support ``response_format=json_schema``.

The schema is intentionally permissive on ``deliverables[i].type`` (free
string) so different presets can declare their own deliverable kinds
("file", "diff", "summary", "report", ...) without breaking the validator.
"""

from __future__ import annotations

from typing import Any

# Canonical schema used to drive ``response_format=json_schema`` on
# OpenAI-compatible providers and to validate locally on every provider.
TASK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "status"],
    "properties": {
        "summary": {"type": "string", "minLength": 1},
        "deliverables": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "type", "content"],
                "properties": {
                    "name": {"type": "string", "minLength": 1},
                    "type": {"type": "string", "minLength": 1},
                    "content": {"type": "string"},
                },
            },
        },
        "status": {
            "type": "string",
            "enum": ["completed", "failed", "blocked"],
        },
        "blockers": {"type": "array", "items": {"type": "string"}},
        "next_steps": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
}

# Top-level required fields, separate from per-field validators so the
# local validator can emit one error per missing key without knowing the
# schema's internal structure.
_REQUIRED_TOP_LEVEL = ("summary", "status")
_ALLOWED_STATUS = {"completed", "failed", "blocked"}


def validate_task_response(obj: Any) -> list[str]:
    """Return a list of validation errors (empty if ``obj`` is valid).

    Only the shape is checked; semantic correctness (e.g. that
    ``deliverables[i].content`` is parseable) is the caller's job.
    """
    errors: list[str] = []
    if not isinstance(obj, dict):
        return [f"expected object, got {type(obj).__name__}"]

    for key in _REQUIRED_TOP_LEVEL:
        if key not in obj:
            errors.append(f"missing required field: {key!r}")

    summary = obj.get("summary")
    if summary is not None and not isinstance(summary, str):
        errors.append("'summary' must be a string")
    if isinstance(summary, str) and not summary.strip():
        errors.append("'summary' must be non-empty")

    status = obj.get("status")
    if status is not None and status not in _ALLOWED_STATUS:
        errors.append(
            f"'status' must be one of {sorted(_ALLOWED_STATUS)}, got {status!r}"
        )

    deliverables = obj.get("deliverables")
    if deliverables is not None:
        if not isinstance(deliverables, list):
            errors.append("'deliverables' must be a list")
        else:
            for i, item in enumerate(deliverables):
                if not isinstance(item, dict):
                    errors.append(f"deliverables[{i}] must be an object")
                    continue
                for field in ("name", "type", "content"):
                    if field not in item:
                        errors.append(f"deliverables[{i}] missing {field!r}")
                name = item.get("name")
                if name is not None and not isinstance(name, str):
                    errors.append(f"deliverables[{i}].name must be a string")
                if isinstance(name, str) and not name.strip():
                    errors.append(f"deliverables[{i}].name must be non-empty")
                kind = item.get("type")
                if kind is not None and not isinstance(kind, str):
                    errors.append(f"deliverables[{i}].type must be a string")
                if isinstance(kind, str) and not kind.strip():
                    errors.append(f"deliverables[{i}].type must be non-empty")
                content = item.get("content")
                if content is not None and not isinstance(content, str):
                    errors.append(f"deliverables[{i}].content must be a string")

    for list_field in ("blockers", "next_steps"):
        value = obj.get(list_field)
        if value is None:
            continue
        if not isinstance(value, list):
            errors.append(f"'{list_field}' must be a list of strings")
            continue
        for i, item in enumerate(value):
            if not isinstance(item, str):
                errors.append(f"{list_field}[{i}] must be a string")

    confidence = obj.get("confidence")
    if confidence is not None:
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
            errors.append("'confidence' must be a number")
        elif not 0.0 <= float(confidence) <= 1.0:
            errors.append("'confidence' must be between 0.0 and 1.0")

    return errors