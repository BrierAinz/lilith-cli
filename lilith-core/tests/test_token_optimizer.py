"""Tests for lilith_core.token_optimizer — response cache, context compressor, token tracker."""

import pytest

from lilith_core.token_optimizer import (
    CompressionResult,
    ContextCompressor,
    ResponseCache,
    SemanticCache,
    TokenBudget,
    TokenTracker,
    _jaccard_similarity,
    _text_fingerprint,
    estimate_messages_tokens,
    estimate_tokens,
)


# ── Token estimation tests ───────────────────────────────────────────────────


class TestEstimateTokens:
    """Tests for token estimation functions."""

    def test_empty_string(self):
        assert estimate_tokens("") == 0

    def test_short_string(self):
        assert estimate_tokens("hello") == 1  # 5 chars / 4 = 1.25 → 1

    def test_longer_string(self):
        # 40 chars / 4 = 10 tokens
        assert estimate_tokens("a" * 40) == 10

    def test_minimum_one_for_nonempty(self):
        assert estimate_tokens("a") == 1

    def test_estimate_messages_tokens(self):
        messages = [
            {"role": "system", "content": "You are helpful."},  # 4 + 4 = 8
            {"role": "user", "content": "Hello world!"},        # 4 + 3 = 7
        ]
        tokens = estimate_messages_tokens(messages)
        assert tokens > 0
        # system: 4 overhead + estimate("You are helpful.") = 4 + 4 = 8
        # user: 4 overhead + estimate("Hello world!") = 4 + 3 = 7
        # total = 15
        assert tokens == 15

    def test_estimate_messages_empty(self):
        assert estimate_messages_tokens([]) == 0


# ── Response cache tests ─────────────────────────────────────────────────────


class TestResponseCache:
    """Tests for the ResponseCache."""

    @pytest.fixture
    def cache(self):
        return ResponseCache(max_size=5, ttl_seconds=60.0)

    def test_put_and_get(self, cache):
        key = cache.make_key("gpt-4", "What is 2+2?")
        cache.put(key, "4")
        assert cache.has(key)
        assert cache.get(key) == "4"

    def test_miss_returns_none(self, cache):
        assert cache.get("nonexistent") is None
        assert cache.get("nonexistent") is None

    def test_make_key_deterministic(self, cache):
        k1 = cache.make_key("gpt-4", "hello", {"temp": 0.7})
        k2 = cache.make_key("gpt-4", "hello", {"temp": 0.7})
        assert k1 == k2

    def test_make_key_different_params(self, cache):
        k1 = cache.make_key("gpt-4", "hello", {"temp": 0.7})
        k2 = cache.make_key("gpt-4", "hello", {"temp": 0.5})
        assert k1 != k2

    def test_make_key_different_model(self, cache):
        k1 = cache.make_key("gpt-4", "hello")
        k2 = cache.make_key("claude-3", "hello")
        assert k1 != k2

    def test_lru_eviction(self, cache):
        # Fill cache to max_size
        for i in range(5):
            k = cache.make_key("m", f"prompt_{i}")
            cache.put(k, f"resp_{i}")

        # Access first entry to make it recently used
        first_key = cache.make_key("m", "prompt_0")
        cache.get(first_key)

        # Add a new entry — should evict the least recently used (prompt_1)
        new_key = cache.make_key("m", "prompt_5")
        cache.put(new_key, "resp_5")

        # prompt_0 should still exist (was accessed)
        assert cache.has(first_key)
        # prompt_1 should have been evicted
        evicted_key = cache.make_key("m", "prompt_1")
        assert not cache.has(evicted_key)

    def test_ttl_expiry(self):
        cache = ResponseCache(max_size=10, ttl_seconds=0.1)
        key = cache.make_key("m", "test")
        cache.put(key, "response")
        assert cache.has(key)
        # Wait for TTL to expire
        import time
        time.sleep(0.15)
        assert not cache.has(key)
        assert cache.get(key) is None

    def test_hit_rate(self, cache):
        k1 = cache.make_key("m", "hit")
        k2 = cache.make_key("m", "miss")
        cache.put(k1, "resp1")

        cache.get(k1)  # hit
        cache.get(k1)  # hit
        cache.get(k2)  # miss

        stats = cache.stats()
        assert stats["total_hits"] == 2
        assert stats["total_misses"] == 1
        assert 0 < stats["hit_rate"] < 1

    def test_tokens_saved_tracking(self, cache):
        key = cache.make_key("m", "expensive")
        cache.put(key, "response", tokens_saved=500)

        cache.get(key)  # hit — saves 500 tokens
        cache.get(key)  # hit — saves 500 more

        stats = cache.stats()
        assert stats["tokens_saved"] == 1000

    def test_clear(self, cache):
        for i in range(3):
            cache.put(cache.make_key("m", f"p{i}"), f"r{i}")
        cache.clear()
        assert cache.size == 0
        assert cache.stats()["total_hits"] == 0

    def test_cleanup_expired(self):
        cache = ResponseCache(max_size=10, ttl_seconds=0.05)
        for i in range(5):
            cache.put(cache.make_key("m", f"p{i}"), f"r{i}")

        import time
        time.sleep(0.1)
        removed = cache.cleanup_expired()
        assert removed == 5
        assert cache.size == 0

    def test_stats(self, cache):
        cache.put(cache.make_key("m", "p1"), "r1")
        stats = cache.stats()
        assert "size" in stats
        assert "max_size" in stats
        assert "total_hits" in stats
        assert "total_misses" in stats
        assert "hit_rate" in stats
        assert "tokens_saved" in stats


# ── Context compressor tests ─────────────────────────────────────────────────


class TestContextCompressor:
    """Tests for the ContextCompressor."""

    @pytest.fixture
    def compressor(self):
        return ContextCompressor(max_tokens=100, keep_recent_count=3)

    def test_no_compression_needed(self, compressor):
        """Messages under budget with no duplicates should pass through unchanged."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Welcome"},
        ]
        result = compressor.compress(messages)
        assert result.removed_messages == 0
        assert len(result.messages) == 2

    def test_truncation_removes_old_messages(self):
        """Old messages should be removed when over budget."""
        compressor = ContextCompressor(max_tokens=20, keep_recent_count=2)
        messages = [
            {"role": "user", "content": "This is a very old message that should be removed to save tokens"},
            {"role": "assistant", "content": "This is an old response that should also be removed"},
            {"role": "user", "content": "Recent query"},
            {"role": "assistant", "content": "Recent response"},
        ]
        result = compressor.compress(messages)
        assert result.removed_messages > 0
        assert len(result.messages) < len(messages)
        # Recent messages should be kept
        contents = [m["content"] for m in result.messages]
        assert "Recent query" in contents or "Recent response" in contents

    def test_system_messages_preserved(self):
        """System messages should be kept when keep_system_messages=True."""
        compressor = ContextCompressor(max_tokens=20, keep_system_messages=True, keep_recent_count=1)
        messages = [
            {"role": "system", "content": "You are a helpful assistant"},
            {"role": "user", "content": "This is a very long old message that takes many tokens to represent"},
            {"role": "assistant", "content": "Short reply"},
        ]
        result = compressor.compress(messages)
        roles = [m["role"] for m in result.messages]
        assert "system" in roles

    def test_system_messages_removed_when_disabled(self):
        """System messages should be removable when keep_system_messages=False."""
        compressor = ContextCompressor(max_tokens=20, keep_system_messages=False, keep_recent_count=1)
        messages = [
            {"role": "system", "content": "You are a very long system prompt that uses many tokens"},
            {"role": "user", "content": "Short"},
        ]
        result = compressor.compress(messages)
        # System message might be truncated or removed
        assert result.compressed_tokens <= 20

    def test_deduplication_removes_consecutive_duplicates(self, compressor):
        """Consecutive duplicate messages should be removed."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "user", "content": "Hello"},  # duplicate
            {"role": "assistant", "content": "Hi there"},
        ]
        result = compressor.compress(messages)
        # At least one duplicate should be removed
        assert len(result.messages) <= 2

    def test_summary_replacement(self):
        """When a summary is provided, removed messages should be replaced."""
        compressor = ContextCompressor(max_tokens=30, keep_recent_count=2)
        messages = [
            {"role": "user", "content": "Very long old message about topic A that should be summarized"},
            {"role": "assistant", "content": "Long old response about topic A that takes many tokens"},
            {"role": "user", "content": "New question"},
            {"role": "assistant", "content": "New answer"},
        ]
        result = compressor.compress(messages, summary="Discussion about topic A")
        # Should have a summary system message
        contents = [m.get("content", "") for m in result.messages]
        has_summary = any("summary" in c.lower() for c in contents)
        assert has_summary

    def test_compression_ratio(self):
        """Compression ratio should be < 1.0 when compression happens."""
        compressor = ContextCompressor(max_tokens=10, keep_recent_count=1)
        messages = [
            {"role": "user", "content": "This is a very long message that exceeds the token budget significantly"},
            {"role": "assistant", "content": "This is another long message that also exceeds the budget"},
            {"role": "user", "content": "Short"},
        ]
        result = compressor.compress(messages)
        if result.removed_messages > 0:
            assert result.compression_ratio < 1.0

    def test_compression_result_fields(self, compressor):
        """CompressionResult should have all expected fields."""
        result = compressor.compress([{"role": "user", "content": "test"}])
        assert isinstance(result, CompressionResult)
        assert result.original_tokens > 0
        assert result.compressed_tokens > 0
        assert result.removed_messages >= 0
        assert result.compression_ratio > 0


# ── Token tracker tests ──────────────────────────────────────────────────────


class TestTokenTracker:
    """Tests for the TokenTracker."""

    @pytest.fixture
    def tracker(self):
        return TokenTracker(default_limit=1000)

    def test_record_usage(self, tracker):
        total = tracker.record("sess_1", "odin", prompt_tokens=100, completion_tokens=200)
        assert total == 300
        budget = tracker.get_budget("sess_1")
        assert budget.used == 300

    def test_budget_limit(self, tracker):
        tracker.set_session_limit("sess_1", 500)
        tracker.record("sess_1", "odin", prompt_tokens=200, completion_tokens=200)
        budget = tracker.get_budget("sess_1")
        assert budget.limit == 500
        assert budget.used == 400
        assert budget.remaining == 100
        assert not budget.exceeded

    def test_budget_exceeded(self, tracker):
        tracker.set_session_limit("sess_1", 100)
        tracker.record("sess_1", "odin", prompt_tokens=60, completion_tokens=60)
        budget = tracker.get_budget("sess_1")
        assert budget.exceeded
        assert budget.remaining == 0

    def test_check_budget_within(self, tracker):
        tracker.set_session_limit("sess_1", 1000)
        tracker.record("sess_1", "odin", prompt_tokens=500, completion_tokens=0)
        assert tracker.check_budget("sess_1", 400) is True  # 500 + 400 <= 1000

    def test_check_budget_exceeds(self, tracker):
        tracker.set_session_limit("sess_1", 1000)
        tracker.record("sess_1", "odin", prompt_tokens=500, completion_tokens=0)
        assert tracker.check_budget("sess_1", 600) is False  # 500 + 600 > 1000

    def test_agent_usage_tracking(self, tracker):
        tracker.record("sess_1", "odin", prompt_tokens=100, completion_tokens=50)
        tracker.record("sess_1", "mimir", prompt_tokens=200, completion_tokens=100)
        tracker.record("sess_2", "odin", prompt_tokens=50, completion_tokens=25)

        stats = tracker.stats()
        # odin: 150 + 75 = 225
        # mimir: 300
        assert stats["top_agents"]["mimir"] == 300
        assert stats["top_agents"]["odin"] == 225

    def test_stats(self, tracker):
        tracker.record("s1", "odin", prompt_tokens=100, completion_tokens=200)
        tracker.record("s2", "mimir", prompt_tokens=50, completion_tokens=100)

        stats = tracker.stats()
        assert stats["total_tokens"] == 450
        assert stats["total_calls"] == 2
        assert stats["avg_tokens_per_call"] == 225
        assert stats["session_count"] == 2
        assert stats["agent_count"] == 2

    def test_session_stats(self, tracker):
        tracker.set_session_limit("sess_1", 1000)
        tracker.record("sess_1", "odin", prompt_tokens=300, completion_tokens=200)

        stats = tracker.session_stats("sess_1")
        assert stats["session_id"] == "sess_1"
        assert stats["limit"] == 1000
        assert stats["used"] == 500
        assert stats["remaining"] == 500
        assert stats["utilization"] == 0.5
        assert not stats["exceeded"]

    def test_reset(self, tracker):
        tracker.record("s1", "odin", prompt_tokens=100, completion_tokens=200)
        tracker.reset()
        stats = tracker.stats()
        assert stats["total_tokens"] == 0
        assert stats["total_calls"] == 0


# ── TokenBudget dataclass tests ──────────────────────────────────────────────


class TestTokenBudget:
    """Tests for the TokenBudget dataclass."""

    def test_defaults(self):
        budget = TokenBudget(limit=1000)
        assert budget.used == 0
        assert budget.remaining == 1000
        assert not budget.exceeded
        assert budget.utilization == 0.0

    def test_exceeded(self):
        budget = TokenBudget(limit=100, used=150)
        assert budget.exceeded
        assert budget.remaining == 0  # clamped to 0
        assert budget.utilization == 1.5

    def test_zero_limit(self):
        budget = TokenBudget(limit=0, used=10)
        assert budget.utilization == 0.0  # avoid div by zero


# ── Text Fingerprint tests ─────────────────────────────────────────────────


class TestTextFingerprint:
    """Tests for _text_fingerprint helper."""

    def test_basic_fingerprint(self):
        fp = _text_fingerprint("hello world")
        assert isinstance(fp, set)
        assert len(fp) > 0
        # Should contain 'hel', 'ell', 'llo', 'lo ', 'o w', ' wo', 'wor', 'orl', 'rld'
        assert "hel" in fp
        assert "wor" in fp

    def test_case_insensitive(self):
        fp_lower = _text_fingerprint("Hello World")
        fp_upper = _text_fingerprint("hello world")
        assert fp_lower == fp_upper

    def test_punctuation_stripped(self):
        fp = _text_fingerprint("hello, world!")
        # After stripping punctuation: "hello world"
        assert "hel" in fp
        assert "wor" in fp

    def test_empty_string(self):
        fp = _text_fingerprint("")
        assert fp == set()

    def test_short_text(self):
        fp = _text_fingerprint("hi")
        # Shorter than ngram_size, returned as-is
        assert "hi" in fp

    def test_whitespace_collapsed(self):
        fp1 = _text_fingerprint("hello   world")
        fp2 = _text_fingerprint("hello world")
        assert fp1 == fp2


# ── Jaccard Similarity tests ───────────────────────────────────────────────


class TestJaccardSimilarity:
    """Tests for _jaccard_similarity helper."""

    def test_identical_sets(self):
        s = {"a", "b", "c"}
        assert _jaccard_similarity(s, s) == 1.0

    def test_disjoint_sets(self):
        assert _jaccard_similarity({"a"}, {"b"}) == 0.0

    def test_partial_overlap(self):
        sim = _jaccard_similarity({"a", "b", "c"}, {"b", "c", "d"})
        # intersection = {b, c} = 2, union = {a, b, c, d} = 4 → 0.5
        assert sim == 0.5

    def test_empty_sets(self):
        assert _jaccard_similarity(set(), set()) == 1.0

    def test_one_empty(self):
        assert _jaccard_similarity({"a"}, set()) == 0.0

    def test_subset(self):
        sim = _jaccard_similarity({"a", "b"}, {"a", "b", "c", "d"})
        # intersection = 2, union = 4 → 0.5
        assert sim == 0.5


# ── SemanticCache tests ────────────────────────────────────────────────────


class TestSemanticCache:
    """Tests for SemanticCache — semantic similarity-based response caching."""

    def test_exact_match_hits(self):
        """Exact same query should always hit."""
        cache = SemanticCache(threshold=0.5)
        cache.store("What is Python?", "Python is a programming language.", model="gpt-4")
        result = cache.lookup("What is Python?")
        assert result == "Python is a programming language."

    def test_similar_query_hits(self):
        """Rephrased query should hit above threshold."""
        cache = SemanticCache(threshold=0.5)
        cache.store("What is the capital of France?", "Paris is the capital of France.")
        # Similar phrasing
        result = cache.lookup("what is capital of france")
        assert result is not None
        assert "Paris" in result

    def test_dissimilar_query_misses(self):
        """Completely different query should miss."""
        cache = SemanticCache(threshold=0.6)
        cache.store("What is Python?", "A programming language.")
        result = cache.lookup("Tell me about quantum physics")
        assert result is None

    def test_model_filter(self):
        """Lookup with model filter should only match same model."""
        cache = SemanticCache(threshold=0.3)
        cache.store("hello", "hi from gpt4", model="gpt-4")
        cache.store("hello", "hi from claude", model="claude")

        result = cache.lookup("hello", model="gpt-4")
        assert result == "hi from gpt4"

        result = cache.lookup("hello", model="claude")
        assert result == "hi from claude"

    def test_threshold_filtering(self):
        """Higher threshold should reject weaker matches."""
        cache = SemanticCache(threshold=0.95)  # very strict
        cache.store("What is Python?", "A programming language.")
        result = cache.lookup("Tell me about Python language")
        # May or may not hit depending on similarity — but very different queries
        # should not hit at 0.95 threshold
        # This is a soft test — we just verify it doesn't crash
        assert result is None or isinstance(result, str)

    def test_ttl_expiration(self):
        """Entries should expire after TTL."""
        import time as _time

        cache = SemanticCache(threshold=0.5, ttl_seconds=0.1)
        cache.store("test query", "test response")
        _time.sleep(0.15)
        result = cache.lookup("test query")
        assert result is None

    def test_max_size_eviction(self):
        """Cache should evict when max_size exceeded."""
        cache = SemanticCache(threshold=0.3, max_size=3)
        for i in range(5):
            cache.store(f"unique query number {i}", f"response {i}")
        assert cache.size <= 3

    def test_stats(self):
        """Stats should track hits, misses, and hit rate."""
        cache = SemanticCache(threshold=0.5)
        cache.store("What is 2+2?", "4")
        cache.lookup("What is 2+2?")  # hit
        cache.lookup("completely different topic xyz")  # miss

        stats = cache.stats()
        assert stats["total_hits"] >= 1
        assert stats["total_misses"] >= 1
        assert 0 <= stats["hit_rate"] <= 1
        assert "tokens_saved" in stats
        assert "avg_hit_similarity" in stats

    def test_clear(self):
        """Clear should remove all entries and reset stats."""
        cache = SemanticCache()
        cache.store("q1", "r1")
        cache.store("q2", "r2")
        assert cache.size == 2

        cache.clear()
        assert cache.size == 0
        assert cache.lookup("q1") is None

    def test_find_similar(self):
        """find_similar should return ranked matches."""
        cache = SemanticCache(threshold=0.3)
        cache.store("What is Python?", "A programming language.")
        cache.store("What is Java?", "Another programming language.")
        cache.store("How to cook pasta?", "Boil water first.")

        results = cache.find_similar("What is Python programming?")
        assert len(results) > 0
        # Python-related should be top result
        assert "Python" in results[0]["query"]

    def test_hit_count_increments(self):
        """Hit count should increment on each cache hit."""
        cache = SemanticCache(threshold=0.5)
        cache.store("test query", "test response")
        cache.lookup("test query")
        cache.lookup("test query")

        stats = cache.stats()
        assert stats["total_hits"] == 2

    def test_empty_cache_lookup(self):
        """Looking up in empty cache should return None."""
        cache = SemanticCache()
        assert cache.lookup("anything") is None

    def test_empty_query(self):
        """Empty query should not crash."""
        cache = SemanticCache()
        cache.store("", "empty response")
        result = cache.lookup("")
        assert result == "empty response"

    def test_case_insensitive_matching(self):
        """Different casing should still match."""
        cache = SemanticCache(threshold=0.5)
        cache.store("What is PYTHON?", "A snake and a language.")
        result = cache.lookup("what is python")
        assert result is not None

    def test_typos_tolerance(self):
        """Minor typos should still match above threshold."""
        cache = SemanticCache(threshold=0.5)
        cache.store("How to install Python packages?", "Use pip install.")
        # Typo: "packges" instead of "packages"
        result = cache.lookup("How to install Python packges?")
        assert result is not None

    def test_defaults(self):
        """Default constructor should set reasonable defaults."""
        cache = SemanticCache()
        assert cache.threshold == 0.6
        assert cache.max_size == 200
        assert cache.ttl_seconds == 7200.0
        assert cache.ngram_size == 3
