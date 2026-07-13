"""Token optimization system for Lilith.

Inspired by Talon's token optimization features:
    - Response caching: cache LLM responses to avoid redundant API calls
    - Context compression: compress conversation history to fit token limits
    - Token tracking: track token usage per session/agent for budgeting

This module provides:
    - ResponseCache: LLM response cache with TTL and LRU eviction
    - ContextCompressor: compress conversation history to fit token budgets
    - TokenTracker: track token usage with per-session and per-agent budgets
    - estimate_tokens: simple token estimation (~4 chars per token)
"""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any


# ── Token estimation ─────────────────────────────────────────────────────────

# Rough approximation: 1 token ≈ 4 characters for English text.
# This is a heuristic; real tokenizers (tiktoken, etc.) are more precise.
_CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Estimate the number of tokens in a text string.

    Uses a simple heuristic: ~4 characters per token.
    For production, replace with tiktoken or the provider's tokenizer.

    Args:
        text: The text to estimate tokens for.

    Returns:
        Estimated token count (minimum 1 for non-empty text).
    """
    if not text:
        return 0
    return max(1, len(text) // _CHARS_PER_TOKEN)


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    """Estimate total tokens in a list of message dicts.

    Each message contributes:
        - ~4 tokens overhead (role, formatting)
        - tokens from content field

    Args:
        messages: List of message dicts with 'content' and 'role' keys.

    Returns:
        Estimated total token count.
    """
    total = 0
    for msg in messages:
        total += 4  # overhead per message
        content = msg.get("content", "")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    total += estimate_tokens(part.get("text", ""))
    return total


# ── Response Cache ───────────────────────────────────────────────────────────


@dataclass
class CacheEntry:
    """A cached LLM response.

    Attributes:
        key: Cache key (hash of prompt + model + params).
        response: The cached response text.
        tokens_saved: Tokens saved by this cache hit.
        created_at: Timestamp when the entry was created.
        hit_count: Number of times this entry has been served.
    """

    key: str
    response: str
    tokens_saved: int = 0
    created_at: float = field(default_factory=time.time)
    hit_count: int = 0


class ResponseCache:
    """LRU cache for LLM responses with TTL.

    Caches responses keyed by (model, prompt, params) hash.
    Evicts least-recently-used entries when max_size is reached.
    Entries expire after ttl_seconds.

    Usage::

        cache = ResponseCache(max_size=100, ttl_seconds=3600)
        key = cache.make_key("gpt-4", "What is 2+2?", {"temperature": 0})
        if cache.has(key):
            response = cache.get(key)
        else:
            response = call_llm(...)
            cache.put(key, response, tokens_saved=150)
    """

    def __init__(
        self,
        max_size: int = 100,
        ttl_seconds: float = 3600.0,
    ) -> None:
        """Initialize the response cache.

        Args:
            max_size: Maximum number of entries (LRU eviction when exceeded).
            ttl_seconds: Time-to-live for cache entries in seconds.
        """
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._total_hits = 0
        self._total_misses = 0
        self._total_tokens_saved = 0

    @staticmethod
    def make_key(
        model: str,
        prompt: str,
        params: dict[str, Any] | None = None,
    ) -> str:
        """Generate a cache key from model, prompt, and params.

        Args:
            model: LLM model name.
            prompt: The prompt text.
            params: Optional generation parameters (temperature, etc.).

        Returns:
            A hex hash string to use as cache key.
        """
        raw = f"{model}:{prompt}:{sorted((params or {}).items())}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def has(self, key: str) -> bool:
        """Check if a key exists in the cache and is not expired."""
        entry = self._cache.get(key)
        if entry is None:
            return False
        if time.time() - entry.created_at > self.ttl_seconds:
            # Expired — remove it
            del self._cache[key]
            return False
        return True

    def get(self, key: str) -> str | None:
        """Get a cached response by key. Returns None on miss/expiry."""
        if not self.has(key):
            self._total_misses += 1
            return None

        entry = self._cache[key]
        entry.hit_count += 1
        self._total_hits += 1
        self._total_tokens_saved += entry.tokens_saved

        # Move to end (most recently used)
        self._cache.move_to_end(key)
        return entry.response

    def put(self, key: str, response: str, tokens_saved: int = 0) -> None:
        """Store a response in the cache.

        Args:
            key: Cache key (from make_key()).
            response: The LLM response to cache.
            tokens_saved: Estimated tokens saved by future cache hits.
        """
        # Evict LRU if at capacity
        while len(self._cache) >= self.max_size:
            self._cache.popitem(last=False)

        self._cache[key] = CacheEntry(
            key=key,
            response=response,
            tokens_saved=tokens_saved,
        )

    def clear(self) -> None:
        """Clear all cache entries."""
        self._cache.clear()
        self._total_hits = 0
        self._total_misses = 0
        self._total_tokens_saved = 0

    def cleanup_expired(self) -> int:
        """Remove all expired entries. Returns count of removed entries."""
        now = time.time()
        expired = [
            k for k, e in self._cache.items()
            if now - e.created_at > self.ttl_seconds
        ]
        for k in expired:
            del self._cache[k]
        return len(expired)

    @property
    def size(self) -> int:
        """Current number of entries in the cache."""
        return len(self._cache)

    @property
    def hit_rate(self) -> float:
        """Cache hit rate (0.0 to 1.0)."""
        total = self._total_hits + self._total_misses
        return self._total_hits / total if total > 0 else 0.0

    def stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        return {
            "size": self.size,
            "max_size": self.max_size,
            "total_hits": self._total_hits,
            "total_misses": self._total_misses,
            "hit_rate": round(self.hit_rate, 4),
            "tokens_saved": self._total_tokens_saved,
        }


# ── Context Compressor ───────────────────────────────────────────────────────


@dataclass
class CompressionResult:
    """Result of context compression.

    Attributes:
        messages: The compressed message list.
        original_tokens: Token count before compression.
        compressed_tokens: Token count after compression.
        removed_messages: Number of messages removed.
        compression_ratio: compressed / original (lower = more compression).
    """

    messages: list[dict[str, Any]]
    original_tokens: int
    compressed_tokens: int
    removed_messages: int
    compression_ratio: float = 1.0


class ContextCompressor:
    """Compress conversation history to fit within a token budget.

    Strategies (applied in order until under budget):
        1. Truncation: Remove oldest messages (keep system + recent).
        2. Summarization: Replace old messages with a summary.
        3. Deduplication: Remove consecutive duplicate messages.

    Usage::

        compressor = ContextCompressor(max_tokens=4096)
        result = compressor.compress(messages)
        # result.messages fits within max_tokens
    """

    def __init__(
        self,
        max_tokens: int = 4096,
        keep_system_messages: bool = True,
        keep_recent_count: int = 5,
    ) -> None:
        """Initialize the context compressor.

        Args:
            max_tokens: Maximum tokens allowed in the compressed output.
            keep_system_messages: If True, never remove system messages.
            keep_recent_count: Minimum number of recent messages to always keep.
        """
        self.max_tokens = max_tokens
        self.keep_system_messages = keep_system_messages
        self.keep_recent_count = keep_recent_count

    def compress(
        self,
        messages: list[dict[str, Any]],
        summary: str | None = None,
    ) -> CompressionResult:
        """Compress messages to fit within the token budget.

        Args:
            messages: The conversation messages to compress.
            summary: Optional pre-computed summary of old messages.
                If provided, replaces removed messages with a single
                system message containing the summary.

        Returns:
            CompressionResult with the compressed messages and stats.
        """
        original_tokens = estimate_messages_tokens(messages)

        # Strategy 1: Deduplication (always apply — cheapest, removes noise)
        deduped = self._deduplicate(messages)

        if estimate_messages_tokens(deduped) <= self.max_tokens:
            removed = len(messages) - len(deduped)
            compressed_tokens = estimate_messages_tokens(deduped)
            return CompressionResult(
                messages=deduped,
                original_tokens=original_tokens,
                compressed_tokens=compressed_tokens,
                removed_messages=removed,
                compression_ratio=round(compressed_tokens / max(original_tokens, 1), 4),
            )

        # Strategy 2: Truncation (remove oldest, keep recent + system)
        truncated = self._truncate(deduped, summary)

        compressed_tokens = estimate_messages_tokens(truncated)
        removed = len(messages) - len(truncated)
        ratio = compressed_tokens / max(original_tokens, 1)

        return CompressionResult(
            messages=truncated,
            original_tokens=original_tokens,
            compressed_tokens=compressed_tokens,
            removed_messages=removed,
            compression_ratio=round(ratio, 4),
        )

    def _deduplicate(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Remove consecutive duplicate messages (same role + similar content)."""
        if len(messages) <= 1:
            return list(messages)

        result: list[dict[str, Any]] = [messages[0]]
        for msg in messages[1:]:
            prev = result[-1]
            if msg.get("role") == prev.get("role"):
                content = msg.get("content", "")
                prev_content = prev.get("content", "")
                # Skip if content is identical or very similar
                if content == prev_content:
                    continue
                # Skip if content is very short and similar (e.g., "ok", "yes")
                if len(content) < 20 and len(prev_content) < 20:
                    if content.lower() == prev_content.lower():
                        continue
            result.append(msg)
        return result

    def _truncate(
        self,
        messages: list[dict[str, Any]],
        summary: str | None = None,
    ) -> list[dict[str, Any]]:
        """Truncate messages to fit token budget, keeping system + recent."""
        # Separate system messages
        system_msgs: list[dict[str, Any]] = []
        other_msgs: list[dict[str, Any]] = []

        for msg in messages:
            if msg.get("role") == "system":
                system_msgs.append(msg)
            else:
                other_msgs.append(msg)

        # Always keep system messages + recent messages
        keep_count = max(self.keep_recent_count, 1)
        recent = other_msgs[-keep_count:] if len(other_msgs) > keep_count else other_msgs

        # Build the result with system + summary + recent
        result: list[dict[str, Any]] = []

        if self.keep_system_messages:
            result.extend(system_msgs)

        if summary:
            result.append({
                "role": "system",
                "content": f"[Previous conversation summary: {summary}]",
            })

        result.extend(recent)

        # If still over budget, truncate from the front
        while estimate_messages_tokens(result) > self.max_tokens and len(result) > 1:
            # Remove the first non-system message
            for i, msg in enumerate(result):
                if msg.get("role") != "system":
                    result.pop(i)
                    break
            else:
                # Only system messages left — truncate the longest one
                if len(result) > 0:
                    longest_idx = max(
                        range(len(result)),
                        key=lambda i: len(result[i].get("content", "")),
                    )
                    content = result[longest_idx].get("content", "")
                    result[longest_idx]["content"] = content[: self.max_tokens * _CHARS_PER_TOKEN]
                break

        return result


# ── Token Tracker ────────────────────────────────────────────────────────────


@dataclass
class TokenBudget:
    """Token budget for a session or agent.

    Attributes:
        limit: Maximum tokens allowed.
        used: Tokens consumed so far.
        remaining: limit - used.
        exceeded: True if used > limit.
    """

    limit: int
    used: int = 0

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    @property
    def exceeded(self) -> bool:
        return self.used > self.limit

    @property
    def utilization(self) -> float:
        """Usage ratio (0.0 to 1.0+, can exceed 1.0 if over budget)."""
        return self.used / self.limit if self.limit > 0 else 0.0


class TokenTracker:
    """Track token usage per session and per agent.

    Provides budget enforcement and usage analytics.

    Usage::

        tracker = TokenTracker(default_limit=10000)
        tracker.set_session_limit("sess_1", 5000)
        tracker.record("sess_1", "odin", prompt_tokens=100, completion_tokens=200)
        budget = tracker.get_budget("sess_1")
        if budget.exceeded:
            raise RuntimeError("Token budget exceeded")
    """

    def __init__(self, default_limit: int = 10000) -> None:
        """Initialize the token tracker.

        Args:
            default_limit: Default token budget per session.
        """
        self.default_limit = default_limit
        self._session_limits: dict[str, int] = {}
        self._session_usage: dict[str, int] = {}
        self._agent_usage: dict[str, int] = {}
        self._total_tokens: int = 0
        self._call_count: int = 0

    def set_session_limit(self, session_id: str, limit: int) -> None:
        """Set a custom token limit for a session."""
        self._session_limits[session_id] = limit

    def get_budget(self, session_id: str) -> TokenBudget:
        """Get the token budget for a session."""
        limit = self._session_limits.get(session_id, self.default_limit)
        used = self._session_usage.get(session_id, 0)
        return TokenBudget(limit=limit, used=used)

    def record(
        self,
        session_id: str,
        agent_name: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> int:
        """Record token usage for a session/agent call.

        Args:
            session_id: Session identifier.
            agent_name: Agent that made the call.
            prompt_tokens: Tokens in the prompt.
            completion_tokens: Tokens in the completion.

        Returns:
            Total tokens recorded for this call.
        """
        total = prompt_tokens + completion_tokens
        self._session_usage[session_id] = self._session_usage.get(session_id, 0) + total
        self._agent_usage[agent_name] = self._agent_usage.get(agent_name, 0) + total
        self._total_tokens += total
        self._call_count += 1
        return total

    def check_budget(self, session_id: str, estimated_tokens: int) -> bool:
        """Check if a call would exceed the session's budget.

        Args:
            session_id: Session to check.
            estimated_tokens: Tokens the call is expected to consume.

        Returns:
            True if the call is within budget, False if it would exceed.
        """
        budget = self.get_budget(session_id)
        return budget.used + estimated_tokens <= budget.limit

    def stats(self) -> dict[str, Any]:
        """Return overall token usage statistics."""
        return {
            "total_tokens": self._total_tokens,
            "total_calls": self._call_count,
            "avg_tokens_per_call": (
                self._total_tokens / self._call_count if self._call_count > 0 else 0
            ),
            "session_count": len(self._session_usage),
            "agent_count": len(self._agent_usage),
            "top_agents": dict(
                sorted(self._agent_usage.items(), key=lambda x: x[1], reverse=True)[:5]
            ),
        }

    def session_stats(self, session_id: str) -> dict[str, Any]:
        """Return token usage for a specific session."""
        budget = self.get_budget(session_id)
        return {
            "session_id": session_id,
            "limit": budget.limit,
            "used": budget.used,
            "remaining": budget.remaining,
            "utilization": round(budget.utilization, 4),
            "exceeded": budget.exceeded,
        }

    def reset(self) -> None:
        """Reset all tracking data."""
        self._session_usage.clear()
        self._agent_usage.clear()
        self._total_tokens = 0
        self._call_count = 0


# ── Semantic Cache ─────────────────────────────────────────────────────────


def _text_fingerprint(text: str, ngram_size: int = 3) -> set[str]:
    """Generate a set of character n-grams from text for similarity comparison.

    Normalizes text to lowercase, strips punctuation, and extracts character
    n-grams. These n-gram sets can be compared using Jaccard similarity to
    determine if two texts are semantically similar.

    Args:
        text: Input text to fingerprint.
        ngram_size: Size of character n-grams (default: 3).

    Returns:
        Set of n-gram strings.
    """
    import re

    # Normalize: lowercase, collapse whitespace, strip non-alphanumeric
    normalized = re.sub(r"[^a-z0-9\s]", "", text.lower().strip())
    normalized = re.sub(r"\s+", " ", normalized)

    if len(normalized) < ngram_size:
        return {normalized} if normalized else set()

    return {normalized[i : i + ngram_size] for i in range(len(normalized) - ngram_size + 1)}


def _jaccard_similarity(set_a: set[str], set_b: set[str]) -> float:
    """Compute Jaccard similarity between two sets.

    Returns:
        Similarity score from 0.0 (no overlap) to 1.0 (identical).
    """
    if not set_a and not set_b:
        return 1.0
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


@dataclass
class SemanticCacheEntry:
    """A semantic cache entry with fingerprint for similarity matching.

    Attributes:
        fingerprint: Set of n-grams for similarity comparison.
        query: Original query text.
        response: Cached LLM response.
        model: Model that produced the response.
        tokens_saved: Estimated tokens saved by cache hits.
        created_at: Timestamp when entry was created.
        hit_count: Number of times this entry has been served.
        avg_similarity: Average similarity score of cache hits.
    """

    fingerprint: set[str]
    query: str
    response: str
    model: str
    tokens_saved: int = 0
    created_at: float = field(default_factory=time.time)
    hit_count: int = 0
    avg_similarity: float = 0.0


class SemanticCache:
    """Semantic cache for LLM responses using text similarity matching.

    Unlike ResponseCache (exact hash match), SemanticCache finds cached
    responses for *similar* queries using character n-gram fingerprinting
    and Jaccard similarity. This catches rephrasings, typos, and near-duplicate
    questions that would miss an exact-match cache.

    Inspired by Symbio's semantic caching pattern:
        - Similar questions hit cache and return zero-token
        - Configurable similarity threshold (0.0-1.0)
        - TTL-based expiration + LRU eviction
        - Stats tracking for hit rate and token savings

    Usage::

        cache = SemanticCache(threshold=0.7, max_size=200)
        result = cache.lookup("What is the capital of France?")
        if result is None:
            result = call_llm(...)
            cache.store("What is the capital of France?", result, model="gpt-4")
        # Later, a similar query will hit the cache:
        result = cache.lookup("what is the capital of france")  # cache hit!
    """

    def __init__(
        self,
        threshold: float = 0.6,
        max_size: int = 200,
        ttl_seconds: float = 7200.0,
        ngram_size: int = 3,
    ) -> None:
        """Initialize the semantic cache.

        Args:
            threshold: Minimum Jaccard similarity to consider a match (0.0-1.0).
            max_size: Maximum number of entries (LRU eviction when exceeded).
            ttl_seconds: Time-to-live for cache entries in seconds.
            ngram_size: Size of character n-grams for fingerprinting.
        """
        self.threshold = threshold
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self.ngram_size = ngram_size
        self._entries: list[SemanticCacheEntry] = []
        self._total_hits = 0
        self._total_misses = 0
        self._total_tokens_saved = 0

    def _cleanup_expired(self) -> None:
        """Remove expired entries in-place."""
        now = time.time()
        self._entries = [
            e for e in self._entries
            if now - e.created_at <= self.ttl_seconds
        ]

    def _evict_lru(self) -> None:
        """Evict least-recently-used entries until under max_size."""
        while len(self._entries) >= self.max_size:
            # Remove entry with lowest hit_count (least useful)
            min_idx = min(
                range(len(self._entries)),
                key=lambda i: self._entries[i].hit_count,
            )
            self._entries.pop(min_idx)

    def lookup(self, query: str, model: str | None = None) -> str | None:
        """Find a cached response for a semantically similar query.

        Args:
            query: The user's query text.
            model: If provided, only match entries from this model.

        Returns:
            Cached response string if a similar entry is found, else None.
        """
        self._cleanup_expired()
        query_fp = _text_fingerprint(query, self.ngram_size)

        best_entry: SemanticCacheEntry | None = None
        best_similarity = 0.0

        for entry in self._entries:
            # Filter by model if specified
            if model and entry.model != model:
                continue

            similarity = _jaccard_similarity(query_fp, entry.fingerprint)
            if similarity >= self.threshold and similarity > best_similarity:
                best_similarity = similarity
                best_entry = entry

        if best_entry is not None:
            best_entry.hit_count += 1
            # Running average of hit similarities
            best_entry.avg_similarity = (
                (best_entry.avg_similarity * (best_entry.hit_count - 1) + best_similarity)
                / best_entry.hit_count
            )
            self._total_hits += 1
            self._total_tokens_saved += best_entry.tokens_saved
            return best_entry.response

        self._total_misses += 1
        return None

    def store(
        self,
        query: str,
        response: str,
        model: str = "unknown",
        tokens_saved: int = 0,
    ) -> None:
        """Store a response in the semantic cache.

        Args:
            query: The original query text.
            response: The LLM response to cache.
            model: The model that produced the response.
            tokens_saved: Estimated tokens saved by future cache hits.
        """
        self._cleanup_expired()
        self._evict_lru()

        entry = SemanticCacheEntry(
            fingerprint=_text_fingerprint(query, self.ngram_size),
            query=query,
            response=response,
            model=model,
            tokens_saved=tokens_saved,
        )
        self._entries.append(entry)

    def find_similar(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """Find the top-k most similar cached entries to a query.

        Useful for debugging cache behavior and understanding similarity scores.

        Args:
            query: The query text to compare against.
            top_k: Number of results to return.

        Returns:
            List of dicts with 'query', 'similarity', 'hit_count', 'model'.
        """
        query_fp = _text_fingerprint(query, self.ngram_size)
        scored: list[tuple[float, SemanticCacheEntry]] = []

        for entry in self._entries:
            sim = _jaccard_similarity(query_fp, entry.fingerprint)
            if sim > 0:
                scored.append((sim, entry))

        scored.sort(key=lambda x: x[0], reverse=True)

        return [
            {
                "query": entry.query,
                "similarity": round(sim, 4),
                "hit_count": entry.hit_count,
                "model": entry.model,
                "tokens_saved": entry.tokens_saved,
            }
            for sim, entry in scored[:top_k]
        ]

    def clear(self) -> None:
        """Clear all cache entries."""
        self._entries.clear()
        self._total_hits = 0
        self._total_misses = 0
        self._total_tokens_saved = 0

    @property
    def size(self) -> int:
        """Current number of entries in the cache."""
        return len(self._entries)

    @property
    def hit_rate(self) -> float:
        """Cache hit rate (0.0 to 1.0)."""
        total = self._total_hits + self._total_misses
        return self._total_hits / total if total > 0 else 0.0

    def stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        return {
            "size": self.size,
            "max_size": self.max_size,
            "threshold": self.threshold,
            "total_hits": self._total_hits,
            "total_misses": self._total_misses,
            "hit_rate": round(self.hit_rate, 4),
            "tokens_saved": self._total_tokens_saved,
            "avg_hit_similarity": (
                round(
                    sum(e.avg_similarity * e.hit_count for e in self._entries)
                    / max(self._total_hits, 1),
                    4,
                )
                if self._total_hits > 0
                else 0.0
            ),
        }
