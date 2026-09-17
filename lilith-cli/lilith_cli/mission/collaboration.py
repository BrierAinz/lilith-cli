from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from lilith_skills.delegation_skills import DelegationSkillRegistry
from lilith_tools.base import ToolResult

from .court import CourtRegistry
from .dispatch import CourtDispatcher

DEFAULT_COUNCIL = ("demiurge", "aura", "shalltear")


class MissionCollaboration:
    """Court-native conclave and reusable skill execution."""

    def __init__(self, dispatcher: CourtDispatcher | None = None) -> None:
        self.dispatcher = dispatcher or CourtDispatcher()

    def conclave(
        self,
        question: str,
        *,
        agents: list[str] | tuple[str, ...] | None = None,
        mission_id: str = "",
        structured: bool = False,
        max_tokens: int | None = None,
        timeout: int = 180,
    ) -> ToolResult:
        question = str(question).strip()
        if not question:
            return ToolResult(False, None, "question es obligatoria")
        requested = list(agents or DEFAULT_COUNCIL)
        if not 2 <= len(requested) <= 4:
            return ToolResult(False, None, "el consejo requiere 2..4 agentes")
        resolved = []
        for name in requested:
            agent = CourtRegistry.get(name)
            if agent is None or agent.agent_id == "lilith":
                return ToolResult(False, None, f"agente de corte desconocido: {name}")
            if agent.agent_id not in resolved:
                resolved.append(agent.agent_id)
        if len(resolved) < 2:
            return ToolResult(False, None, "el consejo requiere agentes distintos")
        rows: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=len(resolved)) as pool:
            future_map = {
                pool.submit(
                    self.dispatcher.dispatch,
                    agent_id,
                    question,
                    mission_id=mission_id,
                    agentic=False,
                    structured=structured,
                    max_tokens=max_tokens,
                    timeout=timeout,
                ): agent_id
                for agent_id in resolved
            }
            for future in as_completed(future_map):
                agent_id = future_map[future]
                try:
                    result = future.result()
                except Exception as exc:  # noqa: BLE001 - worker boundary
                    rows.append({
                        "agent": agent_id,
                        "success": False,
                        "error": f"{type(exc).__name__}: {exc}",
                    })
                    continue
                rows.append({
                    "agent": agent_id,
                    "success": bool(result.success),
                    "data": result.data,
                    "error": result.error,
                })
        rows.sort(key=lambda row: resolved.index(str(row["agent"])))
        ok_count = sum(1 for row in rows if row["success"])
        return ToolResult(
            ok_count > 0,
            {
                "question": question,
                "agents": resolved,
                "responses": rows,
                "ok_count": ok_count,
                "failed_count": len(rows) - ok_count,
                "synthesis_required": True,
            },
            "" if ok_count else "todos los miembros del consejo fallaron",
        )

    def run_skill(
        self,
        name: str,
        task: str,
        *,
        project: str = "",
        context: str = "",
        mission_id: str = "",
        overrides: dict[str, Any] | None = None,
    ) -> ToolResult:
        name = str(name).strip()
        task = str(task).strip()
        if not name or not task:
            return ToolResult(False, None, "name y task son obligatorios")
        registry = DelegationSkillRegistry()
        try:
            skill = registry.get(name)
        except (OSError, TypeError, ValueError) as exc:
            return ToolResult(False, None, str(exc))
        if skill is None:
            return ToolResult(False, None, f"skill desconocida: {name}")
        active_version = next(
            (row for row in registry.versions(skill.name) if row.active),
            None,
        )
        provenance = {"skill_name": skill.name}
        if active_version is not None:
            provenance["skill_version_id"] = active_version.version_id
        options = dict(overrides or {})
        allowed = {"agent", "agentic", "structured", "max_tokens", "timeout"}
        unknown = set(options) - allowed
        if unknown:
            return ToolResult(False, None, f"override(s) no permitidos: {sorted(unknown)}")
        agent_name = str(options.get("agent") or skill.preset).strip()
        agent = CourtRegistry.get(agent_name)
        if agent is None or agent.agent_id == "lilith":
            return ToolResult(False, None, f"skill apunta a rol desconocido: {agent_name}")
        result = self.dispatcher.dispatch(
            agent.agent_id,
            skill.render(task, project, context),
            mission_id=mission_id,
            workdir=project,
            agentic=bool(options.get("agentic", skill.agentic)),
            structured=bool(options.get("structured", skill.structured)),
            max_tokens=(
                int(options["max_tokens"])
                if options.get("max_tokens") is not None
                else skill.max_tokens
            ),
            timeout=int(options.get("timeout", 1800)),
            provenance=provenance,
        )
        if isinstance(result.data, dict):
            result.data["skill"] = skill.name
            result.data["skill_agent"] = agent.agent_id
            if active_version is not None:
                result.data["skill_version_id"] = active_version.version_id
        return result
