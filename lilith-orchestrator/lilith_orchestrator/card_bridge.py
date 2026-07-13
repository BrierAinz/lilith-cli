"""Card → SubAgent bridge — turns Vanaheim AgentCards into spawnable
:class:`~lilith_orchestrator.subagents.SubAgentDefinition` entries (Fase 1.2a).

The skills layer (:mod:`lilith_skills.agent_cards`) owns the *declarative*
metadata for every Vanaheim persona (Odin, Mimir, Heimdall, Jörmungandr…).
The orchestrator layer (:mod:`lilith_orchestrator.subagents`) owns the
*runtime* primitive that the engine actually spawns. This module is the
one-way bridge between them: it translates an :class:`AgentCard` into a
:class:`SubAgentDefinition` and publishes it into the live sub-agent
registry so a :class:`SubAgentRunner` can dispatch it mid-workflow.

Mapping contract (spec-closed — do not deviate):

  - ``agent_type``      = ``card.name.casefold()`` (preserves unicode)
  - ``system_prompt``   = ``f'{card.role}\\n\\n{card.description}'.strip()``
  - ``when_to_use``     = ``card.description or card.role``
  - ``allowed_tools``   = ``list(card.tools)`` *without* canonicalization;
                          a card with no tools maps to ``[]`` (never ``["*"]``)
  - ``model_preference``= ``card.model or None``
  - ``tags``            = ``['vanaheim', f'level:{card.level}', card.name]``

Hooks are intentionally **not** registered here — that is the job of
:func:`lilith_skills.sandbox_binder.bind_loader` (policy + audit wiring).
This module only concerns itself with the spawn-time definition.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from lilith_skills.agent_cards import AgentCard, AgentCardLoader
from lilith_orchestrator.subagents import (
    SubAgentDefinition,
    get_agent,
    register,
)

__all__ = [
    "agent_type_for_card",
    "card_to_subagent",
    "register_card_subagent",
    "register_loader_subagents",
    "register_vanaheim_subagents",
]


# ──────────────────────────────────────────────────────────────────────────────
# Loader protocol (structural — accepts AgentCardLoader and test stubs)
# ──────────────────────────────────────────────────────────────────────────────


class _LoaderLike(Protocol):
    """Structural type for anything that can enumerate agent cards."""

    def list_agents(self) -> list[AgentCard]: ...


# ──────────────────────────────────────────────────────────────────────────────
# Mapping
# ──────────────────────────────────────────────────────────────────────────────


def agent_type_for_card(card: AgentCard) -> str:
    """Return the registry key for a card: ``card.name.casefold()``.

    ``str.casefold`` is used (not ``lower``) because it is the correct
    Unicode-aware caseless comparison (e.g. German ``ß`` → ``ss``) and
    preserves diacritics — ``"Jörmungandr"`` → ``"jörmungandr"``.
    """
    return card.name.casefold()


def card_to_subagent(
    card: AgentCard,
    *,
    agent_type: str | None = None,
    allowed_tools: list[str] | None = None,
    disallowed_tools: list[str] | None = None,
    max_concurrency: int | None = None,
) -> SubAgentDefinition:
    """Translate an :class:`AgentCard` into a :class:`SubAgentDefinition`.

    Args:
        card: Source Vanaheim agent card.
        agent_type: Override the registry key. When ``None`` it is derived
            from ``card.name.casefold()``.
        allowed_tools: Override the tool allow-list. When ``None`` it is
            ``list(card.tools)`` verbatim (no canonicalization); a card
            with no tools yields ``[]`` — never the ``["*"]`` wildcard.
        disallowed_tools: Override the deny-list. Defaults to ``[]``.
        max_concurrency: Per-type concurrency cap. Defaults to ``None``
            (runner default).
    """
    resolved_type = agent_type or agent_type_for_card(card)

    if allowed_tools is None:
        resolved_tools = list(card.tools)
    else:
        resolved_tools = list(allowed_tools)

    if disallowed_tools is None:
        resolved_disallowed: list[str] = []
    else:
        resolved_disallowed = list(disallowed_tools)

    system_prompt = f"{card.role}\n\n{card.description}".strip()
    when_to_use = card.description or card.role
    model_preference = card.model or None
    tags = ["vanaheim", f"level:{card.level}", card.name]

    return SubAgentDefinition(
        agent_type=resolved_type,
        when_to_use=when_to_use,
        system_prompt=system_prompt,
        allowed_tools=resolved_tools,
        disallowed_tools=resolved_disallowed,
        model_preference=model_preference,
        max_concurrency=max_concurrency,
        tags=tags,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Registration
# ──────────────────────────────────────────────────────────────────────────────


def register_card_subagent(card: AgentCard, *, overwrite: bool = False) -> bool:
    """Register a single card's :class:`SubAgentDefinition`.

    Args:
        card: The card to publish.
        overwrite: When ``False`` (default), an already-registered
            ``agent_type`` is left untouched and the function returns
            ``False``. This protects the 8 default personas (and any
            prior Vanaheim registration) from being clobbered. When
            ``True`` the definition is replaced and ``True`` is returned.

    Returns:
        ``True`` if a definition was registered, ``False`` if it was
        skipped to preserve an existing entry.
    """
    defn = card_to_subagent(card)
    if not overwrite and get_agent(defn.agent_type) is not None:
        return False
    register(defn)
    return True


def register_loader_subagents(
    loader: _LoaderLike,
    *,
    overwrite: bool = False,
) -> list[SubAgentDefinition]:
    """Register every card yielded by ``loader.list_agents()``.

    Args:
        loader: Any object exposing ``list_agents() -> list[AgentCard]``
            (e.g. :class:`AgentCardLoader` or a test stub).
        overwrite: Forwarded to :func:`register_card_subagent` semantics.
            Existing agent types are skipped when ``False``.

    Returns:
        The definitions that were actually registered in this call (in
        card order). Skipped cards are not included.
    """
    registered: list[SubAgentDefinition] = []
    for card in loader.list_agents():
        defn = card_to_subagent(card)
        if not overwrite and get_agent(defn.agent_type) is not None:
            continue
        register(defn)
        registered.append(defn)
    return registered


def register_vanaheim_subagents(
    repo_root: Path | str,
    *,
    overwrite: bool = False,
) -> list[SubAgentDefinition]:
    """Load ``Vanaheim/Agents/agent_cards.yaml`` and register every card.

    Thin convenience wrapper around :meth:`AgentCardLoader.from_vanaheim`
    + :func:`register_loader_subagents`.

    Args:
        repo_root: Path to the Yggdrasil monorepo root.
        overwrite: Forwarded to :func:`register_loader_subagents`.

    Returns:
        The definitions that were actually registered.
    """
    loader = AgentCardLoader.from_vanaheim(repo_root)
    return register_loader_subagents(loader, overwrite=overwrite)
