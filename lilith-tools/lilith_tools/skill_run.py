"""Run a named reusable delegation skill."""

from __future__ import annotations

import json
from typing import Any

from lilith_skills.delegation_skills import DelegationSkillRegistry

from .base import BaseTool, ToolResult
from .delegate import DelegateSubagentTool
from .registry import ToolRegistry

_ALLOWED_OVERRIDES = {"preset", "agentic", "structured", "max_tokens"}


@ToolRegistry.register
class SkillRunTool(BaseTool):
    name = "skill_run"
    description = "Renderizar y ejecutar una plantilla de delegación guardada por nombre."
    timeout_seconds = 180
    parameters = {
        "name": {"type": "string", "required": True},
        "task": {"type": "string", "required": True},
        "project": {"type": "string", "required": False},
        "context": {"type": "string", "required": False},
        "overrides": {
            "type": "object",
            "required": False,
            "description": "Overrides: preset, agentic, structured, max_tokens",
        },
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        name = str(kwargs.get("name", "")).strip()
        task = str(kwargs.get("task", "")).strip()
        if not name or not task:
            return ToolResult(False, None, "name y task son requeridos")
        raw_overrides = kwargs.get("overrides") or {}
        if isinstance(raw_overrides, str):
            try:
                raw_overrides = json.loads(raw_overrides)
            except json.JSONDecodeError as exc:
                return ToolResult(False, None, f"overrides JSON inválido: {exc}")
        if not isinstance(raw_overrides, dict):
            return ToolResult(False, None, "overrides debe ser un objeto")
        unknown = set(raw_overrides) - _ALLOWED_OVERRIDES
        if unknown:
            return ToolResult(False, None, f"override(s) no permitidos: {sorted(unknown)}")
        try:
            skill = DelegationSkillRegistry().get(name)
        except (OSError, TypeError, ValueError) as exc:
            return ToolResult(False, None, str(exc))
        if skill is None:
            return ToolResult(False, None, f"skill '{name}' no existe")

        delegated = {
            "preset": raw_overrides.get("preset", skill.preset),
            "prompt": skill.render(
                task,
                str(kwargs.get("project", "")),
                str(kwargs.get("context", "")),
            ),
            "agentic": raw_overrides.get("agentic", skill.agentic),
            "structured": raw_overrides.get("structured", skill.structured),
        }
        max_tokens = raw_overrides.get("max_tokens", skill.max_tokens)
        if max_tokens is not None:
            delegated["max_tokens"] = int(max_tokens)
        result = DelegateSubagentTool().execute(**delegated)
        if isinstance(result.data, dict):
            result.data["skill"] = skill.name
        return result
