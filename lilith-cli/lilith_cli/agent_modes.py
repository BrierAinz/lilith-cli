"""Agent mode definitions and helpers for Lilith CLI.

Defines the available agent operating modes (default, plan-first,
review-only, auto-edit) and the mode registry that maps a mode name to its
configuration effects.  Used by the /agent slash command and by the agent
orchestrator when building prompts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AgentMode:
    """Description of a single agent operating mode.

    Attributes
    ----------
    name:
        Machine identifier (used in /agent <name> and persisted config).
    label:
        Human-readable label shown in listings.
    description:
        One-line Spanish description shown in /agent list and help.
    plan_first:
        When True, the agent must create a plan before executing any
        destructive action.
    confirm_write:
        When False, destructive file_write/file_edit calls skip the diff
        preview and apply immediately.
    allow_writes:
        When False, the agent is not allowed to write or edit files.
    system_prompt_extra:
        Extra instructions injected into the system prompt when this mode
        is active.
    """

    name: str
    label: str
    description: str
    plan_first: bool = False
    confirm_write: bool = True
    allow_writes: bool = True
    system_prompt_extra: str = ""


_AGENT_MODES: dict[str, AgentMode] = {
    "default": AgentMode(
        name="default",
        label="Default",
        description="Capacidades completas: planificar, leer, escribir y ejecutar.",
        plan_first=False,
        confirm_write=True,
        allow_writes=True,
    ),
    "plan-first": AgentMode(
        name="plan-first",
        label="Plan-first",
        description="Siempre crea un plan numerado antes de ejecutar cambios.",
        plan_first=True,
        confirm_write=True,
        allow_writes=True,
        system_prompt_extra=(
            "\n\nMODO PLAN-FIRST: Antes de realizar cualquier cambio destructivo "
            "o ejecutar una tarea compleja, debés crear un plan numerado con /plan "
            "y esperar confirmación del usuario para continuar paso a paso."
        ),
    ),
    "review-only": AgentMode(
        name="review-only",
        label="Review-only",
        description="Solo lectura y revisión: no se permiten escrituras.",
        plan_first=False,
        confirm_write=True,
        allow_writes=False,
        system_prompt_extra=(
            "\n\nMODO REVIEW-ONLY: No estás autorizado a escribir, editar ni eliminar "
            "archivos. Podés leer código, analizar, explicar, revisar y sugerir "
            "cambios, pero no aplicarlos."
        ),
    ),
    "auto-edit": AgentMode(
        name="auto-edit",
        label="Auto-edit",
        description="Aplica ediciones directamente sin confirmación de diff.",
        plan_first=False,
        confirm_write=False,
        allow_writes=True,
        system_prompt_extra=(
            "\n\nMODO AUTO-EDIT: Podés aplicar cambios destructivos de forma directa "
            "sin mostrar diff previo. Mantené precisión y verificá los resultados."
        ),
    ),
}


def list_agent_modes() -> list[AgentMode]:
    """Return all registered agent modes in a stable order."""
    return list(_AGENT_MODES.values())


def get_agent_mode(name: str) -> AgentMode | None:
    """Look up an agent mode by its machine name."""
    return _AGENT_MODES.get(name)


def is_valid_agent_mode(name: str) -> bool:
    """Return True if *name* is a known agent mode."""
    return name in _AGENT_MODES


def apply_agent_mode(session: Any, mode: AgentMode) -> None:
    """Apply *mode* settings to an AgentSession.

    This mutates the session configuration to reflect the mode's policy:
    - ``confirm_write`` is set according to the mode.
    - ``allow_writes`` is stored on the session as ``_agent_allow_writes``.
    - ``plan_first`` is stored on the session as ``_agent_plan_first``.
    - The mode name is stored as ``agent_mode`` on the session.
    """
    session.config.confirm_write = mode.confirm_write
    session._agent_allow_writes = mode.allow_writes  # noqa: SLF001
    session._agent_plan_first = mode.plan_first  # noqa: SLF001
    session.agent_mode = mode.name


def get_current_agent_mode(session: Any) -> str:
    """Return the active mode name for *session*, defaulting to 'default'."""
    return getattr(session, "agent_mode", "default")
