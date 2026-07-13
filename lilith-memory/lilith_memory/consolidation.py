"""Memory consolidation — orchestrates Working → Episodic → Semantic flow."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .layers.episodic_memory import EpisodicMemory  # noqa: TC001
from .layers.semantic_memory import SemanticMemory  # noqa: TC001


if TYPE_CHECKING:
    from .layers.working_memory import WorkingMemory


# ------------------------------------------------------------------
# Fact extraction patterns
# ------------------------------------------------------------------

# Preference patterns: "I like X", "I prefer X", "I hate X", "I love X",
# "I dislike X", "My favorite X is Y"
_PREFERENCE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"i\s+(?:really\s+|definitely\s+)?(?:like|love|enjoy|prefer)\s+(.+?)(?:\.|$)",
            re.IGNORECASE,
        ),
        "positive",
    ),
    (
        re.compile(
            r"i\s+(?:really\s+|definitely\s+)?(?:hate|dislike|don'?\s*t\s+like|don'?\s*t\s+enjoy)\s+(.+?)(?:\.|$)",
            re.IGNORECASE,
        ),
        "negative",
    ),
    (re.compile(r"my\s+favorite\s+\w+\s+is\s+(.+?)(?:\.|$)", re.IGNORECASE), "positive"),
    (
        re.compile(
            r"i\s+(?:prefer|rather)\s+(.+?)(?:\s+over|than|\s+to)\s+(.+?)(?:\.|$)",
            re.IGNORECASE,
        ),
        "positive",
    ),
]

# Identity patterns: "My name is X", "I am X", "Call me X"
_IDENTITY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"my\s+name\s+is\s+(.+?)(?:\.|$)", re.IGNORECASE),
    re.compile(r"i\s+am\s+(.+?)(?:\.|$)", re.IGNORECASE),
    re.compile(r"call\s+me\s+(.+?)(?:\.|$)", re.IGNORECASE),
]

# Procedure patterns: "To do X, you need to Y", "How to X: Y"
_PROCEDURE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"(?:to|in\s+order\s+to)\s+(.+?)\s*,?\s+(?:you\s+)?(?:need\s+to|must|should|have\s+to)\s+(.+?)(?:\.|$)",
        re.IGNORECASE,
    ),
    re.compile(r"how\s+to\s+(.+?)\s*[:\-]\s*(.+?)(?:\.|$)", re.IGNORECASE),
]


@dataclass
class ExtractedFact:
    """A fact extracted from content during consolidation."""

    content: str
    fact_type: str  # 'preference', 'identity', 'procedure', 'fact'
    source: str
    confidence: float = 0.7


LONG_TERM_SCOPE = "long_term"


def _metadata_dict(item: dict[str, Any]) -> dict[str, Any]:
    metadata = item.get("metadata")
    if isinstance(metadata, dict):
        return metadata
    if not isinstance(metadata, str) or not metadata:
        return {}
    try:
        parsed = json.loads(metadata)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _is_consolidated(metadata: dict[str, Any]) -> bool:
    return (
        metadata.get("consolidated") is True
        or metadata.get("scope") == LONG_TERM_SCOPE
        or metadata.get("layer") == LONG_TERM_SCOPE
    )


def _recall_all(store: Any, session_id: str) -> list[dict[str, Any]]:
    count_fn = getattr(store, "count_entries", None) or getattr(store, "count", None)
    limit = count_fn() if count_fn else 1000
    return store.recall(session_id, limit=max(limit, 1))


def consolidate_session(store: Any, session_id: str | None = None) -> dict[str, Any]:
    """Copy session memories into the store's durable long-term scope.

    This is the synchronous operational lifecycle for ``MemoryStore``. It does
    exactly this: reads memories from ``session_id`` (or every known session
    when ``session_id`` is ``None``), treats non-consolidated rows as short-term
    session memories, and writes idempotent long-term copies back to the same
    store under the same session with metadata ``scope='long_term'``,
    ``layer='long_term'``, ``consolidated=True``, ``source_session_id``, and
    ``source_memory_id``. It does not summarize, delete originals, mutate
    source rows, or enforce authorization; read policies still run through the
    read guard at recall/search time.
    """
    sessions = [session_id] if session_id is not None else store.sessions()
    scanned = 0
    consolidated = 0
    skipped = 0

    for current_session in sessions:
        items = _recall_all(store, current_session)
        existing_sources = {
            str(metadata.get("source_memory_id"))
            for metadata in (_metadata_dict(item) for item in items)
            if _is_consolidated(metadata) and metadata.get("source_memory_id") is not None
        }
        candidates = []
        for item in items:
            metadata = _metadata_dict(item)
            if _is_consolidated(metadata):
                skipped += 1
                continue
            candidates.append((item, metadata))

        scanned += len(candidates)
        for item, metadata in candidates:
            source_id = str(item.get("id"))
            if source_id in existing_sources:
                skipped += 1
                continue
            long_term_metadata = {
                **metadata,
                "scope": LONG_TERM_SCOPE,
                "layer": LONG_TERM_SCOPE,
                "consolidated": True,
                "source_session_id": item.get("session_id", current_session),
                "source_memory_id": source_id,
                "source_role": item.get("role"),
            }
            store.store(
                current_session,
                "memory",
                item.get("content", ""),
                long_term_metadata,
            )
            consolidated += 1

    return {
        "sessions": len(sessions),
        "scanned": scanned,
        "consolidated": consolidated,
        "skipped": skipped,
    }


class MemoryConsolidator:
    """Orchestrates memory flow across layers.

    The consolidation pipeline moves information from short-term volatile
    storage (WorkingMemory) through medium-term decay-aware storage
    (EpisodicMemory) into permanent fact storage (SemanticMemory).

    Usage::

        consolidator = MemoryConsolidator(working, episodic, semantic)
        await consolidator.consolidate_session("session-123")
    """

    def __init__(
        self,
        working: WorkingMemory,
        episodic: EpisodicMemory,
        semantic: SemanticMemory,
    ) -> None:
        """Initialise the consolidator with the three memory layers.

        Args:
            working: The volatile working memory layer.
            episodic: The medium-term episodic memory layer.
            semantic: The permanent semantic memory layer.

        """
        self._working = working
        self._episodic = episodic
        self._semantic = semantic

    # ------------------------------------------------------------------
    # Fact extraction (pattern-based, no LLM)
    # ------------------------------------------------------------------

    @staticmethod
    def extract_facts(content: str) -> list[ExtractedFact]:
        """Extract facts from *content* using pattern matching.

        The current implementation uses regex patterns to detect
        preferences, identity statements, and procedures.  Anything
        that doesn't match a known pattern is recorded as a generic
        ``fact`` with a lower confidence.

        Args:
            content: The text to analyse.

        Returns:
            A list of :class:`ExtractedFact` instances.

        """
        facts: list[ExtractedFact] = []
        seen: set[str] = set()

        # --- Preference facts ---
        for pattern, sentiment in _PREFERENCE_PATTERNS:
            for match in pattern.finditer(content):
                extracted = match.group(1).strip().rstrip(".")
                key = f"preference:{extracted}"
                if key not in seen:
                    seen.add(key)
                    prefix = "" if sentiment == "positive" else "dislikes "
                    facts.append(
                        ExtractedFact(
                            content=f"User {prefix}{extracted}",
                            fact_type="preference",
                            source="pattern_extraction",
                            confidence=0.7,
                        ),
                    )

        # --- Identity facts ---
        for pattern in _IDENTITY_PATTERNS:
            for match in pattern.finditer(content):
                extracted = match.group(1).strip().rstrip(".")
                key = f"identity:{extracted}"
                if key not in seen:
                    seen.add(key)
                    facts.append(
                        ExtractedFact(
                            content=f"User is {extracted}",
                            fact_type="identity",
                            source="pattern_extraction",
                            confidence=0.7,
                        ),
                    )

        # --- Procedure facts ---
        for pattern in _PROCEDURE_PATTERNS:
            for match in pattern.finditer(content):
                goal = match.group(1).strip().rstrip(".")
                steps = match.group(2).strip().rstrip(".")
                key = f"procedure:{goal}"
                if key not in seen:
                    seen.add(key)
                    facts.append(
                        ExtractedFact(
                            content=f"To {goal}: {steps}",
                            fact_type="procedure",
                            source="pattern_extraction",
                            confidence=0.7,
                        ),
                    )

        return facts

    # ------------------------------------------------------------------
    # Consolidation pipeline
    # ------------------------------------------------------------------

    async def consolidate_session(self, session_id: str) -> dict[str, Any]:
        """Run the full consolidation pipeline for a given session.

        The pipeline:
        1. Move all working memory items into episodic memory.
        2. Analyse episodic memories for this session to extract facts.
        3. Store extracted facts in semantic memory (or boost
           confidence if the fact already exists).

        Args:
            session_id: The session identifier used to group memories.

        Returns:
            A dict with counts of items moved and facts extracted.

        """
        # Step 1: Working → Episodic
        working_items = await self._working.get_recent(n=await self._working.count())
        moved_count = 0
        for item in working_items:
            await self._episodic.add(
                content=item["content"],
                metadata=item.get("metadata"),
                session_id=session_id,
            )
            moved_count += 1

        # Clear working memory after transfer
        await self._working.clear()

        # Step 2: Episodic → analyse for facts
        episodic_items = await self._episodic.search(query="", limit=1000)
        # Filter to session
        session_items = [i for i in episodic_items if i.get("session_id") == session_id]

        facts_extracted = 0
        for item in session_items:
            content = item.get("content", "")
            if not content:
                continue
            extracted = self.extract_facts(content)
            for fact in extracted:
                await self._semantic.add(
                    content=fact.content,
                    fact_type=fact.fact_type,
                    source=fact.source,
                    confidence=fact.confidence,
                )
                facts_extracted += 1

        # Step 3: Prune expired episodic memories
        pruned = await self._episodic.prune_expired()

        return {
            "moved_to_episodic": moved_count,
            "facts_extracted": facts_extracted,
            "episodic_pruned": pruned,
        }
