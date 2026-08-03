"""Semantic tool matching using sentence-transformers with keyword fallback."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from lilith_tools.base import BaseTool


# Lazy imports — only loaded when needed
_model = None
_embedding_cache: dict[str, Any] = {}
_semantic_available: bool | None = None


def _is_semantic_available() -> bool:
    """Check if sentence-transformers is available (lazy, cached)."""
    global _semantic_available
    if _semantic_available is None:
        try:
            import sentence_transformers  # noqa: F401 — availability check

            _semantic_available = True
        except ImportError:
            _semantic_available = False
    return _semantic_available


def _get_model() -> Any:
    """Load and cache the sentence-transformers model (lazy)."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer("all-MiniLM-L6-v2")
    return _model


@dataclass
class MatchResult:
    """Result of a tool matching operation."""

    tool_name: str
    score: float
    description: str


class ToolMatcher:
    """Match natural-language queries to the most relevant tools.

    Uses sentence-transformers for semantic similarity when available,
    falling back to Jaccard-based keyword matching otherwise.
    """

    def __init__(self) -> None:
        self._embeddings: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def match(
        self,
        query: str,
        tools: dict[str, type[BaseTool]],
        top_k: int = 3,
    ) -> list[MatchResult]:
        """Return the *top_k* tools most relevant to *query*.

        Builds / refreshes the embedding cache when the tool set changes.
        """
        if not tools:
            return []

        self._build_tool_embeddings(tools)

        if _is_semantic_available():
            return self._semantic_match(query, tools, top_k)
        return self._keyword_match(query, tools, top_k)

    @staticmethod
    def compute_similarity(text1: str, text2: str) -> float:
        """Compute cosine similarity between two texts.

        Uses sentence-transformers when available, otherwise Jaccard.
        """
        if _is_semantic_available():
            model = _get_model()
            embeddings = model.encode([text1, text2])
            vec1 = embeddings[0]
            vec2 = embeddings[1]
            dot = sum(a * b for a, b in zip(vec1, vec2))
            norm1 = math.sqrt(sum(a * a for a in vec1))
            norm2 = math.sqrt(sum(b * b for b in vec2))
            if norm1 == 0 or norm2 == 0:
                return 0.0
            return dot / (norm1 * norm2)
        return _jaccard_similarity(text1, text2)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_tool_embeddings(self, tools: dict[str, type[BaseTool]]) -> None:
        """Build / update the embedding cache for registered tools."""
        current_keys = set(tools.keys())
        cached_keys = set(self._embeddings.keys())

        # Rebuild only when the tool set has changed
        if cached_keys == current_keys:
            return

        self._embeddings.clear()

        texts: dict[str, str] = {}
        for name, tool_cls in tools.items():
            combined = f"{name}. {tool_cls.description}"
            texts[name] = combined

        if _is_semantic_available():
            model = _get_model()
            names = list(texts.keys())
            embeddings = model.encode([texts[n] for n in names])
            for idx, name in enumerate(names):
                self._embeddings[name] = embeddings[idx]
        else:
            # Store pre-tokenized keyword sets for fallback
            for name, text in texts.items():
                self._embeddings[name] = _tokenize(text)

    def _semantic_match(
        self,
        query: str,
        tools: dict[str, type[BaseTool]],
        top_k: int,
    ) -> list[MatchResult]:
        """Semantic matching using sentence-transformers embeddings."""
        model = _get_model()
        query_embedding = model.encode(query)

        scores: list[tuple[str, float]] = []
        for name, emb in self._embeddings.items():
            score = _cosine_similarity_vec(query_embedding, emb)
            scores.append((name, float(score)))

        scores.sort(key=lambda x: x[1], reverse=True)
        return [
            MatchResult(
                tool_name=name,
                score=score,
                description=tools[name].description,
            )
            for name, score in scores[:top_k]
        ]

    def _keyword_match(
        self,
        query: str,
        tools: dict[str, type[BaseTool]],
        top_k: int,
    ) -> list[MatchResult]:
        """Fallback keyword matching using Jaccard similarity."""
        query_tokens = _tokenize(query)

        scores: list[tuple[str, float]] = []
        for name, tool_tokens in self._embeddings.items():
            score = _jaccard_from_tokens(query_tokens, tool_tokens)
            scores.append((name, score))

        scores.sort(key=lambda x: x[1], reverse=True)
        return [
            MatchResult(
                tool_name=name,
                score=score,
                description=tools[name].description,
            )
            for name, score in scores[:top_k]
        ]


# ------------------------------------------------------------------
# Utility functions
# ------------------------------------------------------------------


def _tokenize(text: str) -> set[str]:
    """Lowercase and split *text* into a set of alphanumeric tokens."""
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _jaccard_similarity(text1: str, text2: str) -> float:
    """Compute Jaccard similarity between two texts."""
    t1 = _tokenize(text1)
    t2 = _tokenize(text2)
    return _jaccard_from_tokens(t1, t2)


def _jaccard_from_tokens(set1: set[str], set2: set[str]) -> float:
    """Compute Jaccard similarity between two token sets."""
    if not set1 and not set2:
        return 0.0
    intersection = set1 & set2
    union = set1 | set2
    return len(intersection) / len(union) if union else 0.0


def _cosine_similarity_vec(vec1: Any, vec2: Any) -> float:
    """Compute cosine similarity between two numeric vectors."""
    dot = sum(a * b for a, b in zip(vec1, vec2))
    norm1 = math.sqrt(sum(a * a for a in vec1))
    norm2 = math.sqrt(sum(b * b for b in vec2))
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot / (norm1 * norm2)
