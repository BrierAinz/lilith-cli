"""Capability router — pick the best sub-agent for a natural-language task.

Closes the gap between *having* a populated sub-agent registry and
*using* it without a hard-coded ``agent_type="..."`` string at every
call site. Inspired by:

- **Aether-Agents** — keyword/tag overlap scoring with explicit weights
- **Omnigent** — declarative capability surfaces + scoring
- **Talon** — small, dependency-free, fully testable module

The router is a pure scoring function over :class:`SubAgentDefinition`
entries. It is **not** a planner and **does not** call an LLM. The
input is a free-form user request (``"audit this diff for SQL
injection"``), the output is a ranked list of
:class:`AgentRoute` candidates with transparent component scores.

Scoring — three weighted components, all 0..1, summed:

    1. **Token overlap** (default weight 0.50)
       Jaccard similarity between lowercased word tokens of the
       request and ``when_to_use + ' ' + ' '.join(tags)`` of the
       definition. Stop words removed.
    2. **Tag hit** (default weight 0.30)
       1.0 if any tag is explicitly mentioned in the request, else 0.0
       (substring match, case-insensitive). Tags are the highest-signal
       routing signal because authors curate them deliberately.
    3. **Tool fit** (default weight 0.20)
       Coverage of the request's tool-hinting tokens (e.g. ``"read"``,
       ``"write"``, ``"search"``, ``"run"``, ``"test"``, ``"audit"``,
       ``"web"``) over the definition's ``allowed_tools`` minus
       ``disallowed_tools``. Penalises definitions that lack the
       capability being asked for.

Total score is in [0.0, 1.0]. Tie-breaker: registered-order (stable).

Usage::

    from lilith_orchestrator.agent_router import AgentRouter
    from lilith_orchestrator.subagents import register_defaults

    register_defaults()
    router = AgentRouter()

    route = router.route("review the recent commits for security issues")
    # AgentRoute(agent_type="auditor" or "security", score=0.83, ...)

    top3 = router.rank("implement the new endpoint")[:3]

The router can be used in two modes:

- **Stateless** — pass an explicit list of definitions to
  :meth:`AgentRouter.rank`.
- **Registry-backed** — leave it as ``None`` to use the live global
  sub-agent registry (see :mod:`lilith_orchestrator.subagents`).

A small :func:`heuristic_tool_hint` helper extracts tool-hinting words
from free-form text. It is intentionally simple — no LLM, no
POS-tagging. The point is to make tool-fit *interpretable* so product
authors can debug routing decisions and tune weights.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from lilith_orchestrator.subagents import (
    SubAgentDefinition,
    all_agents,
    get_agent,
)

__all__ = [
    "AgentRouter",
    "AgentRoute",
    "RouterWeights",
    "STOP_WORDS",
    "TOOL_HINTS",
    "extract_tokens",
    "heuristic_tool_hint",
    "score_definition",
]


# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────


# Common English stop words we never want in routing tokens. Kept small
# on purpose — we want signal, not noise filtering.
STOP_WORDS: frozenset[str] = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "by", "do", "for",
        "from", "has", "have", "i", "in", "is", "it", "me", "my", "of",
        "on", "or", "please", "so", "that", "the", "this", "to", "we",
        "what", "when", "where", "which", "who", "why", "with", "you",
        "your", "our", "us", "if", "then", "than", "but", "not", "no",
        "yes", "ok", "okay", "can", "could", "would", "should", "will",
        "shall", "may", "might", "must", "just", "also", "any", "all",
        "some", "few", "more", "most", "less", "least",
    }
)


# Map of user-facing verbs / nouns to canonical tool names. Designed
# for **tight, single-intent** mapping: each hint verb maps to the
# *most likely* tool, not every tool that could conceivably fit. This
# keeps ``heuristic_tool_hint`` sharp — "read" → ``read_file`` (not
# ``read_file`` + ``search_files``), so the tool-fit score reflects
# the user's *primary* intent rather than the union of plausible
# tools.
TOOL_HINTS: dict[str, tuple[str, ...]] = {
    # reading
    "read": ("read_file",),
    "look": ("read_file",),
    "find": ("search_files",),
    "search": ("search_files",),
    "grep": ("search_files",),
    "open": ("read_file",),
    "inspect": ("read_file",),
    "view": ("read_file",),
    "show": ("read_file",),
    "list": ("search_files",),
    # writing / mutating
    "write": ("write_file",),
    "edit": ("patch",),
    "modify": ("patch",),
    "patch": ("patch",),
    "create": ("write_file",),
    "add": ("write_file",),
    "delete": ("write_file",),
    "remove": ("write_file",),
    "fix": ("patch",),
    "implement": ("write_file",),
    "refactor": ("patch",),
    # execution
    "run": ("terminal",),
    "execute": ("terminal",),
    "test": ("terminal",),
    "verify": ("terminal",),
    "validate": ("terminal",),
    "deploy": ("terminal",),
    "build": ("terminal",),
    "install": ("terminal",),
    "compile": ("terminal",),
    "lint": ("terminal",),
    "format": ("terminal",),
    # audit / review (read + search)
    "audit": ("search_files",),
    "review": ("read_file",),
    "scan": ("search_files",),
    "check": ("search_files",),
    # web
    "fetch": ("web_search",),
    "browse": ("browser",),
    "download": ("terminal",),
    "web": ("web_search",),
    "online": ("web_search",),
    "query": ("search_files",),
    "lookup": ("web_search",),
    # pure reasoning (no tool needed)
    "plan": (),
    "think": (),
    "design": (),
    "draft": (),
}


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


# Word-boundary regex — match letters/digits/underscores, starting
# with a letter. Allows ``read_file`` to be one token, which matters
# for tool-name matching against the request.
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]+")

# Minimum-token length for routing. 3-letter floor drops 1- and 2-char
# tokens (``a``, ``I``, ``am``, ``be``, ``do``) which carry no routing
# signal. 3 still keeps ``run``, ``fix``, ``web``, ``log``, ``bug`` —
# all common action words.
_MIN_TOKEN_LEN = 3


def extract_tokens(text: str) -> list[str]:
    """Lowercase, alpha-only word tokens with stop words removed.

    Order preserved. Duplicates kept (so repeated emphasis is mildly
    rewarded in Jaccard — same input and output tokens still align).
    """
    if not text:
        return []
    raw = (m.group(0).lower() for m in _TOKEN_RE.finditer(text))
    return [
        t for t in raw
        if t not in STOP_WORDS and len(t) >= _MIN_TOKEN_LEN
    ]


def _jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 0.0
    union = sa | sb
    if not union:
        return 0.0
    return len(sa & sb) / len(union)


def _word_in(needle: str, haystack: str) -> bool:
    """Word-boundary substring match: ``needle`` is a whole word in
    ``haystack``. Case-sensitive on the boundary regex but both
    inputs are expected pre-lowered.

    Implementation: wrap needle in ``r"\\b...\\b"`` and compile, then
    :func:`re.search`. A non-word-character or string boundary on each
    side of ``needle`` is required. This means ``"plan"`` is **not**
    found in ``"planning"`` (good) but ``"plan"`` is found in
    ``"my plan is"`` (good).
    """
    if not needle or not haystack:
        return False
    pattern = r"\b" + re.escape(needle) + r"\b"
    return re.search(pattern, haystack) is not None


def heuristic_tool_hint(text: str) -> set[str]:
    """Return the canonical tool names hinted at by ``text``.

    Looks up each non-stop token in :data:`TOOL_HINTS` and unions the
    result. Returns an empty set when no verb matches — that's fine,
    the router just gives the tool-fit component 0.0.

    Only **exact** token matches count (no substring matching), so
    ``"implementation"`` does not match the ``"implement"`` verb hint
    and ``"read"`` does not match the ``"read_file"`` tool name. This
    keeps the hint set tight and makes the tool-fit signal
    interpretable.
    """
    if not text:
        return set()
    found: set[str] = set()
    for tok in extract_tokens(text):
        tools = TOOL_HINTS.get(tok)
        if tools:
            for t in tools:
                found.add(t)
    return found


# ──────────────────────────────────────────────────────────────────────────────
# Weights and route
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RouterWeights:
    """Per-component weights. Sum need not be 1.0; raw scores are
    multiplied by these and the result is renormalised to [0, 1] at
    the end (so a single 1.0 component dominates correctly).

    Defaults are the routing-shape used in production routing at
    Yggdrasil: tag hits matter most, then free-form text, then tools.
    """

    token_overlap: float = 0.50
    tag_hit: float = 0.30
    tool_fit: float = 0.20

    def __post_init__(self) -> None:  # type: ignore[override]
        if min(self.token_overlap, self.tag_hit, self.tool_fit) < 0:
            raise ValueError("RouterWeights components must be non-negative")
        if (
            self.token_overlap + self.tag_hit + self.tool_fit
        ) <= 0:
            raise ValueError("At least one RouterWeights component must be > 0")


@dataclass(frozen=True)
class AgentRoute:
    """A single scored routing decision.

    Attributes:
        agent_type: The sub-agent ``agent_type`` string.
        score: Final composite score in [0, 1]. Higher is better.
        token_overlap: Jaccard score for ``request`` vs description+tags.
        tag_hit: 1.0 if any tag was mentioned, else 0.0.
        tool_fit: Fraction of hinted tools that the definition permits.
        matched_tags: Tags from the definition found in the request.
        matched_tools: Hinted tools the definition can actually use.
        available_tools: Resolved allowed-tools (minus disallowed) for
            the definition given the registry's known tool set, or
            ``None`` when the router has no tool-pool context.
    """

    agent_type: str
    score: float
    token_overlap: float
    tag_hit: float
    tool_fit: float
    matched_tags: tuple[str, ...] = ()
    matched_tools: tuple[str, ...] = ()
    available_tools: tuple[str, ...] | None = None

    def to_dict(self) -> dict:
        """JSON-safe dict for logging / API responses."""
        return {
            "agent_type": self.agent_type,
            "score": round(self.score, 4),
            "token_overlap": round(self.token_overlap, 4),
            "tag_hit": round(self.tag_hit, 4),
            "tool_fit": round(self.tool_fit, 4),
            "matched_tags": list(self.matched_tags),
            "matched_tools": list(self.matched_tools),
            "available_tools": (
                list(self.available_tools)
                if self.available_tools is not None
                else None
            ),
        }


def score_definition(
    request: str,
    defn: SubAgentDefinition,
    weights: RouterWeights,
    tool_pool: Sequence[str] | None = None,
) -> AgentRoute:
    """Score a single definition against ``request``.

    Exposed as a free function so callers can build their own routers
    or score against non-registry definitions (e.g. drafts, mocks).
    Pure — no I/O, no LLM, deterministic.
    """
    request_tokens = extract_tokens(request)

    # Build the "definition text" we compare against: when_to_use first
    # (the author-curated hint), then tags. We do NOT include the
    # system prompt because that is usually a multi-sentence persona
    # description, not a routing hint.
    defn_text_tokens = extract_tokens(
        f"{defn.when_to_use} {' '.join(defn.tags)}"
    )
    token_overlap = _jaccard(request_tokens, defn_text_tokens)

    # Tag hit — does the request explicitly mention a tag?
    # Use word-boundary matching so "implementation" does not match
    # the "implement" verb hint nor "plan" match the "planning" tag.
    # Substring matching was a footgun: it over-amplifies tag hits and
    # routes requests to the wrong persona.
    request_lower = (request or "").lower()
    matched_tags = tuple(
        t for t in defn.tags
        if t and _word_in(t.lower(), request_lower)
    )
    tag_hit = 1.0 if matched_tags else 0.0

    # Tool fit — does the definition actually own the hinted tools?
    hinted = heuristic_tool_hint(request)
    if hinted:
        if tool_pool is not None:
            available = set(defn.resolve_tools(list(tool_pool)))
        else:
            # Without a tool pool, treat allowed_tools as available
            # (resolved from defn.allowed_tools minus disallowed_tools
            # but ignoring parent intersection). This is the
            # "schema-only" view, used when the router has no executor
            # context.
            if defn.allowed_tools == ["*"]:
                available = set()  # unknown without parent pool
            else:
                available = set(defn.allowed_tools) - set(
                    defn.disallowed_tools
                )
        matched_tools = sorted(hinted & available)
        tool_fit = len(matched_tools) / len(hinted)
        available_tuple: tuple[str, ...] | None = tuple(sorted(available))
    else:
        matched_tools = ()
        tool_fit = 0.0
        available_tuple = (
            tuple(sorted(tool_pool)) if tool_pool is not None else None
        )

    # Weighted sum, then renormalise to [0, 1].
    wsum = (
        weights.token_overlap * token_overlap
        + weights.tag_hit * tag_hit
        + weights.tool_fit * tool_fit
    )
    wmax = (
        weights.token_overlap + weights.tag_hit + weights.tool_fit
    )
    score = wsum / wmax if wmax > 0 else 0.0

    return AgentRoute(
        agent_type=defn.agent_type,
        score=score,
        token_overlap=token_overlap,
        tag_hit=tag_hit,
        tool_fit=tool_fit,
        matched_tags=matched_tags,
        matched_tools=tuple(matched_tools),
        available_tools=available_tuple,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Router
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class AgentRouter:
    """Score and rank sub-agent definitions for a free-form request.

    Args:
        weights: Per-component scoring weights. Defaults to
            :class:`RouterWeights` (token=0.5, tag=0.3, tool=0.2).
        tool_pool: Optional list of all tool names available in the
            parent environment. When ``None``, tool-fit is computed
            from ``defn.allowed_tools`` minus ``defn.disallowed_tools``
            (the schema-only view), which is correct for *routing*
            but does not check parent pool intersection.
        registry: Iterable of :class:`SubAgentDefinition` to rank
            against. When ``None`` (default), uses
            :func:`all_agents` from the live sub-agent registry. Pass
            an explicit list to make the router deterministic in
            tests.
        min_score: Routes with score below this threshold are dropped
            from :meth:`rank`'s output. Set to ``0.0`` to keep all
            candidates. Default ``0.0``.
    """

    weights: RouterWeights = field(default_factory=RouterWeights)
    tool_pool: Sequence[str] | None = None
    registry: Sequence[SubAgentDefinition] | None = None
    min_score: float = 0.0

    def __post_init__(self) -> None:  # type: ignore[override]
        if not 0.0 <= self.min_score <= 1.0:
            raise ValueError("min_score must be in [0, 1]")

    # ── Single-shot API ────────────────────────────────────────────────

    def route(self, request: str) -> AgentRoute | None:
        """Return the highest-scoring route, or ``None`` when no
        candidate clears :attr:`min_score`."""
        ranked = self.rank(request, limit=1)
        return ranked[0] if ranked else None

    # ── Multi-candidate API ────────────────────────────────────────────

    def rank(
        self,
        request: str,
        limit: int | None = None,
    ) -> list[AgentRoute]:
        """Score every definition and return the sorted list.

        Args:
            request: Free-form user request.
            limit: Optional cap on the number of routes returned.

        Returns:
            Sorted list of :class:`AgentRoute`, highest score first.
            Empty list when the registry is empty or nothing clears
            :attr:`min_score`.
        """
        defs = self._definitions()
        if not defs:
            return []
        scored = [
            score_definition(request, d, self.weights, self.tool_pool)
            for d in defs
        ]
        scored.sort(key=lambda r: (-r.score, r.agent_type))
        if self.min_score > 0.0:
            scored = [r for r in scored if r.score >= self.min_score]
        if limit is not None and limit >= 0:
            scored = scored[:limit]
        return scored

    # ── Introspection helpers ──────────────────────────────────────────

    def best_match_tags(self, request: str) -> list[str]:
        """Return the tags most consistently associated with high-score
        candidates for ``request`` (across the whole registry). Useful
        for ``ygg route --explain`` style debugging.
        """
        return _aggregate_top_tags(self.rank(request), top_n=5)

    def explain(self, request: str) -> list[dict]:
        """Score every definition and return the JSON-safe dict form.
        Equivalent to ``[r.to_dict() for r in self.rank(request)]`` but
        keeps the call site symmetric with :meth:`route`/:meth:`rank`.
        """
        return [r.to_dict() for r in self.rank(request)]

    # ── Internals ──────────────────────────────────────────────────────

    def _definitions(self) -> list[SubAgentDefinition]:
        if self.registry is not None:
            return list(self.registry)
        return all_agents()


# ──────────────────────────────────────────────────────────────────────────────
# Tag-aggregation helper (used by best_match_tags + explain tests)
# ──────────────────────────────────────────────────────────────────────────────


def _aggregate_top_tags(
    routes: Sequence[AgentRoute], top_n: int = 5
) -> list[str]:
    """Return the top-N tags by aggregate score (sum of route.score
    for each tag that appears in route.matched_tags). Falls back to
    empty list when no routes carry matched tags.
    """
    if not routes:
        return []
    scores: dict[str, float] = {}
    for r in routes:
        for tag in r.matched_tags:
            scores[tag] = scores.get(tag, 0.0) + r.score
    if not scores:
        return []
    ordered = sorted(
        scores.items(), key=lambda kv: (-kv[1], kv[0])
    )
    return [tag for tag, _ in ordered[:top_n]]


# ──────────────────────────────────────────────────────────────────────────────
# Convenience: route a request to a single SubAgentDefinition
# ──────────────────────────────────────────────────────────────────────────────


def route_request(
    request: str,
    weights: RouterWeights | None = None,
    tool_pool: Sequence[str] | None = None,
) -> SubAgentDefinition | None:
    """Convenience wrapper: return the chosen definition, or ``None``
    when the registry is empty. Useful in code paths that only need
    the definition (the caller will spawn it themselves via
    :class:`SubAgentRunner`).
    """
    router = AgentRouter(weights=weights or RouterWeights(),
                         tool_pool=tool_pool)
    route = router.route(request)
    if route is None:
        return None
    return get_agent(route.agent_type)
