"""Tests for LilithEngine response caching and token tracking.

Validates the Talon-style response cache integration: identical prompts
hit the LRU cache instead of the LLM provider, cache stats are exposed
via get_stats(), and token tracking records usage per session.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from lilith_orchestrator.engine import LilithEngine


@pytest.fixture
def mock_config():
    """Mock config with cache-friendly defaults."""
    cfg = MagicMock()
    cfg.model = "test-model"
    cfg.base_url = "http://localhost:1234/v1"
    cfg.api_key = "test-key"
    cfg.max_tokens = 100
    cfg.temperature = 0.7
    cfg.system_prompt = "You are a test assistant."
    cfg.cache_size = 64
    cfg.cache_ttl_seconds = 600.0
    cfg.token_budget = 50_000
    return cfg


@pytest.fixture
def engine(mock_config):
    """Engine with cache enabled."""
    eng = LilithEngine(mock_config, memory=None)
    eng.enable_cache()
    return eng


@pytest.fixture
def disabled_engine(mock_config):
    """Engine with cache disabled (default)."""
    return LilithEngine(mock_config, memory=None)


# ── Cache control ────────────────────────────────────────────────────────────


class TestCacheControl:
    """Tests for enable_cache / disable_cache / clear_cache."""

    def test_cache_disabled_by_default(self, disabled_engine):
        """Newly constructed engines have caching disabled."""
        assert disabled_engine._cache_enabled is False

    def test_enable_cache(self, disabled_engine):
        """enable_cache() turns on caching."""
        assert disabled_engine._cache_enabled is False
        disabled_engine.enable_cache()
        assert disabled_engine._cache_enabled is True

    def test_disable_cache(self, engine):
        """disable_cache() turns off caching but keeps entries."""
        engine._cache_enabled is True  # already enabled
        engine.disable_cache()
        assert engine._cache_enabled is False

    def test_clear_cache_resets_counters(self, engine):
        """clear_cache() resets hits/misses counters."""
        engine._cache_hits = 5
        engine._cache_misses = 3
        engine.clear_cache()
        assert engine._cache_hits == 0
        assert engine._cache_misses == 0


# ── Cache integration with fallback ──────────────────────────────────────────


class TestCacheFallback:
    """Tests that cache is consulted before LLM call in _process_llm_fallback."""

    def test_cache_hit_skips_llm_call(self, engine, monkeypatch):
        """When the cache has the prompt, _get_llm_client is never called."""
        # Pre-populate cache for a known prompt
        model = engine.config.model
        params = {"temperature": engine.config.temperature, "max_tokens": engine.config.max_tokens}
        from lilith_core.token_optimizer import ResponseCache

        key = ResponseCache.make_key(model, "Hello, world!", params)
        engine._response_cache.put(key, "Cached answer.", tokens_saved=10)

        # Spy on _get_llm_client
        call_log = []
        original_get_client = engine._get_llm_client

        def spy_get_client():
            call_log.append("called")
            return original_get_client()

        monkeypatch.setattr(engine, "_get_llm_client", spy_get_client)

        result = engine._process_llm_fallback("Hello, world!", {}, session_id="s1")

        # LLM client was NOT called — cache served the response
        assert call_log == []
        assert result["response"] == "Cached answer."
        assert result["_cached"] is True
        assert result["usage"].agents_used == ["llm_fallback_cache"]

    def test_cache_miss_calls_llm(self, engine, monkeypatch):
        """On cache miss, _get_llm_client is invoked and response is cached."""
        call_log = []
        monkeypatch.setattr(engine, "_get_llm_client", lambda: call_log.append("called") or None)

        result = engine._process_llm_fallback("Unique prompt", {}, session_id="s2")

        # LLM client was called but returned None → friendly error fallback
        assert call_log == ["called"]
        # No cache hit because cache was empty
        assert result.get("_cached") is not True

    def test_cache_disabled_no_hit(self, disabled_engine, monkeypatch):
        """When cache is disabled, no lookup happens even for same prompt twice."""
        # Pre-populate the underlying cache (shouldn't matter when disabled)
        from lilith_core.token_optimizer import ResponseCache

        key = ResponseCache.make_key(
            disabled_engine.config.model, "test",
            {"temperature": 0.7, "max_tokens": 100},
        )
        disabled_engine._response_cache.put(key, "Cached.")

        call_log = []
        monkeypatch.setattr(
            disabled_engine, "_get_llm_client", lambda: call_log.append("called") or None,
        )

        result = disabled_engine._process_llm_fallback("test", {}, session_id="s3")
        # _get_llm_client called (cache was disabled)
        assert call_log == ["called"]
        # Response is the friendly fallback, not the cached value
        assert "no pude procesar" in result["response"].lower()


# ── Token tracking ───────────────────────────────────────────────────────────


class TestTokenTracking:
    """Tests for per-session token tracking in the engine."""

    def test_token_tracker_initialized(self, engine):
        """Engine exposes a TokenTracker via _token_tracker."""
        assert engine._token_tracker is not None
        assert engine._token_tracker.default_limit == 50_000

    def test_token_tracker_records_on_successful_call(self, engine, monkeypatch):
        """A successful LLM call records tokens for the session."""
        # Mock _call_llm to return a known response
        monkeypatch.setattr(engine, "_call_llm", lambda *a, **kw: "x" * 200)
        monkeypatch.setattr(engine, "_get_llm_client", lambda: object())

        engine._process_llm_fallback("prompt that is reasonably long " * 5, {}, session_id="sess-A")

        budget = engine._token_tracker.get_budget("sess-A")
        assert budget.used > 0

    def test_token_tracker_no_session_id(self, engine, monkeypatch):
        """Token tracking is a no-op when session_id is empty."""
        monkeypatch.setattr(engine, "_call_llm", lambda *a, **kw: "response text")
        monkeypatch.setattr(engine, "_get_llm_client", lambda: object())

        # Should not raise
        result = engine._process_llm_fallback("test", {}, session_id="")
        assert "response" in result


# ── get_stats() integration ──────────────────────────────────────────────────


class TestStatsWithCache:
    """Tests that get_stats() exposes cache metrics."""

    def test_stats_includes_cache_section(self, engine):
        """get_stats() includes a 'cache' dict with hits/misses/etc."""
        stats = engine.get_stats()
        assert "cache" in stats
        cache_stats = stats["cache"]
        assert cache_stats["enabled"] is True
        assert cache_stats["hits"] == 0
        assert cache_stats["misses"] == 0
        assert cache_stats["size"] == 0
        assert cache_stats["hit_rate"] == 0.0

    def test_stats_reflects_cache_activity(self, engine, monkeypatch):
        """get_stats() reflects hits and misses from actual processing."""
        # Pre-populate cache for one prompt
        from lilith_core.token_optimizer import ResponseCache

        params = {"temperature": 0.7, "max_tokens": 100}
        key = ResponseCache.make_key(engine.config.model, "cached_prompt", params)
        engine._response_cache.put(key, "cached response")

        # First call: cache hit
        monkeypatch.setattr(engine, "_get_llm_client", lambda: None)
        engine._process_llm_fallback("cached_prompt", {}, session_id="s1")

        # Second call: cache miss
        engine._process_llm_fallback("different_prompt", {}, session_id="s2")

        stats = engine.get_stats()
        cache_stats = stats["cache"]
        assert cache_stats["hits"] == 1
        assert cache_stats["misses"] == 1
        assert cache_stats["hit_rate"] == 0.5

    def test_reset_stats_clears_cache_counters(self, engine):
        """reset_stats() resets hits/misses counters."""
        engine._cache_hits = 10
        engine._cache_misses = 7
        engine.reset_stats()
        assert engine._cache_hits == 0
        assert engine._cache_misses == 0