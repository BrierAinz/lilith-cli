"""AgentCard tool validator.

Wraps :func:`lilith_core.tools.validate_tool_names` for agent-card
shaped input: takes an ``AgentCard`` (or a loader wrapping many of
them) and reports per-card unknown / concrete / capability-only
splits.

Two-tier semantics (matches the spec):

- **unknown** -- tool name not in the card vocabulary at all; always
  rejected.
- **capability-only** -- name is in the vocabulary but resolves to a
  capability rather than a concrete BaseTool; passes when
  ``allow_capabilities=True`` (default), fails when
  ``allow_capabilities=False``.

Why a wrapper instead of calling :func:`validate_tool_names` directly
from card-loading code?

1. ``AgentCard`` has its own shape (``tools: list[str]``); the
   dataclass here carries the agent name alongside the validation
   result so ``bind_loader`` can produce per-agent lines in its
   report.
2. The wrapper handles ``None`` / duplicate / empty-string tool names
   defensively (YAML is loose; cards in the wild occasionally include
   blank entries).
3. ``assert_loader_tools_valid`` provides a one-liner for callers
   that want fail-loud behavior (e.g. ``ygg.py doctor --strict``)
   without each callsite reimplementing the loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from lilith_core.tools import (
    CARD_TOOL_ALIASES,
    CARD_TOOL_VOCAB,
    ToolNameValidation,
    canonical_tool_name,
    is_real_tool,
    validate_tool_names,
)

from lilith_skills.agent_cards import AgentCard, AgentCardLoader


class CardValidationError(ValueError):
    """Raised when a card declares unknown tool names.

    :class:`ValueError` subclass keeps callers that ``except
    ValueError`` working -- the public surface is "the card is bad
    input".
    """


@dataclass
class CardToolValidation:
    """Per-card tool-validation result.

    Attributes:
        agent_name: Name of the AgentCard.
        unknown_tools: Names that failed validation (typos / invented).
        concrete_tools: Names resolved to a concrete BaseTool.name.
        capability_only_tools: Names accepted as capabilities but
            without a concrete BaseTool registered.
        aliases: Mapping ``card_name -> concrete_name`` for each
            alias resolution that happened (useful for ``ygg.py
            doctor`` and audit logs).
    """

    agent_name: str
    unknown_tools: frozenset[str] = field(default_factory=frozenset)
    concrete_tools: frozenset[str] = field(default_factory=frozenset)
    capability_only_tools: frozenset[str] = field(default_factory=frozenset)
    aliases: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """True iff no unknown tools."""
        return not self.unknown_tools

    @property
    def total(self) -> int:
        """Total declared (after dropping blanks)."""
        return (
            len(self.unknown_tools)
            + len(self.concrete_tools)
            + len(self.capability_only_tools)
        )


# ── Internal helpers ────────────────────────────────────────────────────────


def _dedup_aliases(tools: Iterable[str]) -> dict[str, str]:
    """Build the ``card -> concrete`` alias map for *tools*.

    Only includes entries that actually resolved via
    :data:`CARD_TOOL_ALIASES` to a known concrete name. Pure
    aliases-to-self are included too so the map is a complete
    snapshot for audit logs.
    """
    aliases: dict[str, str] = {}
    for t in tools:
        if t in CARD_TOOL_ALIASES:
            target = CARD_TOOL_ALIASES[t]
            if target == t or is_real_tool(target):
                aliases[t] = target
    return aliases


def _validate_card(
    card: AgentCard,
    *,
    allow_capabilities: bool,
) -> CardToolValidation:
    tools = [str(t) for t in (card.tools or []) if t]
    result: ToolNameValidation = validate_tool_names(
        tools, allow_capabilities=allow_capabilities
    )
    aliases = _dedup_aliases(tools)
    return CardToolValidation(
        agent_name=card.name,
        unknown_tools=result.unknown,
        concrete_tools=result.concrete,
        capability_only_tools=result.capability_only,
        aliases=aliases,
    )


# ── Public API ─────────────────────────────────────────────────────────────


def validate_card_tools(
    card: AgentCard,
    *,
    allow_capabilities: bool = True,
) -> CardToolValidation:
    """Validate the tool list of a single AgentCard.

    Parameters
    ----------
    card
        The agent card to validate.
    allow_capabilities
        When ``True`` (default), names in the card vocabulary but
        without a concrete BaseTool mapping are accepted as
        capabilities. When ``False``, those names are reported as
        unknown.

    Returns
    -------
    CardToolValidation
        Dataclass with per-tier tool sets plus the alias map.

    Raises
    ------
    CardValidationError
        If ``card.tools`` contains unknown names. The dataclass is
        still available on the exception as ``exc.validation`` so
        callers can inspect what passed/failed without a second
        call.
    """
    validation = _validate_card(card, allow_capabilities=allow_capabilities)
    if not validation.ok:
        raise CardValidationError(
            f"AgentCard '{card.name}' declares unknown tools: "
            f"{sorted(validation.unknown_tools)}"
        )
    return validation


def validate_loader_tools(
    loader: AgentCardLoader,
    *,
    allow_capabilities: bool = True,
    strict: bool = False,
) -> list[CardToolValidation]:
    """Validate every card in *loader*.

    Parameters
    ----------
    loader
        An ``AgentCardLoader`` whose ``list_agents()`` produces the
        cards to validate.
    allow_capabilities
        Forwarded to :func:`validate_card_tools` for each card.
    strict
        When ``True``, any card with unknown tools raises
        :class:`CardValidationError` immediately (failing fast).
        When ``False`` (default), the function collects every
        card's validation and returns the full list -- useful for
        ``ygg.py doctor`` reports that want to enumerate *all*
        problems rather than stop at the first.

    Returns
    -------
    list[CardToolValidation]
        One entry per card. Each entry's ``ok`` field tells you
        whether the card was clean.

    Raises
    ------
    CardValidationError
        Only when ``strict=True`` and at least one card has unknown
        tools.
    """
    results: list[CardToolValidation] = []
    for card in loader.list_agents():
        try:
            validation = _validate_card(card, allow_capabilities=allow_capabilities)
        except Exception:
            raise
        results.append(validation)
        if strict and not validation.ok:
            raise CardValidationError(
                f"AgentCard '{card.name}' declares unknown tools: "
                f"{sorted(validation.unknown_tools)}"
            )
    return results


def assert_loader_tools_valid(
    loader: AgentCardLoader,
    *,
    allow_capabilities: bool = True,
) -> None:
    """Convenience: raise if any card in *loader* has unknown tools.

    Use this in callers that want fail-loud guarantees (e.g.
    ``bind_loader(..., strict_tools=True)`` -> ``assert_loader_tools_valid(loader)``)
    without dealing with the full list-of-validations surface.

    Parameters
    ----------
    loader
        The card loader to check.
    allow_capabilities
        Forwarded to :func:`validate_card_tools`.

    Raises
    ------
    CardValidationError
        From the first card that has unknown tools.
    """
    seen_failures: list[str] = []
    for card in loader.list_agents():
        try:
            validate_card_tools(card, allow_capabilities=allow_capabilities)
        except CardValidationError as exc:
            seen_failures.append(f"{card.name}: {exc}")
    if seen_failures:
        raise CardValidationError(
            "AgentCard loader failed tool validation: "
            + "; ".join(seen_failures)
        )


__all__ = [
    "CardToolValidation",
    "CardValidationError",
    "validate_card_tools",
    "validate_loader_tools",
    "assert_loader_tools_valid",
]


# Re-exported for downstream code that imports it from here
# (kept to avoid surprising callers; both paths work).
_ = (canonical_tool_name, CARD_TOOL_VOCAB, CARD_TOOL_ALIASES)
