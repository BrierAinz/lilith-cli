"""SubAgentRunner <-> lilith_core.hooks integration tests.

Closes the loop between the SubAgentRunner primitive and the plugin
hook system shipped in ``lilith_core.hooks``. The runner accepts an
optional ``hook_registry`` and, when one is attached, fires:

  * ``HookType.PRE_SUBAGENT_SPAWN``  — before the executor runs
    (with rewrite / abort semantics — see ``lilith_core.hooks``)
  * ``HookType.POST_SUBAGENT_RESULT`` — after the executor returns
    (observational + optional output rewrite)

The runner exposes new counters ``hooks_fired``, ``hooks_aborted`` and
``hooks_rewritten`` on ``stats()``. The default constructor does NOT
attach a registry, so existing call sites and tests are untouched.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

# Ensure the package is importable when running pytest from Asgard root.
_ASGARD = Path(__file__).resolve().parents[1]
if str(_ASGARD) not in sys.path:
    sys.path.insert(0, str(_ASGARD))

from lilith_core.hooks import (  # noqa: E402
    HookContext,
    HookRegistry,
    HookType,
)

from lilith_orchestrator.subagents import (  # noqa: E402
    SubAgentDefinition,
    SubAgentRunner,
    clear_registry,
    register,
)


# ─── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_registry():
    """Reset the global SubAgentDefinition registry between tests."""
    clear_registry()
    yield
    clear_registry()


@pytest.fixture
def pool():
    return [
        "read_file",
        "search_files",
        "write_file",
        "patch",
        "terminal",
        "web_search",
    ]


@pytest.fixture
def stub_executor():
    """Async stub executor that records invocations and echoes the input."""
    calls: list[dict] = []

    async def _executor(system_prompt, user_input, tools, model_pref):
        calls.append(
            {
                "system_prompt": system_prompt,
                "user_input": user_input,
                "tools": list(tools),
                "model_preference": model_pref,
            }
        )
        return f"STUB[{user_input}]"

    _executor.calls = calls  # type: ignore[attr-defined]
    return _executor


# ─── Default: no hook wiring ────────────────────────────────────────────────


class TestNoHookRegistry:
    """When the runner is constructed without a hook registry, spawning
    proceeds exactly as before — no surprise side-effects on tests or
    on the default SubAgentRunner used in production."""

    def test_default_constructor_has_no_registry(self, pool, stub_executor):
        runner = SubAgentRunner(full_tool_pool=pool, executor=stub_executor)
        assert runner.hook_registry is None
        # Stats should not expose hook counters (they default to 0).
        stats = runner.stats
        assert stats["hooks_fired"] == 0
        assert stats["hooks_aborted"] == 0
        assert stats["hooks_rewritten"] == 0

    @pytest.mark.asyncio
    async def test_spawn_works_without_hooks(self, pool, stub_executor):
        register(SubAgentDefinition(agent_type="r"))
        runner = SubAgentRunner(full_tool_pool=pool, executor=stub_executor)
        result = await runner.make_spawn_fn()("r", "do thing")
        assert result.success
        assert stub_executor.calls[0]["user_input"] == "do thing"


# ─── PRE_SUBAGENT_SPAWN: observation only ───────────────────────────────────


class TestPreSpawnObservation:
    """A hook that returns the same context unchanged still observes
    the spawn. Counters increment; nothing else changes."""

    @pytest.mark.asyncio
    async def test_observe_only_hook_increments_counter(
        self, pool, stub_executor
    ):
        register(SubAgentDefinition(agent_type="r"))
        registry = HookRegistry()
        seen: list[str] = []

        def observe(c: HookContext) -> HookContext:
            seen.append(c.data["agent_type"])
            return c

        registry.register(
            HookType.PRE_SUBAGENT_SPAWN, observe, name="audit"
        )

        runner = SubAgentRunner(
            full_tool_pool=pool,
            executor=stub_executor,
            hook_registry=registry,
        )
        result = await runner.make_spawn_fn()("r", "do thing")
        assert result.success
        assert seen == ["r"]
        # A successful spawn fires the pre-hook (1) + post-hook (1) = 2.
        assert runner.stats["hooks_fired"] == 2
        assert runner.stats["hooks_aborted"] == 0
        assert runner.stats["hooks_rewritten"] == 0


# ─── PRE_SUBAGENT_SPAWN: rewrite ────────────────────────────────────────────


class TestPreSpawnRewrite:
    """A pre-spawn hook can rewrite ``user_input``, ``allowed_tools``,
    and ``model_preference`` — the runner applies the rewrites to the
    executor call."""

    @pytest.mark.asyncio
    async def test_pre_hook_rewrites_user_input(self, pool, stub_executor):
        register(SubAgentDefinition(agent_type="r"))
        registry = HookRegistry()

        def redactor(c: HookContext) -> HookContext:
            return HookContext(
                hook_type=c.hook_type,
                agent_name=c.agent_name,
                session_id=c.session_id,
                data={**c.data, "user_input": f"[REDACTED] {c.data['user_input']}"},
            )

        registry.register(
            HookType.PRE_SUBAGENT_SPAWN, redactor, name="redactor"
        )

        runner = SubAgentRunner(
            full_tool_pool=pool,
            executor=stub_executor,
            hook_registry=registry,
        )
        result = await runner.make_spawn_fn()("r", "fix the bug")
        assert result.success
        # The executor saw the rewritten input.
        assert stub_executor.calls[0]["user_input"] == "[REDACTED] fix the bug"
        assert runner.stats["hooks_rewritten"] == 1

    @pytest.mark.asyncio
    async def test_pre_hook_tightens_tool_pool(self, pool, stub_executor):
        register(
            SubAgentDefinition(
                agent_type="r",
                allowed_tools=["read_file", "search_files", "write_file"],
            )
        )
        registry = HookRegistry()

        def tighter(c: HookContext) -> HookContext:
            return HookContext(
                hook_type=c.hook_type,
                agent_name=c.agent_name,
                session_id=c.session_id,
                data={
                    **c.data,
                    "allowed_tools": ["read_file"],  # deny search/write
                },
            )

        registry.register(
            HookType.PRE_SUBAGENT_SPAWN, tighter, name="policy"
        )

        runner = SubAgentRunner(
            full_tool_pool=pool, executor=stub_executor, hook_registry=registry
        )
        result = await runner.make_spawn_fn()("r", "investigate")
        assert result.success
        assert stub_executor.calls[0]["tools"] == ["read_file"]

    @pytest.mark.asyncio
    async def test_pre_hook_overrides_model_preference(
        self, pool, stub_executor
    ):
        register(
            SubAgentDefinition(agent_type="r", model_preference="fast")
        )
        registry = HookRegistry()

        def upgrade(c: HookContext) -> HookContext:
            return HookContext(
                hook_type=c.hook_type,
                agent_name=c.agent_name,
                session_id=c.session_id,
                data={**c.data, "model_preference": "reasoning"},
            )

        registry.register(
            HookType.PRE_SUBAGENT_SPAWN, upgrade, name="cost"
        )

        runner = SubAgentRunner(
            full_tool_pool=pool, executor=stub_executor, hook_registry=registry
        )
        result = await runner.make_spawn_fn()("r", "deep analysis")
        assert result.success
        assert stub_executor.calls[0]["model_preference"] == "reasoning"


# ─── PRE_SUBAGENT_SPAWN: abort ──────────────────────────────────────────────


class TestPreSpawnAbort:
    """A pre-spawn hook that returns None aborts the spawn. The runner
    returns ``success=False`` with ``error='aborted by hook'`` and the
    executor is NEVER invoked."""

    @pytest.mark.asyncio
    async def test_abort_skips_executor(self, pool, stub_executor):
        register(SubAgentDefinition(agent_type="dangerous"))
        registry = HookRegistry()

        def deny(c: HookContext) -> None:
            return None  # explicit veto

        registry.register(
            HookType.PRE_SUBAGENT_SPAWN, deny, name="policy_deny"
        )

        runner = SubAgentRunner(
            full_tool_pool=pool, executor=stub_executor, hook_registry=registry
        )
        result = await runner.make_spawn_fn()("dangerous", "do evil")
        assert result.success is False
        assert result.error == "aborted by hook"
        assert len(stub_executor.calls) == 0
        # Stats reflect the abort.
        assert runner.stats["hooks_fired"] == 1
        assert runner.stats["hooks_aborted"] == 1

    @pytest.mark.asyncio
    async def test_abort_does_not_count_as_completion(
        self, pool, stub_executor
    ):
        register(SubAgentDefinition(agent_type="r"))
        registry = HookRegistry()
        registry.register(
            HookType.PRE_SUBAGENT_SPAWN,
            lambda c: None,
            name="deny_all",
        )
        runner = SubAgentRunner(
            full_tool_pool=pool, executor=stub_executor, hook_registry=registry
        )
        await runner.make_spawn_fn()("r", "x")
        # failed counter increments; completed stays at 0.
        assert runner.stats["completed"] == 0
        assert runner.stats["failed"] == 1


# ─── POST_SUBAGENT_RESULT: observation + output rewrite ─────────────────────


class TestPostSpawnHook:
    """The post-spawn hook fires after the executor returns. It can
    rewrite the output (audit / redaction use case) but does NOT
    influence success/error — only the output string."""

    @pytest.mark.asyncio
    async def test_post_hook_observes_result(self, pool, stub_executor):
        register(SubAgentDefinition(agent_type="r"))
        registry = HookRegistry()
        seen: list[dict] = []

        def observe(c: HookContext) -> HookContext:
            seen.append(dict(c.data))
            return c

        registry.register(
            HookType.POST_SUBAGENT_RESULT, observe, name="audit"
        )

        runner = SubAgentRunner(
            full_tool_pool=pool, executor=stub_executor, hook_registry=registry
        )
        result = await runner.make_spawn_fn()("r", "do thing")
        assert result.success
        assert len(seen) == 1
        record = seen[0]
        assert record["agent_type"] == "r"
        assert record["success"] is True
        assert record["output"] == "STUB[do thing]"
        assert "tools_used" in record

    @pytest.mark.asyncio
    async def test_post_hook_can_rewrite_output(self, pool, stub_executor):
        register(SubAgentDefinition(agent_type="r"))
        registry = HookRegistry()

        def redact(c: HookContext) -> HookContext:
            return HookContext(
                hook_type=c.hook_type,
                agent_name=c.agent_name,
                session_id=c.session_id,
                data={**c.data, "output": "[REDACTED-OUTPUT]"},
            )

        registry.register(
            HookType.POST_SUBAGENT_RESULT, redact, name="redact"
        )

        runner = SubAgentRunner(
            full_tool_pool=pool, executor=stub_executor, hook_registry=registry
        )
        result = await runner.make_spawn_fn()("r", "leak secret")
        assert result.success
        # The result returned to the caller carries the redacted output.
        assert result.output == "[REDACTED-OUTPUT]"
        assert runner.stats["hooks_rewritten"] == 1


# ─── Hook failure tolerance ─────────────────────────────────────────────────


class TestHookFailureTolerance:
    """A misbehaving hook (raises) must not crash the spawn. The runner
    logs and continues with the un-modified context."""

    @pytest.mark.asyncio
    async def test_broken_pre_hook_does_not_abort(self, pool, stub_executor):
        register(SubAgentDefinition(agent_type="r"))
        registry = HookRegistry()

        def broken(c: HookContext) -> HookContext:
            raise RuntimeError("synthetic boom")

        registry.register(
            HookType.PRE_SUBAGENT_SPAWN, broken, name="broken"
        )

        runner = SubAgentRunner(
            full_tool_pool=pool, executor=stub_executor, hook_registry=registry
        )
        result = await runner.make_spawn_fn()("r", "do thing")
        # Spawn still succeeds; the broken hook is logged + ignored.
        assert result.success
        assert len(stub_executor.calls) == 1

    @pytest.mark.asyncio
    async def test_broken_post_hook_does_not_break_result(
        self, pool, stub_executor
    ):
        register(SubAgentDefinition(agent_type="r"))
        registry = HookRegistry()

        def broken(c: HookContext) -> HookContext:
            raise RuntimeError("post boom")

        registry.register(
            HookType.POST_SUBAGENT_RESULT, broken, name="broken"
        )

        runner = SubAgentRunner(
            full_tool_pool=pool, executor=stub_executor, hook_registry=registry
        )
        result = await runner.make_spawn_fn()("r", "do thing")
        assert result.success
        assert result.output == "STUB[do thing]"


# ─── End-to-end: pre + post together ────────────────────────────────────────


class TestEndToEndHookChain:
    """A realistic setup: a redactor pre-hook + a telemetry post-hook
    attached to the same registry. Both fire; the spawn still completes."""

    @pytest.mark.asyncio
    async def test_pre_and_post_hooks_fire_in_order(
        self, pool, stub_executor
    ):
        register(SubAgentDefinition(agent_type="r"))
        registry = HookRegistry()
        events: list[str] = []

        def pre(c: HookContext) -> HookContext:
            events.append("pre")
            return HookContext(
                hook_type=c.hook_type,
                agent_name=c.agent_name,
                session_id=c.session_id,
                data={**c.data, "user_input": "PRE:" + c.data["user_input"]},
            )

        def post(c: HookContext) -> HookContext:
            events.append("post")
            return c  # no rewrite

        registry.register(HookType.PRE_SUBAGENT_SPAWN, pre, name="pre")
        registry.register(
            HookType.POST_SUBAGENT_RESULT, post, name="post"
        )

        runner = SubAgentRunner(
            full_tool_pool=pool, executor=stub_executor, hook_registry=registry
        )
        result = await runner.make_spawn_fn()("r", "task")
        assert result.success
        # Pre fires first, post second.
        assert events == ["pre", "post"]
        # The executor saw the rewritten input.
        assert stub_executor.calls[0]["user_input"] == "PRE:task"
        # Two hook firings (one pre, one post), one rewrite.
        assert runner.stats["hooks_fired"] == 2
        assert runner.stats["hooks_rewritten"] == 1


# ─── Stats: reset clears hook counters ─────────────────────────────────────


class TestStatsReset:
    @pytest.mark.asyncio
    async def test_reset_stats_clears_hook_counters(
        self, pool, stub_executor
    ):
        register(SubAgentDefinition(agent_type="r"))
        registry = HookRegistry()
        registry.register(
            HookType.PRE_SUBAGENT_SPAWN,
            lambda c: None,
            name="deny",
        )
        runner = SubAgentRunner(
            full_tool_pool=pool, executor=stub_executor, hook_registry=registry
        )
        await runner.make_spawn_fn()("r", "x")
        assert runner.stats["hooks_fired"] == 1
        assert runner.stats["hooks_aborted"] == 1
        runner.reset_stats()
        assert runner.stats["hooks_fired"] == 0
        assert runner.stats["hooks_aborted"] == 0
        assert runner.stats["hooks_rewritten"] == 0
