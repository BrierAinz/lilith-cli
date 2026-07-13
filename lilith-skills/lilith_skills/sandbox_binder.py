"""AgentCard → SandboxPolicy auto-binding.

Bridges lilith-skills (AgentCard) with lilith-core (SandboxPolicy).
Given an AgentCard (loaded from Vanaheim/Agents/agent_cards.yaml),
auto-generate a SandboxPolicy that enforces the card's declared
tool allowlist, plus level-appropriate defaults for execution time,
memory, rate limits, and subprocess / file-write gating.

Design goals
------------
1. **Zero manual policy authoring** — every agent card in Vanaheim
   immediately gets a working sandbox without anyone writing a YAML
   policy file.
2. **Defense-in-depth by default** — level 1 (consultant) agents
   default to a stricter policy than level 2 (executors).
3. **Composable overrides** — caller can pass ``extra_rules`` to
   add custom sandbox rules on top of the auto-derived ones.
4. **Round-trippable** — ``to_dict()`` produces the same shape that
   ``PolicyEngine.from_dict()`` accepts (so policies can be exported
   and re-loaded across realms).

Rule derivation strategy
------------------------
For a card with ``tools=["terminal", "web_search", "read_file"]``:

- ``ALLOWED_TOOLS = tools`` (whitelist from card)
- ``DENIED_TOOLS = []`` (we use whitelist, not blacklist)
- ``MAX_EXEC_TIME``: 60s for level 1, 120s for level 2
- ``MAX_CALLS_PER_MIN``: 30 for level 1, 120 for level 2
- ``MAX_TOKENS``: 8k for level 1, 32k for level 2
- ``NO_SUBPROCESS``: True if ``terminal`` is NOT in tools
- ``NO_FILE_WRITE``: True if ``write_file`` is NOT in tools AND
  ``patch`` is NOT in tools
- ``NO_FILE_DELETE``: True if agent level is 1 (consultants never
  delete; they read and advise)
- ``NO_NETWORK``: True if ``web_search`` is NOT in tools AND no
  other network-capable tool is present
- ``MAX_MEMORY_MB``: 256 default

Inspiration: this closes the loop between the Eter-Agents card
system (agent capability declaration) and the Omnigent-inspired
policy engine (runtime enforcement) that Yggdrasil already has.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable

from lilith_core.hooks import HookContext, HookRegistry, HookType, get_hook_registry
from lilith_core.sandbox import (
    SandboxAction,
    SandboxPolicy,
    SandboxRegistry,
    SandboxRule,
    SandboxRuleType,
)

from lilith_skills.agent_cards import AgentCard, AgentCardLoader

logger = logging.getLogger("lilith.skills.sandbox_binder")


# ── Hook helpers ────────────────────────────────────────────────────────────


#: String aliases for the ``AgentCard.hooks`` YAML field. Agent cards
#: declare hooks as plain strings (e.g. ``hooks: [pre_tool_call]``) for
#: human readability, but the runtime needs the ``HookType`` enum. This
#: mapping is the single source of truth for translation — extend it
#: here when a new lifecycle hook is added in lilith-core.
HOOK_ALIASES: dict[str, HookType] = {
    # LLM lifecycle
    "pre_llm_call": HookType.PRE_LLM_CALL,
    "post_llm_call": HookType.POST_LLM_CALL,
    # Tool lifecycle
    "pre_tool_call": HookType.PRE_TOOL_CALL,
    "pre_tool_use": HookType.PRE_TOOL_CALL,   # Aether-Agents alias
    "post_tool_call": HookType.POST_TOOL_CALL,
    "post_tool_use": HookType.POST_TOOL_CALL,
    "on_tool_result": HookType.ON_TOOL_RESULT,
    # Session lifecycle
    "on_session_start": HookType.ON_SESSION_START,
    "on_session_end": HookType.ON_SESSION_END,
    # Error
    "on_error": HookType.ON_ERROR,
    # Pipeline (orchestrator)
    "pre_pipeline_phase": HookType.PRE_PIPELINE_PHASE,
    "post_pipeline_phase": HookType.POST_PIPELINE_PHASE,
    "pre_pipeline_start": HookType.PRE_PIPELINE_START,
    "post_pipeline_end": HookType.POST_PIPELINE_END,
}


def _default_card_hook_callback(card: AgentCard, hook_type: HookType):
    """Build a no-op pass-through hook callback scoped to one agent.

    The callback passes ``HookContext`` through unchanged so it acts as
    an "audit marker" — its presence in the registry confirms an agent
    card declared subscription to a given lifecycle event. Sidecar
    callers can attach a real implementation by passing
    ``callback=`` to :func:`register_card_hooks`.
    """

    def _callback(ctx: HookContext) -> HookContext:
        # Touch metadata so a debugger can see the card's hook chain ran
        # for this agent. We deliberately do not log here — logging on
        # every tool call would explode the agent's trace volume.
        ctx.metadata.setdefault("agent_card_hooks", []).append(
            {"agent": card.name, "hook": hook_type.value}
        )
        return ctx

    _callback.__name__ = f"{card.name}_{hook_type.value}_audit"
    return _callback


def resolve_hook_type(name: str) -> HookType | None:
    """Translate a string hook name into a ``HookType``.

    Returns ``None`` if the name is unknown — caller decides whether to
    log a warning or fail. Lookup is case-insensitive and ignores
    leading/trailing whitespace.
    """
    if not name:
        return None
    key = str(name).strip().lower()
    return HOOK_ALIASES.get(key)


def register_card_hooks(
    card: AgentCard,
    *,
    registry: HookRegistry | None = None,
    callback=None,
) -> list[tuple[HookType, str]]:
    """Register an ``AgentCard.hooks`` declaration into a HookRegistry.

    For each string in ``card.hooks`` (e.g. ``"pre_tool_call"``), this
    function:

    1. Resolves the string to a :class:`HookType` via :data:`HOOK_ALIASES`.
       Unknown hook names are skipped with a warning (not fatal — keeps
       forward compatibility with cards that declare new hook types
       before lilith-core ships them).
    2. Registers a callback into *registry*. By default, this is the
       audit-only callback returned by
       :func:`_default_card_hook_callback`. Pass a real implementation
       via *callback* to override.
    3. Returns a list of ``(HookType, registration_name)`` tuples
       actually registered, for visibility from ``ygg.py doctor``.

    Parameters
    ----------
    card
        The agent card whose ``hooks`` field should be wired up.
    registry
        Target HookRegistry. Defaults to the global singleton from
        ``lilith_core.hooks.get_hook_registry()``.
    callback
        Optional callable ``HookContext -> HookContext | None`` used
        for every declared hook. When ``None``, the audit pass-through
        callback is used.

    Returns
    -------
    list[tuple[HookType, str]]
        One entry per successfully registered hook.

    Examples
    --------
    >>> from lilith_skills import AgentCardLoader
    >>> from lilith_skills.sandbox_binder import register_card_hooks
    >>> loader = AgentCardLoader.from_vanaheim("/path/to/Yggdrasil")
    >>> heimdall = loader.get_agent("Heimdall")
    >>> registered = register_card_hooks(heimdall)
    """
    if registry is None:
        registry = get_hook_registry()

    registered: list[tuple[HookType, str]] = []
    declared = list(card.hooks or [])
    if not declared:
        return registered

    for hook_name in declared:
        hook_type = resolve_hook_type(hook_name)
        if hook_type is None:
            logger.warning(
                "AgentCard '%s' declared unknown hook '%s'; skipping "
                "(add it to lilith_skills.sandbox_binder.HOOK_ALIASES "
                "if it should be supported).",
                card.name,
                hook_name,
            )
            continue

        cb = callback or _default_card_hook_callback(card, hook_type)
        reg_name = f"{card.name}::{hook_type.value}"
        registry.register(
            hook_type,
            cb,
            name=reg_name,
            priority=10,  # Lower than user-registered hooks (default 0)
        )
        registered.append((hook_type, reg_name))
        logger.debug(
            "Registered AgentCard hook: agent=%s hook=%s",
            card.name,
            hook_type.value,
        )

    return registered


# ── Defaults table ───────────────────────────────────────────────────────────

#: Per-level execution-time budget (seconds).
DEFAULT_MAX_EXEC_TIME: dict[int, float] = {
    1: 60.0,   # Consultants — short answers
    2: 120.0,  # Executors — longer workflows
}

#: Per-level rate limit (calls / minute).
DEFAULT_MAX_CALLS_PER_MIN: dict[int, int] = {
    1: 30,
    2: 120,
}

#: Per-level token budget (estimated).
DEFAULT_MAX_TOKENS: dict[int, int] = {
    1: 8_000,
    2: 32_000,
}

#: Tools that imply subprocess execution (blocked by NO_SUBPROCESS).
_SUBPROCESS_TOOLS = frozenset({"terminal", "coding", "shell", "run_command"})

#: Tools that imply file mutation (blocked by NO_FILE_WRITE).
_WRITE_TOOLS = frozenset({"write_file", "patch", "create_file", "delete_file"})

#: Tools that imply network access.
_NETWORK_TOOLS = frozenset({"web_search", "web_fetch", "http_request", "browser"})


# ── Result dataclass ────────────────────────────────────────────────────────


@dataclass
class BoundSandbox:
    """Result of binding an AgentCard to a SandboxPolicy.

    Attributes:
        agent_name: Name of the agent.
        policy: The derived SandboxPolicy.
        derivation: List of (rule_type, source) tuples explaining where
            each rule came from — useful for ``ygg.py doctor`` output
            and for debugging "why is my agent sandboxed that way?".
        registered_hooks: List of ``(HookType, registration_name)``
            tuples for hooks registered from the card's ``hooks:`` field.
            Empty when no hooks were registered (most common case today —
            see ``register_card_hooks`` for the future-facing wiring).
    """

    agent_name: str
    policy: SandboxPolicy
    derivation: list[tuple[str, str]] = field(default_factory=list)
    registered_hooks: list[tuple[Any, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Export for logging / debugging / YAML round-trip."""
        return {
            "agent_name": self.agent_name,
            "policy": self.policy.to_dict(),
            "derivation": [
                {"rule": rt, "source": src} for rt, src in self.derivation
            ],
            "registered_hooks": [
                {"hook": ht.value if hasattr(ht, "value") else str(ht), "name": nm}
                for ht, nm in self.registered_hooks
            ],
        }


# ── Public API ──────────────────────────────────────────────────────────────


def derive_policy(
    card: AgentCard,
    *,
    extra_rules: Iterable[SandboxRule] = (),
    exec_time_override: float | None = None,
    rate_limit_override: int | None = None,
    token_budget_override: int | None = None,
    memory_mb: int = 256,
    description_prefix: str = "Auto-derived from AgentCard",
) -> SandboxPolicy:
    """Derive a SandboxPolicy from an AgentCard.

    Parameters
    ----------
    card
        The AgentCard to bind.
    extra_rules
        Additional SandboxRule objects appended to the auto-derived
        ones. Use this to layer custom rules (e.g., ``NO_NETWORK``
        for an offline-only agent) on top of the card-derived
        defaults without re-implementing the derivation logic.
    exec_time_override
        Force a specific ``MAX_EXEC_TIME`` (otherwise derived from
        agent level).
    rate_limit_override
        Force a specific ``MAX_CALLS_PER_MIN``.
    token_budget_override
        Force a specific ``MAX_TOKENS``.
    memory_mb
        ``MAX_MEMORY_MB`` value (no level-based default; memory is
        platform-specific).
    description_prefix
        Prepended to the policy description. Useful when exporting
        multiple policies and want a common prefix.

    Returns
    -------
    SandboxPolicy
        A populated policy ready to register with ``SandboxRegistry``
        or hand to ``AgentSandbox(policy=...)``.

    Examples
    --------
    >>> from lilith_skills import AgentCardLoader
    >>> from lilith_skills.sandbox_binder import derive_policy, bind_loader
    >>> loader = AgentCardLoader.from_vanaheim("/path/to/Yggdrasil")
    >>> odin = loader.get_agent("Odin")
    >>> policy = derive_policy(odin)
    >>> policy.has_rule(SandboxRuleType.ALLOWED_TOOLS)
    True
    """
    rules: list[SandboxRule] = []
    derivation: list[tuple[str, str]] = []

    tools_lower = {t.lower() for t in card.tools}

    # 1. ALLOWED_TOOLS — the card's tool list is the whitelist.
    if card.tools:
        rules.append(
            SandboxRule(
                type=SandboxRuleType.ALLOWED_TOOLS,
                value=list(card.tools),
                action=SandboxAction.BLOCK,
            )
        )
        derivation.append(
            (SandboxRuleType.ALLOWED_TOOLS.value, f"card.tools ({len(card.tools)} entries)")
        )

    # 2. NO_SUBPROCESS — if no subprocess-capable tool is in the card.
    if not tools_lower & _SUBPROCESS_TOOLS:
        rules.append(
            SandboxRule(
                type=SandboxRuleType.NO_SUBPROCESS,
                value=True,
                action=SandboxAction.BLOCK,
            )
        )
        derivation.append(
            (SandboxRuleType.NO_SUBPROCESS.value, "no subprocess-capable tool in card")
        )

    # 3. NO_FILE_WRITE — if no write-capable tool is in the card.
    if not tools_lower & _WRITE_TOOLS:
        rules.append(
            SandboxRule(
                type=SandboxRuleType.NO_FILE_WRITE,
                value=True,
                action=SandboxAction.BLOCK,
            )
        )
        derivation.append(
            (SandboxRuleType.NO_FILE_WRITE.value, "no write-capable tool in card")
        )

    # 4. NO_FILE_DELETE — level 1 consultants never delete.
    if card.level == 1:
        rules.append(
            SandboxRule(
                type=SandboxRuleType.NO_FILE_DELETE,
                value=True,
                action=SandboxAction.BLOCK,
            )
        )
        derivation.append(
            (SandboxRuleType.NO_FILE_DELETE.value, "agent.level == 1 (consultant)")
        )

    # 5. NO_NETWORK — if no network-capable tool is in the card.
    if not tools_lower & _NETWORK_TOOLS:
        rules.append(
            SandboxRule(
                type=SandboxRuleType.NO_NETWORK,
                value=True,
                action=SandboxAction.BLOCK,
            )
        )
        derivation.append(
            (SandboxRuleType.NO_NETWORK.value, "no network-capable tool in card")
        )

    # 6. MAX_EXEC_TIME — level-based default.
    exec_time = (
        exec_time_override
        if exec_time_override is not None
        else DEFAULT_MAX_EXEC_TIME.get(card.level, 60.0)
    )
    rules.append(
        SandboxRule(
            type=SandboxRuleType.MAX_EXEC_TIME,
            value=exec_time,
            action=SandboxAction.TERMINATE,
        )
    )
    derivation.append(
        (
            SandboxRuleType.MAX_EXEC_TIME.value,
            f"level {card.level} default ({exec_time}s)"
            if exec_time_override is None
            else f"explicit override ({exec_time}s)",
        )
    )

    # 7. MAX_CALLS_PER_MIN — level-based default.
    rate = (
        rate_limit_override
        if rate_limit_override is not None
        else DEFAULT_MAX_CALLS_PER_MIN.get(card.level, 30)
    )
    rules.append(
        SandboxRule(
            type=SandboxRuleType.MAX_CALLS_PER_MIN,
            value=rate,
            action=SandboxAction.BLOCK,
        )
    )
    derivation.append(
        (
            SandboxRuleType.MAX_CALLS_PER_MIN.value,
            f"level {card.level} default ({rate}/min)"
            if rate_limit_override is None
            else f"explicit override ({rate}/min)",
        )
    )

    # 8. MAX_TOKENS — level-based default.
    tokens = (
        token_budget_override
        if token_budget_override is not None
        else DEFAULT_MAX_TOKENS.get(card.level, 8_000)
    )
    rules.append(
        SandboxRule(
            type=SandboxRuleType.MAX_TOKENS,
            value=tokens,
            action=SandboxAction.BLOCK,
        )
    )
    derivation.append(
        (
            SandboxRuleType.MAX_TOKENS.value,
            f"level {card.level} default ({tokens})"
            if token_budget_override is None
            else f"explicit override ({tokens})",
        )
    )

    # 9. MAX_MEMORY_MB — platform default.
    rules.append(
        SandboxRule(
            type=SandboxRuleType.MAX_MEMORY_MB,
            value=memory_mb,
            action=SandboxAction.BLOCK,
        )
    )
    derivation.append(
        (SandboxRuleType.MAX_MEMORY_MB.value, f"platform default ({memory_mb}MB)")
    )

    # 10. Append caller-supplied extra rules.
    for extra in extra_rules:
        rules.append(extra)
        derivation.append((extra.type.value, "caller extra_rule"))

    description = (
        f"{description_prefix} '{card.name}' "
        f"(level={card.level}, role='{card.role}')"
    )

    return SandboxPolicy(
        name=f"agent:{card.name.lower()}",
        rules=rules,
        description=description,
        enabled=True,
    )


def bind(
    card: AgentCard,
    *,
    registry: SandboxRegistry | None = None,
    register_hooks: bool = False,
    hook_registry: HookRegistry | None = None,
    hook_callback=None,
    **kwargs: Any,
) -> BoundSandbox:
    """Derive a SandboxPolicy from an AgentCard and optionally register it.

    Parameters
    ----------
    card
        The AgentCard to bind.
    registry
        If provided, the resulting policy is registered under the
        agent's name. Use ``get_sandbox_registry()`` from lilith-core
        to get the global singleton.
    register_hooks
        When True, also wire ``card.hooks`` into *hook_registry*
        (or the global singleton if None). See
        :func:`register_card_hooks`.
    hook_registry
        Target hook registry. Ignored when ``register_hooks=False``.
    hook_callback
        Custom hook callback (overrides the default audit marker).
        Ignored when ``register_hooks=False``.
    **kwargs
        Forwarded to :func:`derive_policy`.

    Returns
    -------
    BoundSandbox
        Container with the policy, a derivation trace for
        debugging / ``ygg.py doctor`` output, and the list of hooks
        registered (empty if ``register_hooks=False``).
    """
    policy = derive_policy(card, **kwargs)
    registered_hooks: list[tuple[HookType, str]] = []
    if register_hooks:
        registered_hooks = register_card_hooks(
            card,
            registry=hook_registry,
            callback=hook_callback,
        )

    bound = BoundSandbox(
        agent_name=card.name,
        policy=policy,
        derivation=_trace_from_policy(policy, card),
        registered_hooks=registered_hooks,
    )

    if registry is not None:
        registry.register(card.name, policy)
        logger.info(
            "Registered auto-derived sandbox policy for agent '%s' "
            "(%d rules, %d hooks)",
            card.name, len(policy.rules), len(registered_hooks),
        )

    return bound


def bind_loader(
    loader: AgentCardLoader,
    *,
    registry: SandboxRegistry | None = None,
    register_hooks: bool = False,
    hook_registry: HookRegistry | None = None,
    hook_callback=None,
    validate_tools: bool = False,
    strict_tools: bool = False,
    allow_capabilities: bool = True,
    **kwargs: Any,
) -> list[BoundSandbox]:
    """Bind every AgentCard in a loader to a SandboxPolicy.

    Convenience for ``ygg.py doctor`` and bootstrap flows: load the
    cards, derive a policy per card, register them all into the
    global SandboxRegistry, and return the BoundSandbox list for
    reporting.

    Parameters
    ----------
    loader
        An AgentCardLoader (typically ``from_vanaheim(repo_root)``).
    registry
        Target registry. Defaults to the global singleton from
        lilith-core if not provided.
    register_hooks
        When True, also wire ``card.hooks`` into the global
        HookRegistry for every card. See :func:`register_card_hooks`.
    hook_registry
        Target hook registry. Defaults to the global singleton.
    hook_callback
        Custom hook callback used for every card (e.g. a real
        implementation instead of the audit marker).
    validate_tools
        When ``True``, run ``lilith_skills.card_validator`` over each
        card before binding. By default ``False`` (no-op, preserves
        existing call-site behavior). With
        ``strict_tools=False`` (default), validation problems are
        emitted as warnings via :mod:`logging`. With
        ``strict_tools=True``, the first failing card raises
        :class:`~lilith_skills.card_validator.CardValidationError`.
    strict_tools
        Only meaningful when ``validate_tools=True``. When
        ``True``, validation failures raise
        :class:`~lilith_skills.card_validator.CardValidationError`
        instead of being logged as warnings. Defaults to ``False``.
    allow_capabilities
        Forwarded to the card validator: when ``True`` (default),
        names in the card vocabulary without a concrete BaseTool
        mapping are accepted as capabilities; when ``False`` they
        are treated as unknowns.
    **kwargs
        Forwarded to :func:`derive_policy` for every card.

    Returns
    -------
    list[BoundSandbox]
        One entry per agent card in the loader.
    """
    if registry is None:
        # Late import to avoid a circular dep at module load time.
        from lilith_core.sandbox import get_sandbox_registry
        registry = get_sandbox_registry()

    # Lazy import to keep module-load order circular-free and so
    # ``card_validator`` can be edited independently of sandbox_binder.
    from lilith_skills.card_validator import (
        CardValidationError,
        validate_card_tools,
    )

    if validate_tools:
        if strict_tools:
            # Fail-loud: raise on the first bad card.
            for card in loader.list_agents():
                try:
                    validate_card_tools(card, allow_capabilities=allow_capabilities)
                except CardValidationError:
                    raise
        else:
            # Soft: log a warning per bad card and continue binding.
            for card in loader.list_agents():
                try:
                    validate_card_tools(card, allow_capabilities=allow_capabilities)
                except CardValidationError as exc:
                    logger.warning(
                        "AgentCard '%s' failed tool validation: %s",
                        card.name,
                        exc,
                    )

    bound: list[BoundSandbox] = []
    for card in loader.list_agents():
        bound.append(
            bind(
                card,
                registry=registry,
                register_hooks=register_hooks,
                hook_registry=hook_registry,
                hook_callback=hook_callback,
                **kwargs,
            )
        )
    return bound


def bind_vanaheim(
    repo_root: str,
    *,
    registry: SandboxRegistry | None = None,
    **kwargs: Any,
) -> list[BoundSandbox]:
    """One-shot helper: load Vanaheim agent cards and bind them all.

    This is the typical entry point — load cards from
    ``<repo_root>/Vanaheim/Agents/agent_cards.yaml``, derive policies,
    and register them in the global sandbox registry.

    Parameters
    ----------
    repo_root
        Path to the Yggdrasil monorepo root (or any directory
        containing Vanaheim/Agents/agent_cards.yaml).
    registry
        Optional target registry. Defaults to the global singleton.
    **kwargs
        Forwarded to :func:`derive_policy`.

    Returns
    -------
    list[BoundSandbox]
        One entry per agent card found in the YAML.
    """
    loader = AgentCardLoader.from_vanaheim(repo_root)
    return bind_loader(loader, registry=registry, **kwargs)


# ── Internal helpers ────────────────────────────────────────────────────────


def _trace_from_policy(
    policy: SandboxPolicy, card: AgentCard
) -> list[tuple[str, str]]:
    """Build a derivation trace for a policy + card.

    Reconstructs the ``(rule_type, source)`` pairs the same way
    :func:`derive_policy` did, but without re-running the rules.
    Used by :func:`bind` so callers see the derivation in the
    ``BoundSandbox`` regardless of whether ``extra_rules`` were
    passed.
    """
    tools_lower = {t.lower() for t in card.tools}
    trace: list[tuple[str, str]] = []
    for rule in policy.rules:
        if rule.type == SandboxRuleType.ALLOWED_TOOLS:
            trace.append(
                (rule.type.value, f"card.tools ({len(card.tools)} entries)")
            )
        elif rule.type == SandboxRuleType.NO_SUBPROCESS:
            trace.append(
                (rule.type.value, "no subprocess-capable tool in card")
            )
        elif rule.type == SandboxRuleType.NO_FILE_WRITE:
            trace.append(
                (rule.type.value, "no write-capable tool in card")
            )
        elif rule.type == SandboxRuleType.NO_FILE_DELETE:
            trace.append(
                (rule.type.value, "agent.level == 1 (consultant)")
            )
        elif rule.type == SandboxRuleType.NO_NETWORK:
            trace.append(
                (rule.type.value, "no network-capable tool in card")
            )
        elif rule.type == SandboxRuleType.MAX_EXEC_TIME:
            trace.append(
                (rule.type.value, f"level {card.level} default ({rule.value}s)")
            )
        elif rule.type == SandboxRuleType.MAX_CALLS_PER_MIN:
            trace.append(
                (rule.type.value, f"level {card.level} default ({rule.value}/min)")
            )
        elif rule.type == SandboxRuleType.MAX_TOKENS:
            trace.append(
                (rule.type.value, f"level {card.level} default ({rule.value})")
            )
        elif rule.type == SandboxRuleType.MAX_MEMORY_MB:
            trace.append(
                (rule.type.value, f"platform default ({rule.value}MB)")
            )
        else:
            trace.append((rule.type.value, "caller extra_rule"))

    return trace