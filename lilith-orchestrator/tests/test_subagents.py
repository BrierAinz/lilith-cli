"""Tests for lilith_orchestrator.subagents — Neurosurfer-inspired
sub-agent primitive: definitions, registry, runner, parallel spawn,
depth + concurrency caps, fork semantics, tool filtering.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from lilith_orchestrator.subagents import (
    MAX_DEPTH,
    SubAgentDefinition,
    SubAgentGuardrails,
    SubAgentResult,
    SubAgentRunner,
    agent_types,
    all_agents,
    clear_registry,
    get_agent,
    make_default_definitions,
    register,
    register_defaults,
    unregister,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_registry():
    """Each test gets a pristine registry."""
    clear_registry()
    yield
    clear_registry()


POOL = ["read_file", "write_file", "terminal", "patch", "search_files", "web_search"]


# ── Definition tests ─────────────────────────────────────────────────────────


class TestSubAgentDefinition:
    def test_minimal_fields(self):
        defn = SubAgentDefinition(agent_type="minimal")
        assert defn.agent_type == "minimal"
        assert defn.allowed_tools == ["*"]
        assert defn.disallowed_tools == []
        assert defn.model_preference is None

    def test_get_system_prompt_static(self):
        defn = SubAgentDefinition(agent_type="x", system_prompt="hello")
        assert defn.get_system_prompt() == "hello"

    def test_get_system_prompt_lazy_callable(self):
        calls = {"n": 0}

        def build():
            calls["n"] += 1
            return "lazy prompt"

        defn = SubAgentDefinition(agent_type="x", system_prompt=build)
        # Callable not invoked yet
        assert calls["n"] == 0
        out = defn.get_system_prompt()
        assert out == "lazy prompt"
        assert calls["n"] == 1
        # Second call invokes again (no caching — by design)
        defn.get_system_prompt()
        assert calls["n"] == 2

    def test_resolve_tools_inherit_all(self):
        defn = SubAgentDefinition(agent_type="x", allowed_tools=["*"])
        assert defn.resolve_tools(POOL) == POOL

    def test_resolve_tools_explicit_allowlist(self):
        defn = SubAgentDefinition(
            agent_type="x", allowed_tools=["read_file", "search_files"]
        )
        assert defn.resolve_tools(POOL) == ["read_file", "search_files"]

    def test_resolve_tools_allowlist_intersects_pool(self):
        """Tools not in pool are silently dropped."""
        defn = SubAgentDefinition(
            agent_type="x",
            allowed_tools=["read_file", "not_in_pool"],
        )
        assert defn.resolve_tools(POOL) == ["read_file"]

    def test_resolve_tools_disallowed_removes(self):
        defn = SubAgentDefinition(
            agent_type="x",
            allowed_tools=["*"],
            disallowed_tools=["terminal", "write_file"],
        )
        result = defn.resolve_tools(POOL)
        assert "terminal" not in result
        assert "write_file" not in result
        assert "read_file" in result

    def test_resolve_tools_disallow_preserves_order(self):
        defn = SubAgentDefinition(
            agent_type="x",
            disallowed_tools=["terminal"],
        )
        result = defn.resolve_tools(POOL)
        assert result.index("read_file") < result.index("patch")


# ── Registry tests ───────────────────────────────────────────────────────────


class TestSubAgentRegistry:
    def test_register_and_get(self):
        defn = SubAgentDefinition(agent_type="r1", system_prompt="p")
        register(defn)
        assert get_agent("r1") is defn

    def test_get_unknown_returns_none(self):
        assert get_agent("does_not_exist") is None

    def test_register_overwrites(self):
        register(SubAgentDefinition(agent_type="x", system_prompt="v1"))
        register(SubAgentDefinition(agent_type="x", system_prompt="v2"))
        assert get_agent("x").system_prompt == "v2"

    def test_all_agents_returns_snapshot(self):
        register(SubAgentDefinition(agent_type="a"))
        register(SubAgentDefinition(agent_type="b"))
        snap = all_agents()
        assert {d.agent_type for d in snap} == {"a", "b"}
        # Mutating the snapshot list does not affect the registry
        snap.clear()
        assert len(all_agents()) == 2

    def test_unregister(self):
        register(SubAgentDefinition(agent_type="x"))
        assert unregister("x") is True
        assert get_agent("x") is None

    def test_unregister_missing_returns_false(self):
        assert unregister("ghost") is False

    def test_clear_registry(self):
        for t in ["a", "b", "c"]:
            register(SubAgentDefinition(agent_type=t))
        clear_registry()
        assert all_agents() == []

    def test_agent_types_sorted(self):
        register(SubAgentDefinition(agent_type="zeta"))
        register(SubAgentDefinition(agent_type="alpha"))
        register(SubAgentDefinition(agent_type="mu"))
        assert agent_types() == ["alpha", "mu", "zeta"]

    def test_register_defaults_idempotent(self):
        n1 = register_defaults()
        # 8 personas: researcher, editor, auditor, coder, tester,
        # security, reviewer, planner
        assert n1 == 8
        n2 = register_defaults()
        assert n2 == 0  # already there

    def test_make_default_definitions_have_tags(self):
        defs = make_default_definitions()
        assert len(defs) >= 3
        for d in defs:
            assert d.agent_type
            assert d.system_prompt
            assert isinstance(d.tags, list)


# ── Guardrails tests ─────────────────────────────────────────────────────────


class TestSubAgentGuardrails:
    def test_defaults(self):
        g = SubAgentGuardrails()
        assert g.max_concurrent_subagents == 4
        assert g.max_depth == MAX_DEPTH == 3
        assert g.timeout_seconds == 0.0

    def test_rejects_zero_concurrency(self):
        with pytest.raises(ValueError, match="max_concurrent_subagents"):
            SubAgentGuardrails(max_concurrent_subagents=0)

    def test_rejects_negative_depth(self):
        with pytest.raises(ValueError, match="max_depth"):
            SubAgentGuardrails(max_depth=-1)

    def test_custom_values(self):
        g = SubAgentGuardrails(
            max_concurrent_subagents=8,
            max_depth=5,
            timeout_seconds=30.0,
        )
        assert g.max_concurrent_subagents == 8
        assert g.max_depth == 5
        assert g.timeout_seconds == 30.0


# ── Runner: synchronous & basics ─────────────────────────────────────────────


class TestSubAgentRunnerBasics:
    def test_runner_init_with_defaults(self):
        runner = SubAgentRunner(full_tool_pool=POOL)
        assert runner.full_tool_pool == POOL
        assert runner.guardrails.max_concurrent_subagents == 4
        assert runner.executor is None

    def test_runner_init_pool_is_copied(self):
        """Mutating the caller's pool must not affect the runner."""
        pool = ["a", "b"]
        runner = SubAgentRunner(full_tool_pool=pool)
        pool.append("c")
        assert runner.full_tool_pool == ["a", "b"]

    def test_stats_initial(self):
        runner = SubAgentRunner(full_tool_pool=POOL)
        s = runner.stats
        assert s["completed"] == 0
        assert s["failed"] == 0
        assert s["in_flight"] == 0
        assert s["max_concurrent"] == 4

    def test_reset_stats(self):
        runner = SubAgentRunner(full_tool_pool=POOL)
        runner._completed = 5
        runner._failed = 2
        runner.reset_stats()
        assert runner.stats["completed"] == 0
        assert runner.stats["failed"] == 0

    def test_make_spawn_fn_attaches_spawn_many(self):
        runner = SubAgentRunner(full_tool_pool=POOL)
        spawn = runner.make_spawn_fn(parent_depth=0)
        assert callable(spawn)
        assert callable(spawn.spawn_many)

    def test_max_depth_constant(self):
        assert MAX_DEPTH == 3


# ── Runner: async spawn ──────────────────────────────────────────────────────


class TestSubAgentRunnerSpawn:
    @pytest.mark.asyncio
    async def test_spawn_stub_executor(self):
        """Without an executor, the runner returns a deterministic stub."""
        runner = SubAgentRunner(full_tool_pool=POOL)
        register(
            SubAgentDefinition(
                agent_type="r", allowed_tools=["read_file"], tags=["t"]
            )
        )
        spawn = runner.make_spawn_fn(parent_depth=0)
        result = await spawn("r", "hi")
        assert isinstance(result, SubAgentResult)
        assert result.success
        assert result.agent_type == "r"
        assert result.depth == 1
        assert "stub:r" in result.output
        assert "hi" in result.output
        assert "read_file" in result.output
        assert result.spawn_id  # uuid hex present

    @pytest.mark.asyncio
    async def test_spawn_with_custom_executor(self):
        async def my_executor(sys_prompt, user_input, tools, model):
            return f"OK|{sys_prompt[:5]}|{user_input}|{'+'.join(tools)}|{model}"

        runner = SubAgentRunner(
            full_tool_pool=POOL,
            executor=my_executor,
            default_model_preference="haiku",
        )
        register(
            SubAgentDefinition(
                agent_type="r",
                system_prompt="You are R",
                allowed_tools=["read_file", "write_file"],
                model_preference=None,
            )
        )
        spawn = runner.make_spawn_fn(parent_depth=0)
        result = await spawn("r", "say hi")
        assert result.success
        assert result.output.startswith("OK|")
        assert "say hi" in result.output
        assert "read_file+write_file" in result.output
        assert "haiku" in result.output  # came from default, not definition
        assert result.tools_used == ["read_file", "write_file"]

    @pytest.mark.asyncio
    async def test_spawn_unknown_type_fails(self):
        runner = SubAgentRunner(full_tool_pool=POOL)
        spawn = runner.make_spawn_fn(parent_depth=0)
        result = await spawn("ghost", "hi")
        assert not result.success
        assert "unknown agent_type" in (result.error or "")
        assert runner.stats["failed"] == 1

    @pytest.mark.asyncio
    async def test_spawn_depth_cap(self):
        runner = SubAgentRunner(
            full_tool_pool=POOL,
            guardrails=SubAgentGuardrails(max_depth=2),
        )
        register(SubAgentDefinition(agent_type="r"))
        # parent_depth=2 → child at depth 3, exceeds max_depth=2
        spawn = runner.make_spawn_fn(parent_depth=2)
        result = await spawn("r", "deep")
        assert not result.success
        assert "max_depth" in (result.error or "")

    @pytest.mark.asyncio
    async def test_spawn_records_duration(self):
        runner = SubAgentRunner(full_tool_pool=POOL)
        register(SubAgentDefinition(agent_type="r"))
        spawn = runner.make_spawn_fn(parent_depth=0)
        result = await spawn("r", "x")
        assert result.duration_ms >= 0
        assert result.duration_ms < 1000  # stub should be fast

    @pytest.mark.asyncio
    async def test_spawn_executor_exception_caught(self):
        async def boom(*_a, **_kw):
            raise RuntimeError("explosion")

        runner = SubAgentRunner(full_tool_pool=POOL, executor=boom)
        register(SubAgentDefinition(agent_type="r"))
        spawn = runner.make_spawn_fn(parent_depth=0)
        result = await spawn("r", "x")
        assert not result.success
        assert "RuntimeError" in (result.error or "")
        assert "explosion" in (result.error or "")

    @pytest.mark.asyncio
    async def test_spawn_timeout(self):
        async def slow(*_a, **_kw):
            await asyncio.sleep(0.5)

        runner = SubAgentRunner(
            full_tool_pool=POOL,
            executor=slow,
            guardrails=SubAgentGuardrails(timeout_seconds=0.05),
        )
        register(SubAgentDefinition(agent_type="r"))
        spawn = runner.make_spawn_fn(parent_depth=0)
        result = await spawn("r", "x")
        assert not result.success
        # asyncio.TimeoutError raised; exception caught by runner
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_spawn_many_returns_in_order(self):
        async def echo(sys_prompt, user_input, tools, model):
            return user_input.upper()

        runner = SubAgentRunner(full_tool_pool=POOL, executor=echo)
        register(SubAgentDefinition(agent_type="r"))
        spawn = runner.make_spawn_fn(parent_depth=0)
        results = await spawn.spawn_many(
            [("r", "a"), ("r", "b"), ("r", "c")]
        )
        assert [r.output for r in results] == ["A", "B", "C"]
        assert all(r.success for r in results)

    @pytest.mark.asyncio
    async def test_spawn_many_parallel(self):
        """spawn_many should run in parallel, not serial."""
        barrier = asyncio.Event()
        counter = {"n": 0}

        async def slow_executor(*_a, **_kw):
            counter["n"] += 1
            if counter["n"] >= 3:
                barrier.set()
            await barrier.wait()
            return "done"

        runner = SubAgentRunner(full_tool_pool=POOL, executor=slow_executor)
        register(SubAgentDefinition(agent_type="r"))
        spawn = runner.make_spawn_fn(parent_depth=0)
        start = time.monotonic()
        results = await spawn.spawn_many(
            [("r", "1"), ("r", "2"), ("r", "3")]
        )
        elapsed = time.monotonic() - start
        assert all(r.success for r in results)
        # If parallel, elapsed should be much less than 3 * barrier_wait
        # (we don't measure exact timing; just assert < 1.0s for fast tests)
        assert elapsed < 1.0

    @pytest.mark.asyncio
    async def test_spawn_stats_increment(self):
        runner = SubAgentRunner(full_tool_pool=POOL)
        register(SubAgentDefinition(agent_type="r"))
        spawn = runner.make_spawn_fn(parent_depth=0)
        await spawn("r", "1")
        await spawn("r", "2")
        await spawn("ghost", "3")
        assert runner.stats["completed"] == 2
        assert runner.stats["failed"] == 1


# ── Runner: tool filtering ────────────────────────────────────────────────────


class TestSubAgentRunnerToolFiltering:
    @pytest.mark.asyncio
    async def test_executor_receives_filtered_tools(self):
        seen = {}

        async def capture(sys_prompt, user_input, tools, model):
            seen["tools"] = tools
            return "ok"

        runner = SubAgentRunner(full_tool_pool=POOL, executor=capture)
        register(
            SubAgentDefinition(
                agent_type="r",
                allowed_tools=["read_file", "search_files"],
                disallowed_tools=["search_files"],
            )
        )
        spawn = runner.make_spawn_fn(parent_depth=0)
        await spawn("r", "hi")
        assert seen["tools"] == ["read_file"]


# ── Public exports ───────────────────────────────────────────────────────────


class TestPackageExports:
    def test_all_required_names_exported(self):
        import lilith_orchestrator as L

        for name in (
            "SubAgentDefinition",
            "SubAgentRunner",
            "SubAgentGuardrails",
            "SubAgentResult",
            "register_subagent",
            "register_default_subagents",
            "all_subagent_definitions",
            "clear_subagent_registry",
            "get_subagent",
            "unregister_subagent",
            "make_default_definitions",
            "agent_types",
            "SUBAGENT_MAX_DEPTH",
        ):
            assert hasattr(L, name), f"missing export: {name}"

    def test_version_is_1_12_0(self):
        import lilith_orchestrator as L

        assert L.__version__ == "1.12.0"
