"""Tests for lilith_core.hooks — plugin hook system."""

import pytest

from lilith_core.hooks import (
    HookContext,
    HookRegistry,
    HookType,
    get_hook_registry,
    register_hook,
)


@pytest.fixture
def registry() -> HookRegistry:
    """Fresh registry for each test."""
    return HookRegistry()


@pytest.fixture
def ctx() -> HookContext:
    """Basic hook context."""
    return HookContext(
        hook_type=HookType.PRE_LLM_CALL,
        agent_name="test_agent",
        session_id="sess_001",
        data={"prompt": "hello"},
    )


class TestHookRegistry:
    """Tests for HookRegistry register/unregister/fire."""

    def test_register_and_fire(self, registry: HookRegistry, ctx: HookContext) -> None:
        called = []
        def my_hook(c: HookContext) -> HookContext:
            called.append(c.agent_name)
            return c

        registry.register(HookType.PRE_LLM_CALL, my_hook, name="test")
        result = registry.fire(ctx)
        assert result is not None
        assert called == ["test_agent"]

    def test_priority_order(self, registry: HookRegistry, ctx: HookContext) -> None:
        order = []
        def hook_a(c: HookContext) -> HookContext:
            order.append("a")
            return c
        def hook_b(c: HookContext) -> HookContext:
            order.append("b")
            return c

        registry.register(HookType.PRE_LLM_CALL, hook_a, name="a", priority=10)
        registry.register(HookType.PRE_LLM_CALL, hook_b, name="b", priority=1)
        registry.fire(ctx)
        assert order == ["b", "a"]

    def test_abort_chain(self, registry: HookRegistry, ctx: HookContext) -> None:
        reached = []
        def abort_hook(c: HookContext) -> None:
            return None
        def never_called(c: HookContext) -> HookContext:
            reached.append("nope")
            return c

        registry.register(HookType.PRE_LLM_CALL, abort_hook, name="abort", priority=0)
        registry.register(HookType.PRE_LLM_CALL, never_called, name="after", priority=1)
        result = registry.fire(ctx)
        assert result is None
        assert reached == []

    def test_modify_context(self, registry: HookRegistry, ctx: HookContext) -> None:
        def modifier(c: HookContext) -> HookContext:
            c.data["prompt"] = c.data["prompt"] + " world"
            return c

        registry.register(HookType.PRE_LLM_CALL, modifier, name="mod")
        result = registry.fire(ctx)
        assert result is not None
        assert result.data["prompt"] == "hello world"

    def test_unregister_by_name(self, registry: HookRegistry, ctx: HookContext) -> None:
        def hook(c: HookContext) -> HookContext:
            return c

        registry.register(HookType.PRE_LLM_CALL, hook, name="remove_me")
        assert len(registry.hooks_for(HookType.PRE_LLM_CALL)) == 1
        removed = registry.unregister("remove_me")
        assert removed == 1
        assert len(registry.hooks_for(HookType.PRE_LLM_CALL)) == 0

    def test_fire_empty_chain(self, registry: HookRegistry, ctx: HookContext) -> None:
        result = registry.fire(ctx)
        assert result is not None
        assert result.agent_name == "test_agent"

    def test_clear_all(self, registry: HookRegistry) -> None:
        def hook(c: HookContext) -> HookContext:
            return c

        registry.register(HookType.PRE_LLM_CALL, hook, name="a")
        registry.register(HookType.POST_LLM_CALL, hook, name="b")
        assert registry.hook_count == 2
        registry.clear()
        assert registry.hook_count == 0

    def test_clear_specific_type(self, registry: HookRegistry) -> None:
        def hook(c: HookContext) -> HookContext:
            return c

        registry.register(HookType.PRE_LLM_CALL, hook, name="a")
        registry.register(HookType.POST_LLM_CALL, hook, name="b")
        registry.clear(HookType.PRE_LLM_CALL)
        assert len(registry.hooks_for(HookType.PRE_LLM_CALL)) == 0
        assert len(registry.hooks_for(HookType.POST_LLM_CALL)) == 1

    def test_hook_count(self, registry: HookRegistry) -> None:
        def hook(c: HookContext) -> HookContext:
            return c

        assert registry.hook_count == 0
        registry.register(HookType.PRE_LLM_CALL, hook, name="a")
        registry.register(HookType.POST_TOOL_CALL, hook, name="b")
        assert registry.hook_count == 2


class TestHookContext:
    """Tests for HookContext dataclass."""

    def test_defaults(self) -> None:
        ctx = HookContext(
            hook_type=HookType.ON_SESSION_START,
            agent_name="agent",
            session_id="s1",
        )
        assert ctx.data == {}
        assert ctx.metadata == {}

    def test_hook_types_exist(self) -> None:
        assert HookType.PRE_LLM_CALL.value == "pre_llm_call"
        assert HookType.POST_LLM_CALL.value == "post_llm_call"
        assert HookType.PRE_TOOL_CALL.value == "pre_tool_call"
        assert HookType.POST_TOOL_CALL.value == "post_tool_call"
        assert HookType.ON_SESSION_START.value == "on_session_start"
        assert HookType.ON_SESSION_END.value == "on_session_end"

    def test_subagent_hook_types_exist(self) -> None:
        """PRE_SUBAGENT_SPAWN and POST_SUBAGENT_RESULT are declared on HookType.

        These two values are the bridge between lilith-core's plugin hook
        system and lilith-orchestrator's SubAgentRunner. They were added
        so the orchestrator can emit lifecycle events that arbitrary
        policy / audit / telemetry plugins (registered against the
        global HookRegistry) can observe and rewrite.
        """
        assert HookType.PRE_SUBAGENT_SPAWN.value == "pre_subagent_spawn"
        assert HookType.POST_SUBAGENT_RESULT.value == "post_subagent_result"

    def test_subagent_hook_types_registerable(self, registry: HookRegistry) -> None:
        """The two sub-agent hook types accept registrations like any other."""
        seen: list[str] = []

        def pre(c: HookContext) -> HookContext:
            seen.append(f"pre:{c.data.get('agent_type')}")
            return c

        def post(c: HookContext) -> HookContext:
            seen.append(f"post:{c.data.get('agent_type')}")
            return c

        registry.register(HookType.PRE_SUBAGENT_SPAWN, pre, name="audit_pre")
        registry.register(HookType.POST_SUBAGENT_RESULT, post, name="audit_post")
        # The new types must be auto-created in the registry's _hooks dict
        # (the registry constructor iterates HookType to seed it).
        pre_hooks = registry.hooks_for(HookType.PRE_SUBAGENT_SPAWN)
        post_hooks = registry.hooks_for(HookType.POST_SUBAGENT_RESULT)
        assert len(pre_hooks) == 1
        assert pre_hooks[0].name == "audit_pre"
        assert len(post_hooks) == 1
        assert post_hooks[0].name == "audit_post"

        # Fire a synthetic pre-spawn event and confirm the callback ran
        ctx = HookContext(
            hook_type=HookType.PRE_SUBAGENT_SPAWN,
            agent_name="orchestrator",
            session_id="sess_1",
            data={"agent_type": "researcher"},
        )
        out = registry.fire(ctx)
        assert out is not None
        assert seen == ["pre:researcher"]

    def test_subagent_hook_can_rewrite_user_input(
        self, registry: HookRegistry
    ) -> None:
        """A pre-spawn hook can rewrite the user_input before the executor runs.

        This is the canonical use case: a policy hook adds context /
        redaction / instructions to a child agent's input without the
        orchestrator knowing about it.
        """

        def add_context(c: HookContext) -> HookContext:
            original = c.data.get("user_input", "")
            return HookContext(
                hook_type=c.hook_type,
                agent_name=c.agent_name,
                session_id=c.session_id,
                data={**c.data, "user_input": f"[REDACTED-CTX] {original}"},
            )

        registry.register(
            HookType.PRE_SUBAGENT_SPAWN, add_context, name="redactor", priority=10
        )
        ctx = HookContext(
            hook_type=HookType.PRE_SUBAGENT_SPAWN,
            agent_name="orchestrator",
            session_id="sess_1",
            data={"agent_type": "coder", "user_input": "fix bug"},
        )
        out = registry.fire(ctx)
        assert out is not None
        assert out.data["user_input"] == "[REDACTED-CTX] fix bug"

    def test_subagent_hook_can_abort_spawn(self, registry: HookRegistry) -> None:
        """A pre-spawn hook that returns None aborts the chain.

        SubAgentRunner treats a None return as a signal to skip the
        executor and return a SubAgentResult with success=False.
        """

        def deny(c: HookContext) -> None:
            return None  # explicit abort

        registry.register(
            HookType.PRE_SUBAGENT_SPAWN, deny, name="policy_deny", priority=-1
        )
        ctx = HookContext(
            hook_type=HookType.PRE_SUBAGENT_SPAWN,
            agent_name="orchestrator",
            session_id="sess_1",
            data={"agent_type": "dangerous"},
        )
        out = registry.fire(ctx)
        assert out is None


class TestGlobalRegistry:
    """Tests for the global registry singleton."""

    def test_singleton(self) -> None:
        r1 = get_hook_registry()
        r2 = get_hook_registry()
        assert r1 is r2

    def test_register_hook_convenience(self) -> None:
        reg = get_hook_registry()
        reg.clear()

        def hook(c: HookContext) -> HookContext:
            return c

        register_hook(HookType.PRE_LLM_CALL, hook, name="global_test")
        assert len(reg.hooks_for(HookType.PRE_LLM_CALL)) == 1
        reg.clear()
