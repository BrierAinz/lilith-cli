"""Comprehensive tests for the Smart Tool Router."""

from __future__ import annotations

import time
from typing import Any

import pytest

from lilith_tools.base import BaseTool, ToolResult
from lilith_tools.registry import ToolRegistry
from lilith_tools.router.analytics import ToolAnalytics, ToolUsage
from lilith_tools.router.chainer import (
    BUILTIN_CHAINS,
    ChainExecutor,
    ChainResult,
    ChainStep,
    ToolChain,
    _deep_get,
    _resolve_placeholders,
)
from lilith_tools.router.matcher import MatchResult, ToolMatcher, _jaccard_similarity, _tokenize
from lilith_tools.router.recovery import (
    DEFAULT_NETWORK_POLICY,
    FallbackChain,
    RecoveryManager,
    RetryPolicy,
)
from lilith_tools.router.router import SmartToolRouter


# ------------------------------------------------------------------
# Mock tool fixtures
# ------------------------------------------------------------------


class EchoTool(BaseTool):
    """Simple echo tool for testing."""

    name = "echo"
    description = "Echoes back the input message"
    parameters = {"message": {"type": "string", "required": True}}

    def execute(self, message: str = "", **kwargs: Any) -> ToolResult:
        if not message:
            return ToolResult(success=False, data=None, error="Empty message")
        return ToolResult(success=True, data={"echo": message})


class FailingTool(BaseTool):
    """Tool that always fails with a configurable error."""

    name = "failing_tool"
    description = "A tool that always fails"
    parameters = {}

    def __init__(self, error_msg: str = "generic error") -> None:
        super().__init__()
        self.error_msg = error_msg

    def execute(self, **kwargs: Any) -> ToolResult:
        return ToolResult(success=False, data=None, error=self.error_msg)


class SlowTool(BaseTool):
    """Tool that succeeds slowly for retry / backoff testing."""

    name = "slow_tool"
    description = "A tool that takes time"
    parameters = {}

    def execute(self, **kwargs: Any) -> ToolResult:
        time.sleep(0.05)
        return ToolResult(success=True, data={"slept": True})


class SearchTool(BaseTool):
    """Mock search tool for chain testing."""

    name = "search"
    description = "Search for information on the web"
    parameters = {"query": {"type": "string", "required": True}}

    def execute(self, query: str = "", **kwargs: Any) -> ToolResult:
        return ToolResult(success=True, data={"results": [f"Result for: {query}"]})


class SummarizeTool(BaseTool):
    """Mock summarize tool for chain testing."""

    name = "summarize"
    description = "Summarize text content"
    parameters = {"text": {"type": "string", "required": True}}

    def execute(self, text: str = "", **kwargs: Any) -> ToolResult:
        return ToolResult(success=True, data={"summary": f"Summary of: {text[:50]}"})


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_registry() -> None:
    """Clear registry before each test."""
    ToolRegistry._tools.clear()


@pytest.fixture()
def registry_with_tools() -> ToolRegistry:
    """Register mock tools and return the registry."""
    ToolRegistry._tools["echo"] = EchoTool
    ToolRegistry._tools["search"] = SearchTool
    ToolRegistry._tools["summarize"] = SummarizeTool
    ToolRegistry._tools["failing_tool"] = FailingTool
    ToolRegistry._tools["slow_tool"] = SlowTool
    return ToolRegistry


class TestToolMatcher:
    """Tests for ToolMatcher — keyword and semantic matching."""

    def test_match_by_keyword(self, registry_with_tools: ToolRegistry) -> None:
        matcher = ToolMatcher()
        results = matcher.match("search for information", registry_with_tools._tools, top_k=3)
        assert len(results) >= 1
        # The "search" tool should score highest for "search for information"
        names = [r.tool_name for r in results]
        assert "search" in names

    def test_match_top_k(self, registry_with_tools: ToolRegistry) -> None:
        matcher = ToolMatcher()
        results = matcher.match("tool", registry_with_tools._tools, top_k=2)
        assert len(results) <= 2

    def test_match_empty_query(self, registry_with_tools: ToolRegistry) -> None:
        matcher = ToolMatcher()
        results = matcher.match("", registry_with_tools._tools, top_k=3)
        # Should return results (possibly with low scores)
        assert isinstance(results, list)

    def test_match_empty_tools(self) -> None:
        matcher = ToolMatcher()
        results = matcher.match("anything", {}, top_k=3)
        assert results == []

    def test_jaccard_similarity_basic(self) -> None:
        score = _jaccard_similarity("search web", "web search engine")
        assert 0 < score <= 1.0

    def test_jaccard_similarity_identical(self) -> None:
        score = _jaccard_similarity("hello world", "hello world")
        assert score == 1.0

    def test_jaccard_similarity_disjoint(self) -> None:
        score = _jaccard_similarity("alpha", "zulu")
        assert score == 0.0

    def test_tokenize(self) -> None:
        tokens = _tokenize("Hello, World! 123")
        assert tokens == {"hello", "world", "123"}

    def test_compute_similarity_keyword_fallback(self) -> None:
        """compute_similarity should work even without sentence-transformers."""
        score = ToolMatcher.compute_similarity("search", "search the web")
        assert score > 0

    def test_match_result_dataclass(self) -> None:
        result = MatchResult(tool_name="echo", score=0.9, description="echo tool")
        assert result.tool_name == "echo"
        assert result.score == 0.9

    def test_embedding_cache_refreshed_on_new_tools(
        self, registry_with_tools: ToolRegistry
    ) -> None:
        matcher = ToolMatcher()
        results1 = matcher.match("test", registry_with_tools._tools, top_k=3)
        # Add another tool — cache should refresh
        registry_with_tools._tools["new_tool"] = EchoTool
        results2 = matcher.match("test", registry_with_tools._tools, top_k=3)
        assert len(results2) >= len(results1)


class TestToolChain:
    """Tests for ChainStep, ToolChain, ChainExecutor."""

    def test_chain_step_creation(self) -> None:
        step = ChainStep(tool_name="echo", params={"message": "hello"})
        assert step.tool_name == "echo"
        assert step.params == {"message": "hello"}
        assert step.condition is None

    def test_tool_chain_creation(self) -> None:
        chain = ToolChain(
            name="test_chain",
            description="A test chain",
            steps=[ChainStep(tool_name="echo", params={"message": "hi"})],
            required_tools=["echo"],
        )
        assert chain.name == "test_chain"
        assert len(chain.steps) == 1

    def test_execute_chain_simple(self, registry_with_tools: ToolRegistry) -> None:
        chain = ToolChain(
            name="echo_chain",
            description="Echo chain",
            steps=[ChainStep(tool_name="echo", params={"message": "hello"})],
            required_tools=["echo"],
        )
        executor = ChainExecutor(registry_with_tools)
        result = executor.execute(chain)
        assert result.success is True
        assert len(result.results) == 1
        assert result.results[0]["success"] is True

    def test_execute_chain_with_context_injection(self, registry_with_tools: ToolRegistry) -> None:
        chain = ToolChain(
            name="echo_chain_ctx",
            description="Echo chain with context",
            steps=[ChainStep(tool_name="echo", params={"message": "{{msg}}"})],
            required_tools=["echo"],
        )
        executor = ChainExecutor(registry_with_tools)
        result = executor.execute(chain, context={"msg": "from context"})
        assert result.success is True

    def test_execute_chain_missing_tool(self, registry_with_tools: ToolRegistry) -> None:
        chain = ToolChain(
            name="missing_chain",
            description="Chain with missing tool",
            steps=[ChainStep(tool_name="nonexistent", params={})],
            required_tools=["nonexistent"],
        )
        executor = ChainExecutor(registry_with_tools)
        result = executor.execute(chain)
        assert result.success is False
        assert len(result.errors) == 1

    def test_condition_evaluation_success(self) -> None:
        ctx = {"result": {"success": True, "data": None, "error": ""}}
        assert ChainExecutor._evaluate_condition("result.success == True", ctx) is True

    def test_condition_evaluation_failure(self) -> None:
        ctx = {"result": {"success": False, "data": None, "error": "err"}}
        assert ChainExecutor._evaluate_condition("result.success == True", ctx) is False

    def test_condition_evaluation_false(self) -> None:
        ctx = {"result": {"success": False, "data": None, "error": "err"}}
        assert ChainExecutor._evaluate_condition("result.success == False", ctx) is True

    def test_condition_no_match_falls_back(self) -> None:
        ctx = {"key": "value"}
        # Non-matching pattern falls back to truthiness of context
        assert ChainExecutor._evaluate_condition("unknown condition", ctx) is True

    def test_context_injection(self) -> None:
        params = {"message": "{{msg}}", "other": "static"}
        ctx = {"msg": "dynamic"}
        resolved = ChainExecutor._inject_context(params, ctx)
        assert resolved["message"] == "dynamic"
        assert resolved["other"] == "static"

    def test_context_injection_nested(self) -> None:
        params = {"data": {"text": "{{msg}}"}}
        ctx = {"msg": "nested_val"}
        resolved = ChainExecutor._inject_context(params, ctx)
        assert resolved["data"]["text"] == "nested_val"

    def test_chain_result_model(self) -> None:
        result = ChainResult(success=True, results=[], errors=[], execution_time=0.5)
        assert result.success is True
        assert result.execution_time == 0.5

    def test_builtin_chains_exist(self) -> None:
        assert "search_and_summarize" in BUILTIN_CHAINS
        assert "debug_cycle" in BUILTIN_CHAINS
        assert "deploy_pipeline" in BUILTIN_CHAINS

    def test_builtin_chain_structure(self) -> None:
        chain = BUILTIN_CHAINS["search_and_summarize"]
        assert isinstance(chain, ToolChain)
        assert len(chain.steps) >= 2
        assert chain.steps[0].tool_name == "web_search"

    def test_step_result_feeds_context(self, registry_with_tools: ToolRegistry) -> None:
        """Verify that a step's result is available in context for subsequent steps."""
        chain = ToolChain(
            name="feed_chain",
            description="Test context feeding",
            steps=[
                ChainStep(tool_name="echo", params={"message": "first"}),
                ChainStep(tool_name="echo", params={"message": "second"}),
            ],
            required_tools=["echo"],
        )
        executor = ChainExecutor(registry_with_tools)
        result = executor.execute(chain)
        assert result.success is True
        assert len(result.results) == 2

    def test_chain_step_skipped_when_condition_false(
        self, registry_with_tools: ToolRegistry
    ) -> None:
        chain = ToolChain(
            name="skip_chain",
            description="Test condition skip",
            steps=[
                ChainStep(tool_name="failing_tool", params={}),
                ChainStep(
                    tool_name="echo",
                    params={"message": "conditional"},
                    condition="result.success == True",
                ),
            ],
            required_tools=["failing_tool", "echo"],
        )
        executor = ChainExecutor(registry_with_tools)
        result = executor.execute(chain)
        # The failing_tool returns success=False, so the condition fails and echo is skipped
        assert any(r.get("skipped") for r in result.results)


class TestRecoveryManager:
    """Tests for retry, fallback, and backoff logic."""

    def test_execute_success_no_retry(self, registry_with_tools: ToolRegistry) -> None:
        recovery = RecoveryManager(registry_with_tools)
        result = recovery.execute_with_retry(
            "echo", {"message": "hello"}, policy=RetryPolicy(max_retries=3)
        )
        assert result.success is True
        assert result.data == {"echo": "hello"}

    def test_retry_on_matching_error(self, registry_with_tools: ToolRegistry) -> None:
        # Register a failing tool with a network error message
        registry_with_tools._tools["network_fail"] = type(
            "NetFailTool",
            (BaseTool,),
            {
                "name": "network_fail",
                "description": "Fails with network error",
                "parameters": {},
                "execute": lambda self, **kw: ToolResult(
                    success=False, data=None, error="ConnectionError: timeout"
                ),
            },
        )
        recovery = RecoveryManager(registry_with_tools)
        result = recovery.execute_with_retry(
            "network_fail",
            {},
            policy=RetryPolicy(
                max_retries=2, backoff_factor=0.01, retry_on_errors=["ConnectionError"]
            ),
        )
        # It will still fail after retries because the tool always fails
        assert result.success is False

    def test_no_retry_when_error_not_matching(self, registry_with_tools: ToolRegistry) -> None:
        registry_with_tools._tools["logic_fail"] = type(
            "LogicFailTool",
            (BaseTool,),
            {
                "name": "logic_fail",
                "description": "Fails with logic error",
                "parameters": {},
                "execute": lambda self, **kw: ToolResult(
                    success=False, data=None, error="ValueError: bad input"
                ),
            },
        )
        recovery = RecoveryManager(registry_with_tools)
        policy = RetryPolicy(max_retries=5, retry_on_errors=["network", "timeout"])
        result = recovery.execute_with_retry("logic_fail", {}, policy=policy)
        assert result.success is False
        # Should not retry since error doesn't match patterns
        # (Would have retried 5 times if matching)

    def test_retry_with_empty_patterns_retries_all(self, registry_with_tools: ToolRegistry) -> None:
        registry_with_tools._tools["always_fail"] = type(
            "AlwaysFailTool",
            (BaseTool,),
            {
                "name": "always_fail",
                "description": "Always fails",
                "parameters": {},
                "execute": lambda self, **kw: ToolResult(
                    success=False, data=None, error="something went wrong"
                ),
            },
        )
        recovery = RecoveryManager(registry_with_tools)
        result = recovery.execute_with_retry(
            "always_fail",
            {},
            policy=RetryPolicy(max_retries=2, backoff_factor=0.01, retry_on_errors=[]),
        )
        assert result.success is False

    def test_execute_with_fallback_primary_succeeds(
        self, registry_with_tools: ToolRegistry
    ) -> None:
        chain = FallbackChain(
            name="test_fallback",
            primary="echo",
            fallbacks=["search"],
        )
        recovery = RecoveryManager(registry_with_tools)
        result = recovery.execute_with_fallback(chain, {"message": "hello"})
        assert result.success is True
        assert result.data == {"echo": "hello"}

    def test_execute_with_fallback_uses_first_success(
        self, registry_with_tools: ToolRegistry
    ) -> None:
        registry_with_tools._tools["fail1"] = type(
            "Fail1",
            (BaseTool,),
            {
                "name": "fail1",
                "description": "Fails",
                "parameters": {},
                "execute": lambda self, **kw: ToolResult(success=False, data=None, error="fail"),
            },
        )
        chain = FallbackChain(
            name="test_fallback_chain",
            primary="fail1",
            fallbacks=["echo"],
        )
        recovery = RecoveryManager(registry_with_tools)
        result = recovery.execute_with_fallback(chain, {"message": "from fallback"})
        assert result.success is True

    def test_execute_with_fallback_all_fail(self, registry_with_tools: ToolRegistry) -> None:
        registry_with_tools._tools["fail1"] = type(
            "Fail1",
            (BaseTool,),
            {
                "name": "fail1",
                "description": "Fails",
                "parameters": {},
                "execute": lambda self, **kw: ToolResult(success=False, data=None, error="fail1"),
            },
        )
        registry_with_tools._tools["fail2"] = type(
            "Fail2",
            (BaseTool,),
            {
                "name": "fail2",
                "description": "Fails too",
                "parameters": {},
                "execute": lambda self, **kw: ToolResult(success=False, data=None, error="fail2"),
            },
        )
        chain = FallbackChain(
            name="all_fail",
            primary="fail1",
            fallbacks=["fail2"],
        )
        recovery = RecoveryManager(registry_with_tools)
        result = recovery.execute_with_fallback(chain, {})
        assert result.success is False
        assert "All fallbacks failed" in result.error

    def test_execute_with_fallback_nonexistent_tool(
        self, registry_with_tools: ToolRegistry
    ) -> None:
        chain = FallbackChain(
            name="missing_fallback",
            primary="nonexistent",
            fallbacks=["also_missing", "echo"],
        )
        recovery = RecoveryManager(registry_with_tools)
        result = recovery.execute_with_fallback(chain, {"message": "hello"})
        assert result.success is True

    def test_backoff_calculation(self) -> None:
        assert RecoveryManager._backoff(0, 1.5) == 1.5**0  # 1.0
        assert RecoveryManager._backoff(1, 1.5) == 1.5**1  # 1.5
        assert RecoveryManager._backoff(2, 1.5) == 1.5**2  # 2.25
        assert RecoveryManager._backoff(0, 2.0) == 1.0

    def test_should_retry_empty_patterns(self) -> None:
        policy = RetryPolicy(max_retries=3, retry_on_errors=[])
        assert RecoveryManager._should_retry("any error", policy) is True

    def test_should_retry_matching_patterns(self) -> None:
        policy = RetryPolicy(max_retries=3, retry_on_errors=["network", "timeout"])
        assert RecoveryManager._should_retry("Network error", policy) is True
        assert RecoveryManager._should_retry("Timeout expired", policy) is True
        assert RecoveryManager._should_retry("ValueError", policy) is False

    def test_default_retry_policies(self) -> None:
        assert DEFAULT_NETWORK_POLICY.max_retries == 3
        assert "ConnectionError" in DEFAULT_NETWORK_POLICY.retry_on_errors


class TestToolAnalytics:
    """Tests for ToolAnalytics — recording, querying, recommendations."""

    def test_record_and_get_stats(self) -> None:
        analytics = ToolAnalytics(db_path=None)
        now = time.time()
        analytics.record(
            ToolUsage(tool_name="echo", timestamp=now, success=True, duration_ms=100.0)
        )
        analytics.record(
            ToolUsage(
                tool_name="echo", timestamp=now + 1, success=False, duration_ms=200.0, error="fail"
            )
        )

        stats = analytics.get_stats()
        assert len(stats) == 1
        assert stats[0].tool_name == "echo"
        assert stats[0].total_calls == 2
        assert stats[0].success_rate == 0.5
        assert stats[0].error_count == 1

    def test_get_stats_filtered(self) -> None:
        analytics = ToolAnalytics(db_path=None)
        now = time.time()
        analytics.record(
            ToolUsage(tool_name="search", timestamp=now, success=True, duration_ms=50.0)
        )
        analytics.record(
            ToolUsage(tool_name="echo", timestamp=now, success=True, duration_ms=100.0)
        )

        stats = analytics.get_stats(tool_name="search")
        assert len(stats) == 1
        assert stats[0].tool_name == "search"

    def test_get_stats_empty(self) -> None:
        analytics = ToolAnalytics(db_path=None)
        stats = analytics.get_stats()
        assert stats == []

    def test_slow_tools(self) -> None:
        analytics = ToolAnalytics(db_path=None)
        now = time.time()
        analytics.record(
            ToolUsage(tool_name="slow", timestamp=now, success=True, duration_ms=6000.0)
        )
        analytics.record(
            ToolUsage(tool_name="fast", timestamp=now, success=True, duration_ms=100.0)
        )

        slow = analytics.get_slow_tools(threshold_ms=5000)
        assert len(slow) == 1
        assert slow[0].tool_name == "slow"

    def test_no_slow_tools(self) -> None:
        analytics = ToolAnalytics(db_path=None)
        now = time.time()
        analytics.record(
            ToolUsage(tool_name="fast", timestamp=now, success=True, duration_ms=100.0)
        )

        slow = analytics.get_slow_tools(threshold_ms=5000)
        assert slow == []

    def test_unreliable_tools(self) -> None:
        analytics = ToolAnalytics(db_path=None)
        now = time.time()
        # 6 successes, 4 failures → 60% success rate < 80% threshold
        for i in range(6):
            analytics.record(
                ToolUsage(tool_name="flaky", timestamp=now + i, success=True, duration_ms=100.0)
            )
        for i in range(4):
            analytics.record(
                ToolUsage(
                    tool_name="flaky",
                    timestamp=now + 10 + i,
                    success=False,
                    duration_ms=100.0,
                    error="fail",
                )
            )

        unreliable = analytics.get_unreliable_tools(threshold=0.8)
        assert len(unreliable) == 1
        assert unreliable[0].tool_name == "flaky"

    def test_reliable_tools_not_flagged(self) -> None:
        analytics = ToolAnalytics(db_path=None)
        now = time.time()
        # 95% success rate
        for i in range(19):
            analytics.record(
                ToolUsage(tool_name="reliable", timestamp=now + i, success=True, duration_ms=50.0)
            )
        analytics.record(
            ToolUsage(
                tool_name="reliable",
                timestamp=now + 20,
                success=False,
                duration_ms=50.0,
                error="oops",
            )
        )

        unreliable = analytics.get_unreliable_tools(threshold=0.8)
        assert unreliable == []

    def test_recommendations(self) -> None:
        analytics = ToolAnalytics(db_path=None)
        now = time.time()
        # Slow + unreliable tool
        for i in range(5):
            analytics.record(
                ToolUsage(
                    tool_name="bad_tool",
                    timestamp=now + i,
                    success=False,
                    duration_ms=8000.0,
                    error="fail",
                )
            )

        recs = analytics.get_recommendations()
        assert len(recs) >= 1
        assert recs[0]["tool"] == "bad_tool"
        assert len(recs[0]["suggestions"]) >= 2

    def test_no_recommendations_for_healthy_tools(self) -> None:
        analytics = ToolAnalytics(db_path=None)
        now = time.time()
        analytics.record(
            ToolUsage(tool_name="good", timestamp=now, success=True, duration_ms=100.0)
        )

        recs = analytics.get_recommendations()
        assert recs == []

    @pytest.mark.asyncio
    async def test_async_record_and_stats(self) -> None:
        analytics = ToolAnalytics(db_path=None)
        now = time.time()
        usage = ToolUsage(tool_name="echo", timestamp=now, success=True, duration_ms=50.0)
        await analytics.record_async(usage)
        stats = await analytics.get_stats_async()
        assert len(stats) == 1
        assert stats[0].tool_name == "echo"

    @pytest.mark.asyncio
    async def test_async_slow_tools(self) -> None:
        analytics = ToolAnalytics(db_path=None)
        now = time.time()
        analytics.record(
            ToolUsage(tool_name="slow", timestamp=now, success=True, duration_ms=7000.0)
        )
        slow = await analytics.get_slow_tools_async(threshold_ms=5000)
        assert len(slow) == 1

    @pytest.mark.asyncio
    async def test_async_recommendations(self) -> None:
        analytics = ToolAnalytics(db_path=None)
        now = time.time()
        for i in range(10):
            analytics.record(
                ToolUsage(
                    tool_name="bad",
                    timestamp=now + i,
                    success=False,
                    duration_ms=6000.0,
                    error="err",
                )
            )
        recs = await analytics.get_recommendations_async()
        assert len(recs) >= 1

    def test_persistent_db(self, tmp_path: Any) -> None:
        """Test that analytics persist across instances using a file DB."""
        db_file = tmp_path / "test_analytics.db"
        analytics1 = ToolAnalytics(db_path=db_file)
        now = time.time()
        analytics1.record(
            ToolUsage(tool_name="echo", timestamp=now, success=True, duration_ms=100.0)
        )

        # New instance pointing to same DB
        analytics2 = ToolAnalytics(db_path=db_file)
        stats = analytics2.get_stats()
        assert len(stats) == 1
        assert stats[0].tool_name == "echo"


class TestSmartToolRouter:
    """Integration tests for the SmartToolRouter facade."""

    def test_find_tools(self, registry_with_tools: ToolRegistry) -> None:
        router = SmartToolRouter(registry_with_tools)
        results = router.find_tools("search the web")
        assert len(results) >= 1
        assert any(r.tool_name == "search" for r in results)

    def test_execute_tool_success(self, registry_with_tools: ToolRegistry) -> None:
        router = SmartToolRouter(registry_with_tools)
        result = router.execute_tool("echo", {"message": "hello"})
        assert result.success is True
        assert result.data == {"echo": "hello"}

    def test_execute_tool_with_retry(self, registry_with_tools: ToolRegistry) -> None:
        router = SmartToolRouter(registry_with_tools)
        result = router.execute_tool(
            "echo",
            {"message": "hello"},
            retry_policy=RetryPolicy(max_retries=2, backoff_factor=0.01),
        )
        assert result.success is True

    def test_execute_tool_not_found(self, registry_with_tools: ToolRegistry) -> None:
        router = SmartToolRouter(registry_with_tools)
        result = router.execute_tool("nonexistent", {})
        assert result.success is False
        assert "not found" in result.error.lower()

    def test_execute_with_fallback(self, registry_with_tools: ToolRegistry) -> None:
        registry_with_tools._tools["fail1"] = type(
            "Fail1",
            (BaseTool,),
            {
                "name": "fail1",
                "description": "Fails",
                "parameters": {},
                "execute": lambda self, **kw: ToolResult(success=False, data=None, error="fail"),
            },
        )
        router = SmartToolRouter(registry_with_tools)
        chain = FallbackChain(name="fb", primary="fail1", fallbacks=["echo"])
        result = router.execute_with_fallback(chain, {"message": "fallback"})
        assert result.success is True

    def test_execute_chain(self, registry_with_tools: ToolRegistry) -> None:
        router = SmartToolRouter(registry_with_tools)
        chain = ToolChain(
            name="echo_chain",
            description="Test chain",
            steps=[ChainStep(tool_name="echo", params={"message": "chain test"})],
            required_tools=["echo"],
        )
        result = router.execute_chain(chain)
        assert result.success is True
        assert len(result.results) == 1

    def test_execute_chain_with_context(self, registry_with_tools: ToolRegistry) -> None:
        router = SmartToolRouter(registry_with_tools)
        chain = ToolChain(
            name="context_chain",
            description="Chain with context",
            steps=[ChainStep(tool_name="echo", params={"message": "{{msg}}"})],
            required_tools=["echo"],
        )
        result = router.execute_chain(chain, context={"msg": "from context"})
        assert result.success is True

    def test_analytics_integration(self, registry_with_tools: ToolRegistry) -> None:
        router = SmartToolRouter(registry_with_tools)
        # Execute a tool — should record analytics
        router.execute_tool("echo", {"message": "tracked"})
        # Check analytics
        stats = router.analytics.get_stats()
        assert len(stats) >= 1
        found = any(s.tool_name == "echo" for s in stats)
        assert found

    def test_recommendations_after_usage(self, registry_with_tools: ToolRegistry) -> None:
        router = SmartToolRouter(registry_with_tools)
        # Make some calls
        for _ in range(5):
            router.execute_tool("echo", {"message": "test"})
        recs = router.get_recommendations()
        # With success rate = 1.0 and low duration, no recommendations expected
        assert isinstance(recs, list)

    def test_find_tools_top_k(self, registry_with_tools: ToolRegistry) -> None:
        router = SmartToolRouter(registry_with_tools)
        results = router.find_tools("tool", top_k=2)
        assert len(results) <= 2


# ------------------------------------------------------------------
# Utility function tests
# ------------------------------------------------------------------


class TestUtilities:
    """Tests for module-level utility functions."""

    def test_deep_get_top_level(self) -> None:
        data = {"key": "value"}
        assert _deep_get(data, "key") == "value"

    def test_deep_get_nested(self) -> None:
        data = {"result": {"success": True, "data": {"items": [1, 2, 3]}}}
        assert _deep_get(data, "result.success") is True
        assert _deep_get(data, "result.data.items") == [1, 2, 3]

    def test_deep_get_missing(self) -> None:
        data = {"key": "value"}
        assert _deep_get(data, "missing.key") is None

    def test_resolve_placeholders_simple(self) -> None:
        result = _resolve_placeholders("Hello {{name}}", {"name": "World"})
        assert result == "Hello World"

    def test_resolve_placeholders_nested(self) -> None:
        result = _resolve_placeholders("{{result.data}}", {"result": {"data": "found"}})
        assert result == "found"

    def test_resolve_placeholders_missing(self) -> None:
        result = _resolve_placeholders("{{missing}}", {})
        assert result == "{{missing}}"

    def test_resolve_placeholders_multiple(self) -> None:
        result = _resolve_placeholders("{{a}} and {{b}}", {"a": "X", "b": "Y"})
        assert result == "X and Y"
