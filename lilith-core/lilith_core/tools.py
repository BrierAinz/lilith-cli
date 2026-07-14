"""Tool name catalog and validation for Lilith.

Single source of truth for which tool *names* are valid in agent cards
and which map to concrete implementations in :mod:`lilith_tools`.

Two vocabulary layers live here:

1. **Concrete tool catalog** -- ``TOOL_NAMES`` is the frozen set of
   ``BaseTool.name`` class attributes currently shipped in
   :mod:`lilith_tools`. ``is_real_tool()`` checks against this set.
   These are the names a runtime router/agent will look up.

2. **Card-level tool vocabulary** -- agent cards (e.g. Vanaheim's
   ``agent_cards.yaml``) declare their capabilities using a slightly
   different vocabulary than the runtime tools: a card can promise
   ``read_file`` even though the BaseTool is named ``file_read``.
   ``CARD_TOOL_VOCAB`` is the set of names that are valid in a card,
   ``CARD_TOOL_ALIASES`` maps card names onto concrete tool names,
   and ``CAPABILITY_ONLY`` is derived as ``card-name vocab - alias
   targets - self-names that are also concrete``. Capability-only
   names are accepted by the validator when ``allow_capabilities=True``
   (default), rejected otherwise.

Two-tier validation:

- **Unknown** names (not in CARD_TOOL_VOCAB) -- always fail (with
  ``allow_capabilities=True`` or not). These are typos / invented tools.
- **Capability-only** names (in vocab but no alias mapping to a real
  BaseTool) -- pass when ``allow_capabilities=True``; fail when
  ``allow_capabilities=False`` if *strict* is desired via
  :func:`validate_tool_names`.

Designed to NOT import :mod:`lilith_tools` at module load time -- the
catalog is hardcoded from a verified read of the package, so the
contract test in ``tests/test_tool_catalog.py`` can re-verify by
importing lilith_tools independently and catching any drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


# ── Concrete tool catalog ──────────────────────────────────────────────────


#: Frozen set of concrete ``BaseTool.name`` values shipped by
#: :mod:`lilith_tools` at the time this catalog was authored. The
#: test in ``tests/test_tool_catalog.py`` cross-checks this set against
#: the real BaseTool subclasses discovered at import time -- if a new
#: BaseTool is added in lilith-tools without updating this catalog,
#: ``test_tool_catalog_contract`` fails.
TOOL_NAMES: frozenset[str] = frozenset({
    "batch_edit",
    "browser",
    "chunk_ingest",
    "chunk_recall",
    "chunk_store_stats",
    "coding",
    "directory_list",
    "file_edit",
    "file_read",
    "file_write",
    "grep_files",
    "local_disk_usage",
    "local_docker_ps",
    "local_env",
    "local_git_log",
    "local_git_status",
    "local_ports",
    "local_processes",
    "local_python_info",
    "package_guard",
    "screenshot_capture",
    "security_scan",
    "system_info",
    "system_time",
    "vision_analyze",
    "web_search",
})


# ── Card-level vocabulary ──────────────────────────────────────────────────


#: Tool names valid in an ``AgentCard.tools:`` list. Names not in this
#: set (and not equal to a concrete BaseTool name either) are rejected
#: at card-validation time as ``unknown``.
CARD_TOOL_VOCAB: frozenset[str] = frozenset({
    "terminal",
    "read_file",
    "write_file",
    "patch",
    "search_files",
    "session_search",
    "web_search",
})


#: Mapping from card-level tool names to concrete ``BaseTool.name``
#: values. Only two mappings today: ``read_file`` (card) -> ``file_read``
#: (BaseTool), and ``web_search`` (self-mapping).
#:
#: Note: ``terminal`` is intentionally NOT aliased to ``coding`` here.
#: A card declaring ``terminal`` is expressing a capability (the agent
#: can run shell commands), not pointing to a specific BaseTool; the
#: runtime router may pick from a pool later. This keeps the alias
#: surface narrow and explicit.
CARD_TOOL_ALIASES: dict[str, str] = {
    "read_file": "file_read",
    "web_search": "web_search",
}


def _derive_capability_only() -> frozenset[str]:
    """Compute the capability-only subset of card-vocabulary names.

    A name is capability-only iff it's in ``CARD_TOOL_VOCAB``, has no
    entry in ``CARD_TOOL_ALIASES`` (or aliases to itself) -- meaning
    the card promises a capability but there's no concrete BaseTool
    registered for it under the same surface name.

    Names whose alias *targets* a real BaseTool are treated as
    concrete (resolved) and are not capability-only.
    """
    concrete_or_unresolved: set[str] = set()
    for card_name, concrete in CARD_TOOL_ALIASES.items():
        concrete_or_unresolved.add(card_name)
        if concrete in TOOL_NAMES:
            concrete_or_unresolved.add(concrete)
    return frozenset(CARD_TOOL_VOCAB - concrete_or_unresolved)


#: Card-vocabulary names accepted by the validator when
#: ``allow_capabilities=True`` but that don't map to a concrete
#: BaseTool. Derived once at import time from the constants above.
CAPABILITY_ONLY: frozenset[str] = _derive_capability_only()


# ── Validation dataclasses ─────────────────────────────────────────────────


@dataclass(frozen=True)
class ToolNameValidation:
    """Result of validating a collection of tool names.

    Attributes:
        known: All names recognized (concrete + capability-only).
        unknown: Names that failed validation; agents should refuse to
            bind a card with unknown names.
        concrete: Names that resolved to a concrete ``BaseTool.name``
            (via CARD_TOOL_ALIASES or by exact match in TOOL_NAMES).
        capability_only: Names that passed as capabilities but have no
            concrete implementation registered.
    """

    known: frozenset[str] = field(default_factory=frozenset)
    unknown: frozenset[str] = field(default_factory=frozenset)
    concrete: frozenset[str] = field(default_factory=frozenset)
    capability_only: frozenset[str] = field(default_factory=frozenset)

    @property
    def ok(self) -> bool:
        """True iff no unknown names. Capability-only is allowed."""
        return not self.unknown


# ── Predicate helpers ──────────────────────────────────────────────────────


def is_real_tool(name: str) -> bool:
    """Whether *name* matches a concrete ``BaseTool.name`` shipped today."""
    return name in TOOL_NAMES


def is_known_card_tool(name: str) -> bool:
    """Whether *name* is part of the card-level vocabulary (concrete
    or capability-only)."""
    return name in CARD_TOOL_VOCAB or name in TOOL_NAMES


def canonical_tool_name(name: str) -> str | None:
    """Resolve a card-level *name* to a concrete BaseTool name.

    Returns the alias target if present, the name itself if it's a
    real BaseTool, or ``None`` if the name doesn't resolve to either.
    Capability-only names return ``None`` (they pass the validator but
    don't resolve to a concrete BaseTool at this time).
    """
    if name in CARD_TOOL_ALIASES:
        return CARD_TOOL_ALIASES[name]
    if name in TOOL_NAMES:
        return name
    return None


# ── Bulk validator ─────────────────────────────────────────────────────────


def validate_tool_names(
    names: Iterable[str],
    *,
    allow_capabilities: bool = True,
) -> ToolNameValidation:
    """Validate a list of tool names against the catalog.

    Unknown names are always rejected. Capability-only names are
    rejected when ``allow_capabilities=False``.

    Parameters
    ----------
    names
        Iterable of tool names (typically from an ``AgentCard.tools``
        list).
    allow_capabilities
        When ``True`` (default), names that are in
        ``CARD_TOOL_VOCAB`` but don't resolve to a concrete BaseTool
        are accepted as capabilities. When ``False``, those names are
        moved from ``capability_only`` to ``unknown``.

    Returns
    -------
    ToolNameValidation
        Dataclass with disjoint sets describing exactly what the
        validator accepted / rejected.
    """
    names_set = {n for n in names if n}
    unknown: set[str] = set()
    concrete: set[str] = set()
    capability_only: set[str] = set()

    for n in names_set:
        if n in TOOL_NAMES:
            concrete.add(n)
        elif n in CARD_TOOL_VOCAB:
            if n in CAPABILITY_ONLY:
                if allow_capabilities:
                    capability_only.add(n)
                else:
                    unknown.add(n)
            else:
                # Card vocab name that resolves via alias -- add the
                # alias target to concrete; keep the card name itself
                # out of concrete to avoid double-counting.
                target = CARD_TOOL_ALIASES[n]
                if target in TOOL_NAMES:
                    concrete.add(target)
                elif allow_capabilities:
                    capability_only.add(n)
                else:
                    unknown.add(n)
        else:
            unknown.add(n)

    known = frozenset(concrete | capability_only)
    return ToolNameValidation(
        known=known,
        unknown=frozenset(unknown),
        concrete=frozenset(concrete),
        capability_only=frozenset(capability_only),
    )


__all__ = [
    "CAPABILITY_ONLY",
    "CARD_TOOL_ALIASES",
    "CARD_TOOL_VOCAB",
    "TOOL_NAMES",
    "ToolNameValidation",
    "canonical_tool_name",
    "is_known_card_tool",
    "is_real_tool",
    "validate_tool_names",
]
