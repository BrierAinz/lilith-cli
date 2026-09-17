from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from lilith_tools.base import BaseTool, ToolResult
from lilith_tools.registry import ToolRegistry

from .authority import AuthorityPolicy
from .compiler import MissionCompiler
from .compute import ComputeBroker
from .compute_health import ComputeHealthResolver
from .court import CourtRegistry
from .dispatch import CourtDispatcher
from .kernel import MissionKernel
from .model import MissionQuestion, MissionRoute


def _strings(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError("expected an array of strings")
    return [str(item).strip() for item in value if str(item).strip()]


def _questions(value: Any) -> list[MissionQuestion]:
    if value in (None, []):
        return []
    if not isinstance(value, list):
        raise TypeError("questions must be an array")
    rows: list[MissionQuestion] = []
    for item in value:
        if not isinstance(item, dict):
            raise TypeError("every question must be an object")
        options = tuple(_strings(item.get("options")))
        recommended = item.get("recommended_option")
        rows.append(MissionQuestion(
            str(item.get("question") or ""),
            options,
            str(item.get("reason") or ""),
            bool(item.get("blocking", True)),
            int(recommended) if recommended is not None else None,
        ))
    return rows


def _routes(value: Any) -> list[MissionRoute]:
    if value in (None, []):
        return []
    if not isinstance(value, list):
        raise TypeError("routes must be an array")
    rows: list[MissionRoute] = []
    for item in value:
        if not isinstance(item, dict):
            raise TypeError("every route must be an object")
        rows.append(MissionRoute(
            str(item.get("route_id") or ""),
            str(item.get("label") or ""),
            str(item.get("rationale") or ""),
            tuple(_strings(item.get("expected_strengths"))),
            tuple(_strings(item.get("risks"))),
            str(item.get("estimated_cost") or "unknown"),
        ))
    return rows


def _ok(callable_, *args: Any, **kwargs: Any) -> ToolResult:
    try:
        return ToolResult(True, callable_(*args, **kwargs), "")
    except (OSError, LookupError, TypeError, ValueError) as exc:
        return ToolResult(False, None, f"{type(exc).__name__}: {exc}")


@ToolRegistry.register
class MissionCourtTool(BaseTool):
    name = "mission_court"
    description = "Lista la corte canónica de subagentes de Lilith o propone un equipo por capacidades."
    parameters: ClassVar[dict[str, Any]] = {
        "capabilities": {"type": "array", "required": False},
        "count": {"type": "integer", "required": False},
        "require_executor": {"type": "boolean", "required": False},
        "require_reviewer": {"type": "boolean", "required": False},
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        try:
            caps = set(_strings(kwargs.get("capabilities")))
            if not caps:
                data = [item.as_dict() for item in CourtRegistry.list_agents(include_queen=True)]
            else:
                team = CourtRegistry.choose(
                    caps,
                    count=max(1, min(7, int(kwargs.get("count", 4)))),
                    require_executor=bool(kwargs.get("require_executor", False)),
                    require_reviewer=bool(kwargs.get("require_reviewer", True)),
                )
                data = [item.as_dict() for item in team]
            return ToolResult(True, {"agents": data, "legacy_aliases": CourtRegistry.migration_map()}, "")
        except (TypeError, ValueError) as exc:
            return ToolResult(False, None, str(exc))


@ToolRegistry.register
class MissionComputeTool(BaseTool):
    name = "mission_compute"
    description = (
        "Consulta recursos, salud local/circuitos y calcula una ruta de cómputo "
        "sin ligar un agente a un proveedor. El probe de Fabric no ejecuta inferencia."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "capabilities": {"type": "array", "required": False},
        "healthy": {"type": "array", "required": False},
        "avoid": {"type": "array", "required": False},
        "preferred_transport": {"type": "string", "required": False},
        "court_agent": {"type": "string", "required": False},
        "probe_fabric": {"type": "boolean", "required": False},
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        broker = ComputeBroker()
        resolver = ComputeHealthResolver(broker=broker)
        court_agent = str(kwargs.get("court_agent") or "").strip() or None
        probe = bool(kwargs.get("probe_fabric", False))
        try:
            health = resolver.public_snapshot(
                court_agent=court_agent,
                probe_fabric=probe,
            )
            accounts = resolver.observed_fabric_accounts()
            caps = set(_strings(kwargs.get("capabilities")))
            if not caps:
                return ToolResult(
                    True,
                    {
                        "resources": broker.public_inventory(),
                        "health": health,
                        "fabric_accounts": accounts,
                    },
                    "",
                )
            healthy_raw = kwargs.get("healthy")
            healthy = (
                set(_strings(healthy_raw))
                if healthy_raw is not None
                else {row["resource_id"] for row in health if row["available"]}
            )
            route = broker.route(
                caps,
                healthy=healthy,
                avoid=set(_strings(kwargs.get("avoid"))),
                preferred_transport=(
                    str(kwargs.get("preferred_transport") or "").strip() or None
                ),
            )
            return ToolResult(
                True,
                {
                    "resource": route.resource.as_dict(),
                    "reason": route.reason,
                    "fallbacks": list(route.fallbacks),
                    "health": health,
                    "fabric_accounts": accounts,
                },
                "",
            )
        except (LookupError, TypeError, ValueError) as exc:
            return ToolResult(False, None, str(exc))


@ToolRegistry.register
class MissionAuthorityTool(BaseTool):
    name = "mission_authority"
    description = "Evalúa autoridad local de una acción: D:/C:, elevación, rollback y gate del Overlord."
    parameters: ClassVar[dict[str, Any]] = {
        "action": {"type": "string", "required": True},
        "path": {"type": "string", "required": False},
        "recovery_available": {"type": "boolean", "required": False},
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        action = str(kwargs.get("action") or "").strip()
        if not action:
            return ToolResult(False, None, "action es obligatoria")
        decision = AuthorityPolicy().evaluate(
            action,
            path=str(kwargs.get("path") or "").strip() or None,
            recovery_available=bool(kwargs.get("recovery_available", False)),
        )
        return ToolResult(True, decision.as_dict(), "")


@ToolRegistry.register
class MissionPrepareTool(BaseTool):
    name = "mission_prepare"
    description = (
        "Compila y registra una misión persistente DESPUÉS de inspeccionar el objetivo. "
        "Si quedan preguntas materiales, la misión queda bloqueada sin iniciar longrun."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "objective": {"type": "string", "required": True},
        "project_root": {"type": "string", "required": True},
        "success_criteria": {"type": "array", "required": True},
        "constraints": {"type": "array", "required": False},
        "assumptions": {"type": "array", "required": False},
        "questions": {"type": "array", "required": False},
        "routes": {"type": "array", "required": False},
        "recommended_route": {"type": "string", "required": False},
        "required_capabilities": {"type": "array", "required": False},
        "verify": {"type": "string", "required": False},
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        try:
            compiled = MissionCompiler().compile(
                objective=str(kwargs.get("objective") or ""),
                project_root=str(kwargs.get("project_root") or ""),
                success_criteria=_strings(kwargs.get("success_criteria")),
                constraints=_strings(kwargs.get("constraints")),
                assumptions=_strings(kwargs.get("assumptions")),
                questions=_questions(kwargs.get("questions")),
                routes=_routes(kwargs.get("routes")),
                recommended_route=(str(kwargs.get("recommended_route") or "").strip() or None),
            )
            registration = MissionKernel().register(
                compiled,
                required_capabilities=set(_strings(kwargs.get("required_capabilities"))),
                verify=str(kwargs.get("verify") or ""),
                lease_owner=(str(kwargs.get("_lease_owner") or "").strip() or None),
                lease_seconds=float(kwargs.get("_lease_seconds", 300.0) or 300.0),
            )
            return ToolResult(True, {
                "mission": compiled.mission.as_dict(),
                "registration": registration.as_dict(),
                "needs_operator": compiled.needs_operator,
                "recommended_route": compiled.recommended_route,
            }, "")
        except (LookupError, OSError, TypeError, ValueError) as exc:
            return ToolResult(False, None, f"{type(exc).__name__}: {exc}")


@ToolRegistry.register
class MissionStatusTool(BaseTool):
    name = "mission_status"
    description = "Consulta misiones persistentes de Lilith sin ejecutar ni modificar ninguna."
    parameters: ClassVar[dict[str, Any]] = {
        "mission_id": {"type": "string", "required": False},
        "limit": {"type": "integer", "required": False},
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        kernel = MissionKernel()
        mission_id = str(kwargs.get("mission_id") or "").strip()
        if mission_id:
            return ToolResult(True, {"mission": kernel.status(mission_id)}, "")
        try:
            return ToolResult(
                True,
                {"missions": kernel.list_missions(limit=int(kwargs.get("limit", 20)))},
                "",
            )
        except (TypeError, ValueError) as exc:
            return ToolResult(False, None, str(exc))


@ToolRegistry.register
class MissionCompleteTool(BaseTool):
    name = "mission_complete"
    description = (
        "Cierra una misión sólo después de verificar criterios; guarda resultado, "
        "evidencia resumida y post-mortem reutilizable."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "task_id": {"type": "string", "required": True},
        "success": {"type": "boolean", "required": True},
        "summary": {"type": "string", "required": True},
        "verification": {"type": "object", "required": False},
        "usage": {"type": "object", "required": False},
        "lessons": {"type": "array", "required": False},
        "next_missions": {"type": "array", "required": False},
        "campaign_mode": {"type": "boolean", "required": False},
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        task_id = str(kwargs.get("task_id") or "").strip()
        summary = str(kwargs.get("summary") or "").strip()
        if not task_id or not summary:
            return ToolResult(False, None, "task_id y summary son obligatorios")
        return _ok(
            MissionKernel().complete,
            task_id,
            success=bool(kwargs.get("success")),
            summary=summary,
            verification=dict(kwargs.get("verification") or {}),
            usage=dict(kwargs.get("usage") or {}),
            lessons=_strings(kwargs.get("lessons")),
            next_missions=list(kwargs.get("next_missions") or []),
            campaign_mode=bool(kwargs.get("campaign_mode", True)),
        )


@ToolRegistry.register
class MissionResumeExpiredTool(BaseTool):
    name = "mission_resume_expired"
    description = (
        "Recupera leases de tareas expiradas tras caída/reinicio. No repite efectos "
        "desconocidos ni ejecuta la tarea por sí mismo."
    )
    parameters: ClassVar[dict[str, Any]] = {}

    def execute(self, **kwargs: Any) -> ToolResult:
        return _ok(MissionKernel().resume_expired)


@ToolRegistry.register
class MissionDelegateTool(BaseTool):
    name = "mission_delegate"
    allow_automatic_retry = False
    description = (
        "Delega una subtarea a un miembro de la corte. Lilith elige rol; el broker "
        "elige cómputo/adapter y conserva idempotencia, evidencia y post-mortem. "
        "No pases nombres de providers: usa agent + tarea."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "agent": {"type": "string", "required": True},
        "prompt": {"type": "string", "required": True},
        "mission_id": {"type": "string", "required": False},
        "workdir": {"type": "string", "required": False},
        "required_compute": {"type": "array", "required": False},
        "healthy_compute": {"type": "array", "required": False},
        "agentic": {"type": "boolean", "required": False},
        "structured": {"type": "boolean", "required": False},
        "max_turns": {"type": "integer", "required": False},
        "max_tokens": {"type": "integer", "required": False},
        "timeout": {"type": "integer", "required": False},
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        healthy_raw = kwargs.get("healthy_compute")
        healthy = set(_strings(healthy_raw)) if healthy_raw is not None else None
        agentic_raw = kwargs.get("agentic")
        agentic = bool(agentic_raw) if agentic_raw is not None else None
        max_tokens_raw = kwargs.get("max_tokens")
        return CourtDispatcher().dispatch(
            str(kwargs.get("agent") or ""),
            str(kwargs.get("prompt") or ""),
            mission_id=str(kwargs.get("mission_id") or ""),
            workdir=str(kwargs.get("workdir") or ""),
            required_compute=set(_strings(kwargs.get("required_compute"))) or None,
            healthy_compute=healthy,
            agentic=agentic,
            structured=bool(kwargs.get("structured", False)),
            max_turns=int(kwargs.get("max_turns", 10)),
            max_tokens=int(max_tokens_raw) if max_tokens_raw is not None else None,
            timeout=int(kwargs.get("timeout", 1800)),
        )


@ToolRegistry.register
class MissionConclaveTool(BaseTool):
    name = "mission_conclave"
    allow_automatic_retry = False
    description = (
        "Consulta en paralelo a 2-4 miembros de la corte y devuelve sus respuestas "
        "sin sintetizar. Usa roles (Demiurge, Aura, Shalltear...), no providers."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "question": {"type": "string", "required": True},
        "agents": {"type": "array", "required": False},
        "mission_id": {"type": "string", "required": False},
        "structured": {"type": "boolean", "required": False},
        "max_tokens": {"type": "integer", "required": False},
        "timeout": {"type": "integer", "required": False},
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        from .collaboration import MissionCollaboration

        max_tokens = kwargs.get("max_tokens")
        return MissionCollaboration().conclave(
            str(kwargs.get("question") or ""),
            agents=_strings(kwargs.get("agents")) or None,
            mission_id=str(kwargs.get("mission_id") or ""),
            structured=bool(kwargs.get("structured", False)),
            max_tokens=int(max_tokens) if max_tokens is not None else None,
            timeout=int(kwargs.get("timeout", 180)),
        )


@ToolRegistry.register
class MissionSkillRunTool(BaseTool):
    name = "mission_skill_run"
    allow_automatic_retry = False
    description = (
        "Ejecuta una skill reutilizable a través de la corte. Skills legacy se "
        "mapean automáticamente a roles nuevos; el provider queda oculto tras el broker."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "name": {"type": "string", "required": True},
        "task": {"type": "string", "required": True},
        "project": {"type": "string", "required": False},
        "context": {"type": "string", "required": False},
        "mission_id": {"type": "string", "required": False},
        "overrides": {"type": "object", "required": False},
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        from .collaboration import MissionCollaboration

        return MissionCollaboration().run_skill(
            str(kwargs.get("name") or ""),
            str(kwargs.get("task") or ""),
            project=str(kwargs.get("project") or ""),
            context=str(kwargs.get("context") or ""),
            mission_id=str(kwargs.get("mission_id") or ""),
            overrides=dict(kwargs.get("overrides") or {}),
        )


@ToolRegistry.register
class MissionLearningTool(BaseTool):
    name = "mission_learning"
    description = (
        "Consulta aprendizaje operativo de la corte: rutas degradadas por fallos "
        "repetidos y patrones candidatos a skill. No modifica estado."
    )
    parameters: ClassVar[dict[str, Any]] = {}

    def execute(self, **kwargs: Any) -> ToolResult:
        from .learning import LearningEngine

        try:
            return ToolResult(True, LearningEngine().public_summary(), "")
        except (OSError, TypeError, ValueError) as exc:
            return ToolResult(False, None, str(exc))


@ToolRegistry.register
class MissionDesktopObserveTool(BaseTool):
    name = "mission_desktop_observe"
    description = (
        "Observa el escritorio Windows sin interacción: ventanas, cursor o screenshot "
        "guardado localmente con hash. No hace clicks ni escribe."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "action": {
            "type": "string", "required": True,
            "enum": ["windows", "cursor", "screenshot"],
        },
        "limit": {"type": "integer", "required": False},
        "directory": {"type": "string", "required": False},
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        from .desktop import DesktopObserver

        try:
            action = str(kwargs.get("action") or "")
            if action == "windows":
                return ToolResult(True, {"windows": DesktopObserver.windows(limit=int(kwargs.get("limit", 80)))}, "")
            if action == "cursor":
                return ToolResult(True, DesktopObserver.cursor(), "")
            if action == "screenshot":
                return ToolResult(True, DesktopObserver.screenshot(kwargs.get("directory")), "")
            return ToolResult(False, None, "desktop observe action inválida")
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return ToolResult(False, None, str(exc))


@ToolRegistry.register
class MissionDesktopActTool(BaseTool):
    name = "mission_desktop_act"
    description = (
        "Computer Use de Sebas: focus/click/type/hotkey. effect es obligatorio: "
        "local, external_submit, publish, purchase, credential o irreversible. "
        "AuthorityPolicy bloquea consecuencias externas/irreversibles."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "action": {
            "type": "string", "required": True,
            "enum": ["focus", "click", "type", "hotkey"],
        },
        "effect": {
            "type": "string", "required": True,
            "enum": ["local", "external_submit", "publish", "purchase", "credential", "irreversible"],
        },
        "expected_window": {"type": "string", "required": False},
        "title_contains": {"type": "string", "required": False},
        "x": {"type": "integer", "required": False},
        "y": {"type": "integer", "required": False},
        "button": {"type": "string", "required": False},
        "clicks": {"type": "integer", "required": False},
        "text": {"type": "string", "required": False},
        "keys": {"type": "array", "required": False},
    }
    def execute(self, **kwargs: Any) -> ToolResult:
        from .desktop import DesktopActor

        try:
            action = str(kwargs.get("action") or "")
            effect = str(kwargs.get("effect") or "")
            expected = kwargs.get("expected_window")
            if action == "focus":
                data = DesktopActor.focus(str(kwargs.get("title_contains") or ""), effect=effect)
            elif action == "click":
                data = DesktopActor.click(
                    int(kwargs.get("x", 0)), int(kwargs.get("y", 0)),
                    button=str(kwargs.get("button") or "left"),
                    clicks=int(kwargs.get("clicks", 1)),
                    effect=effect, expected_window=str(expected) if expected else None,
                )
            elif action == "type":
                data = DesktopActor.type_text(
                    str(kwargs.get("text") or ""), effect=effect,
                    expected_window=str(expected) if expected else None,
                )
            elif action == "hotkey":
                data = DesktopActor.hotkey(
                    _strings(kwargs.get("keys")), effect=effect,
                    expected_window=str(expected) if expected else None,
                )
            else:
                return ToolResult(False, None, "desktop action inválida")
            return ToolResult(True, data, "")
        except (LookupError, OSError, PermissionError, RuntimeError, TypeError, ValueError) as exc:
            return ToolResult(False, None, str(exc))


@ToolRegistry.register
class MissionAdminExecTool(BaseTool):
    name = "mission_admin_exec"
    description = (
        "Ejecuta PowerShell local con los permisos actuales tras AuthorityPolicy. "
        "El caller debe declarar action; external/irreversible se bloquean. "
        "Lilith no eleva privilegios del sistema operativo por sí misma."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "command": {"type": "string", "required": True},
        "action": {
            "type": "string", "required": True,
            "enum": [
                "local_command", "service_change", "driver_change", "firewall_change",
                "registry_write", "registry_delete", "system_install", "security_change",
                "boot_change", "reboot", "delete", "publish", "send_message",
                "purchase", "payment", "credential_change", "wipe_disk", "format",
            ],
        },
        "cwd": {"type": "string", "required": False},
        "timeout": {"type": "integer", "required": False},
        "recovery_available": {"type": "boolean", "required": False},
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        from .desktop import AdminCommandBroker

        try:
            data = AdminCommandBroker.run(
                str(kwargs.get("command") or ""),
                action=str(kwargs.get("action") or ""),
                cwd=str(kwargs.get("cwd") or Path.cwd()),
                timeout=int(kwargs.get("timeout", 120)),
                recovery_available=bool(kwargs.get("recovery_available", False)),
            )
            success = bool(data.get("executed")) and int(data.get("exit_code", 0)) == 0
            return ToolResult(success, data, "" if success else str(data.get("error") or "authority blocked"))
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return ToolResult(False, None, str(exc))
