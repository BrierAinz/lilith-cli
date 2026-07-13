"""Tests for the Policy Engine (lilith_core.policy_engine)."""

from __future__ import annotations

import time

import pytest
from lilith_core.hooks import HookContext, HookType, get_hook_registry
from lilith_core.policy_engine import (
    AlwaysRule,
    CircuitBreakerRule,
    Policy,
    PolicyAction,
    PolicyEngine,
    PolicyScope,
    PolicyState,
    RateLimitRule,
    RegexRule,
    TokenBudgetRule,
    ToolAllowlistRule,
    ToolDenylistRule,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _tool_ctx(
    tool_name: str = "terminal",
    agent: str = "Odin",
    session: str = "sess-001",
) -> HookContext:
    return HookContext(
        hook_type=HookType.PRE_TOOL_CALL,
        agent_name=agent,
        session_id=session,
        data={"tool_name": tool_name, "params": {}},
    )


def _llm_ctx(
    message: str = "hello",
    agent: str = "Odin",
    session: str = "sess-001",
) -> HookContext:
    return HookContext(
        hook_type=HookType.PRE_LLM_CALL,
        agent_name=agent,
        session_id=session,
        data={"message": message},
    )


# ── PolicyScope ──────────────────────────────────────────────────────────────


class TestPolicyScope:
    def test_empty_scope_matches_everything(self):
        scope = PolicyScope()
        assert scope.matches(_tool_ctx())
        assert scope.matches(_llm_ctx())
        assert scope.matches(_tool_ctx(agent="Mimir"))

    def test_agent_scope(self):
        scope = PolicyScope(agent="Odin")
        assert scope.matches(_tool_ctx(agent="Odin"))
        assert not scope.matches(_tool_ctx(agent="Mimir"))

    def test_agent_scope_case_insensitive(self):
        scope = PolicyScope(agent="odin")
        assert scope.matches(_tool_ctx(agent="ODIN"))

    def test_tool_scope(self):
        scope = PolicyScope(tool="terminal")
        assert scope.matches(_tool_ctx(tool_name="terminal"))
        assert not scope.matches(_tool_ctx(tool_name="read_file"))

    def test_session_scope(self):
        scope = PolicyScope(session="sess-001")
        assert scope.matches(_tool_ctx(session="sess-001"))
        assert not scope.matches(_tool_ctx(session="sess-999"))

    def test_hook_type_scope(self):
        scope = PolicyScope(hook_type=HookType.PRE_TOOL_CALL)
        assert scope.matches(_tool_ctx())
        assert not scope.matches(_llm_ctx())

    def test_combined_scope(self):
        scope = PolicyScope(agent="Odin", tool="terminal")
        assert scope.matches(_tool_ctx(agent="Odin", tool_name="terminal"))
        assert not scope.matches(_tool_ctx(agent="Odin", tool_name="read_file"))
        assert not scope.matches(_tool_ctx(agent="Mimir", tool_name="terminal"))


# ── PolicyState ──────────────────────────────────────────────────────────────


class TestPolicyState:
    def test_counter(self):
        state = PolicyState()
        assert state.get_counter("x") == 0
        state.increment("x")
        assert state.get_counter("x") == 1
        state.increment("x", 5)
        assert state.get_counter("x") == 6

    def test_list(self):
        state = PolicyState()
        assert state.get_list("ts") == []
        state.append_list("ts", 1.0)
        state.append_list("ts", 2.0)
        assert state.get_list("ts") == [1.0, 2.0]
        state.set_list("ts", [3.0])
        assert state.get_list("ts") == [3.0]

    def test_kv(self):
        state = PolicyState()
        assert state.get("missing") is None
        assert state.get("missing", "default") == "default"
        state.set("key", "value")
        assert state.get("key") == "value"

    def test_reset(self):
        state = PolicyState()
        state.increment("c", 10)
        state.append_list("l", 1.0)
        state.set("k", "v")
        state.reset()
        assert state.get_counter("c") == 0
        assert state.get_list("l") == []
        assert state.get("k") is None


# ── Rules ────────────────────────────────────────────────────────────────────


class TestToolAllowlistRule:
    def test_blocks_unlisted_tool(self):
        rule = ToolAllowlistRule(tools=["terminal", "read_file"])
        state = PolicyState()
        assert rule.evaluate(_tool_ctx(tool_name="web_search"), state) is True
        assert rule.evaluate(_tool_ctx(tool_name="terminal"), state) is False

    def test_empty_list_blocks_all(self):
        rule = ToolAllowlistRule(tools=[])
        state = PolicyState()
        assert rule.evaluate(_tool_ctx(tool_name="terminal"), state) is True

    def test_ignores_non_tool_context(self):
        rule = ToolAllowlistRule(tools=["terminal"])
        state = PolicyState()
        assert rule.evaluate(_llm_ctx(), state) is False


class TestToolDenylistRule:
    def test_blocks_listed_tool(self):
        rule = ToolDenylistRule(tools=["web_search", "browser"])
        state = PolicyState()
        assert rule.evaluate(_tool_ctx(tool_name="web_search"), state) is True
        assert rule.evaluate(_tool_ctx(tool_name="terminal"), state) is False

    def test_empty_list_blocks_nothing(self):
        rule = ToolDenylistRule(tools=[])
        state = PolicyState()
        assert rule.evaluate(_tool_ctx(tool_name="terminal"), state) is False


class TestRateLimitRule:
    def test_allows_under_limit(self):
        rule = RateLimitRule(max_calls=3, window_seconds=60)
        state = PolicyState()
        ctx = _tool_ctx()
        # First 3 calls should not trigger
        assert rule.evaluate(ctx, state) is False
        state.append_list(f"rate:{ctx.agent_name}:{ctx.session_id}", time.time())
        assert rule.evaluate(ctx, state) is False
        state.append_list(f"rate:{ctx.agent_name}:{ctx.session_id}", time.time())
        assert rule.evaluate(ctx, state) is False

    def test_blocks_at_limit(self):
        rule = RateLimitRule(max_calls=2, window_seconds=60)
        state = PolicyState()
        ctx = _tool_ctx()
        key = f"rate:{ctx.agent_name}:{ctx.session_id}"
        now = time.time()
        state.set_list(key, [now, now])
        assert rule.evaluate(ctx, state) is True

    def test_expired_entries_dont_count(self):
        rule = RateLimitRule(max_calls=2, window_seconds=1)
        state = PolicyState()
        ctx = _tool_ctx()
        key = f"rate:{ctx.agent_name}:{ctx.session_id}"
        # Old timestamps — outside the window
        state.set_list(key, [time.time() - 10, time.time() - 10])
        assert rule.evaluate(ctx, state) is False


class TestRegexRule:
    def test_matches_message(self):
        rule = RegexRule(field_name="message", pattern=r"rm\s+-rf")
        state = PolicyState()
        ctx = _llm_ctx(message="rm -rf /")
        assert rule.evaluate(ctx, state) is True

    def test_no_match(self):
        rule = RegexRule(field_name="message", pattern=r"rm\s+-rf")
        state = PolicyState()
        ctx = _llm_ctx(message="list files please")
        assert rule.evaluate(ctx, state) is False

    def test_empty_pattern(self):
        rule = RegexRule(field_name="message", pattern="")
        state = PolicyState()
        assert rule.evaluate(_llm_ctx(), state) is False


class TestAlwaysRule:
    def test_always_matches(self):
        rule = AlwaysRule()
        state = PolicyState()
        assert rule.evaluate(_tool_ctx(), state) is True
        assert rule.evaluate(_llm_ctx(), state) is True


class TestTokenBudgetRule:
    def test_under_budget(self):
        rule = TokenBudgetRule(max_tokens=1000)
        state = PolicyState()
        assert rule.evaluate(_llm_ctx(), state) is False

    def test_over_budget(self):
        rule = TokenBudgetRule(max_tokens=10)
        state = PolicyState()
        state.increment("tokens:sess-001", 5000)
        assert rule.evaluate(_llm_ctx(session="sess-001"), state) is True


# ── PolicyEngine ─────────────────────────────────────────────────────────────


class TestPolicyEngine:
    def test_empty_engine_allows_everything(self):
        engine = PolicyEngine()
        result = engine.evaluate(_tool_ctx())
        assert result.allowed

    def test_deny_policy(self):
        engine = PolicyEngine()
        engine.add_policy(Policy(
            name="block-web",
            scope=PolicyScope(tool="web_search"),
            rule=ToolDenylistRule(tools=["web_search"]),
            action=PolicyAction.DENY,
        ))
        result = engine.evaluate(_tool_ctx(tool_name="web_search"))
        assert result.denied
        assert "block-web" in result.matched_policies

    def test_deny_does_not_affect_other_tools(self):
        engine = PolicyEngine()
        engine.add_policy(Policy(
            name="block-web",
            scope=PolicyScope(tool="web_search"),
            rule=ToolDenylistRule(tools=["web_search"]),
            action=PolicyAction.DENY,
        ))
        result = engine.evaluate(_tool_ctx(tool_name="terminal"))
        assert result.allowed

    def test_allow_short_circuits(self):
        engine = PolicyEngine()
        engine.add_policy(Policy(
            name="always-allow-terminal",
            scope=PolicyScope(tool="terminal"),
            rule=AlwaysRule(),
            action=PolicyAction.ALLOW,
            priority=1,
        ))
        engine.add_policy(Policy(
            name="block-everything",
            rule=AlwaysRule(),
            action=PolicyAction.DENY,
            priority=100,
        ))
        # terminal should be allowed (short-circuit before deny)
        result = engine.evaluate(_tool_ctx(tool_name="terminal"))
        assert result.allowed
        assert "always-allow-terminal" in result.matched_policies

    def test_priority_ordering(self):
        engine = PolicyEngine()
        # Add in reverse priority order
        engine.add_policy(Policy(
            name="low-priority",
            rule=AlwaysRule(),
            action=PolicyAction.LOG,
            priority=100,
        ))
        engine.add_policy(Policy(
            name="high-priority",
            rule=AlwaysRule(),
            action=PolicyAction.DENY,
            priority=1,
        ))
        # High priority (1) runs first and denies
        result = engine.evaluate(_tool_ctx())
        assert result.denied
        assert result.matched_policies[0] == "high-priority"

    def test_remove_policy(self):
        engine = PolicyEngine()
        engine.add_policy(Policy(name="test", rule=AlwaysRule()))
        assert engine.remove_policy("test") is True
        assert engine.remove_policy("nonexistent") is False

    def test_enable_disable(self):
        engine = PolicyEngine()
        engine.add_policy(Policy(
            name="toggle",
            rule=AlwaysRule(),
            action=PolicyAction.DENY,
        ))
        # Enabled by default — should deny
        result = engine.evaluate(_tool_ctx())
        assert result.denied

        # Disable — should allow
        engine.disable("toggle")
        result = engine.evaluate(_tool_ctx())
        assert result.allowed

        # Re-enable — should deny again
        engine.enable("toggle")
        result = engine.evaluate(_tool_ctx())
        assert result.denied

    def test_violations_recorded(self):
        engine = PolicyEngine()
        engine.add_policy(Policy(
            name="blocker",
            rule=AlwaysRule(),
            action=PolicyAction.DENY,
        ))
        engine.evaluate(_tool_ctx(agent="Odin"))
        assert len(engine.violations) == 1
        assert engine.violations[0]["policy"] == "blocker"
        assert engine.violations[0]["agent"] == "Odin"

    def test_flag_action(self):
        engine = PolicyEngine()
        engine.add_policy(Policy(
            name="flagger",
            scope=PolicyScope(tool="web_search"),
            rule=AlwaysRule(),
            action=PolicyAction.FLAG,
        ))
        ctx = _tool_ctx(tool_name="web_search")
        result = engine.evaluate(ctx)
        assert result.allowed
        assert ctx.metadata.get("policy_flagged") is True
        assert "flagger" in ctx.metadata.get("flagged_by", [])

    def test_log_action_non_blocking(self):
        engine = PolicyEngine()
        engine.add_policy(Policy(
            name="logger",
            rule=AlwaysRule(),
            action=PolicyAction.LOG,
        ))
        result = engine.evaluate(_tool_ctx())
        assert result.allowed

    def test_to_dict(self):
        engine = PolicyEngine()
        engine.add_policy(Policy(
            name="test",
            description="Test policy",
            action=PolicyAction.DENY,
        ))
        d = engine.to_dict()
        assert d["policy_count"] == 1
        assert d["policies"][0]["name"] == "test"
        assert d["policies"][0]["action"] == "deny"

    def test_stats(self):
        engine = PolicyEngine()
        engine.add_policy(Policy(name="a", enabled=True, rule=AlwaysRule()))
        engine.add_policy(Policy(name="b", enabled=False, rule=AlwaysRule()))
        s = engine.stats()
        assert s["total_policies"] == 2
        assert s["enabled_policies"] == 1

    def test_llm_call_recording(self):
        engine = PolicyEngine()
        engine.add_policy(Policy(
            name="tracker",
            rule=AlwaysRule(),
            action=PolicyAction.LOG,
        ))
        engine.evaluate(_llm_ctx(message="test message"))
        # Token state should be recorded
        assert engine.state.get_counter("tokens:sess-001") > 0


# ── Hook Integration ─────────────────────────────────────────────────────────


class TestPolicyEngineHookIntegration:
    def setup_method(self) -> None:
        # Clean up hook registry before each test
        get_hook_registry().clear()

    def teardown_method(self) -> None:
        get_hook_registry().clear()

    def test_activate_registers_hooks(self):
        engine = PolicyEngine()
        engine.activate()
        assert engine.is_active
        # Should have registered 2 hooks (pre_tool + pre_llm)
        registry = get_hook_registry()
        assert registry.hook_count >= 2
        engine.deactivate()

    def test_deactivate_removes_hooks(self):
        engine = PolicyEngine()
        engine.activate()
        initial_count = get_hook_registry().hook_count
        engine.deactivate()
        assert not engine.is_active
        assert get_hook_registry().hook_count < initial_count

    def test_policy_blocks_via_hook(self):
        engine = PolicyEngine()
        engine.add_policy(Policy(
            name="block-web",
            rule=ToolDenylistRule(tools=["web_search"]),
            action=PolicyAction.DENY,
        ))
        engine.activate()

        # Fire the hook registry with a blocked tool
        ctx = _tool_ctx(tool_name="web_search")
        result = get_hook_registry().fire(ctx)
        assert result is None  # Hook chain aborted

        # Fire with an allowed tool
        ctx2 = _tool_ctx(tool_name="terminal")
        result2 = get_hook_registry().fire(ctx2)
        assert result2 is not None  # Hook chain passed

        engine.deactivate()

    def test_policy_allows_llm_calls(self):
        engine = PolicyEngine()
        engine.add_policy(Policy(
            name="block-terminal",
            rule=ToolDenylistRule(tools=["terminal"]),
            action=PolicyAction.DENY,
        ))
        engine.activate()

        # LLM calls should pass (tool filter doesn't apply)
        ctx = _llm_ctx(message="hello")
        result = get_hook_registry().fire(ctx)
        assert result is not None

        engine.deactivate()

    def test_double_activate_idempotent(self):
        engine = PolicyEngine()
        engine.activate()
        count1 = get_hook_registry().hook_count
        engine.activate()  # Should be no-op
        count2 = get_hook_registry().hook_count
        assert count1 == count2
        engine.deactivate()


# ── Integration scenario ─────────────────────────────────────────────────────


class TestPolicyEngineScenarios:
    """End-to-end scenarios combining multiple policies."""

    def setup_method(self) -> None:
        get_hook_registry().clear()

    def teardown_method(self) -> None:
        get_hook_registry().clear()

    def test_tool_allowlist_per_agent(self):
        """Odin can only use terminal + read_file; Mimir can use everything."""
        engine = PolicyEngine()
        engine.add_policy(Policy(
            name="odin-restricted",
            scope=PolicyScope(agent="Odin"),
            rule=ToolAllowlistRule(tools=["terminal", "read_file"]),
            action=PolicyAction.DENY,
        ))

        # Odin with allowed tool
        result = engine.evaluate(_tool_ctx(tool_name="terminal", agent="Odin"))
        assert result.allowed

        # Odin with blocked tool
        result = engine.evaluate(_tool_ctx(tool_name="web_search", agent="Odin"))
        assert result.denied

        # Mimir can use web_search (no policy for Mimir)
        result = engine.evaluate(_tool_ctx(tool_name="web_search", agent="Mimir"))
        assert result.allowed

    def test_rate_limit_per_agent(self):
        """Rate limit all agents to 2 tool calls per 60s."""
        engine = PolicyEngine()
        engine.add_policy(Policy(
            name="rate-limit",
            rule=RateLimitRule(max_calls=2, window_seconds=60),
            action=PolicyAction.DENY,
        ))

        ctx = _tool_ctx(agent="Odin", session="s1")

        # First 2 calls allowed
        assert engine.evaluate(ctx).allowed
        assert engine.evaluate(ctx).allowed

        # Third call denied
        assert engine.evaluate(ctx).denied

    def test_audit_log_and_deny_combo(self):
        """Audit log everything + deny dangerous commands."""
        engine = PolicyEngine()
        engine.add_policy(Policy(
            name="audit-all",
            rule=AlwaysRule(),
            action=PolicyAction.LOG,
            priority=100,
        ))
        engine.add_policy(Policy(
            name="block-dangerous",
            rule=RegexRule(field_name="message", pattern=r"rm\s+-rf\s+/"),
            action=PolicyAction.DENY,
            priority=1,
        ))

        # Normal message passes
        ctx = _llm_ctx(message="list files")
        result = engine.evaluate(ctx)
        assert result.allowed

        # Dangerous message blocked
        ctx2 = _llm_ctx(message="rm -rf /")
        result2 = engine.evaluate(ctx2)
        assert result2.denied
        assert len(engine.violations) == 1


# ── YAML / dict loading ──────────────────────────────────────────────────────


class TestPolicyEngineFromDict:
    """Loading policies from dicts / YAML declarations."""

    def test_from_dict_empty(self) -> None:
        engine = PolicyEngine.from_dict({})
        assert engine.list_policies() == []

    def test_from_dict_tool_allowlist(self) -> None:
        data = {
            "policies": [
                {
                    "name": "odin-safe-tools",
                    "description": "Only safe tools",
                    "priority": 10,
                    "action": "deny",
                    "scope": {"agent": "Odin"},
                    "rule": {
                        "type": "tool_allowlist",
                        "tools": ["terminal", "read_file"],
                    },
                }
            ]
        }
        engine = PolicyEngine.from_dict(data)
        policies = engine.list_policies()
        assert len(policies) == 1
        p = policies[0]
        assert p.name == "odin-safe-tools"
        assert p.priority == 10
        assert p.action == PolicyAction.DENY
        assert p.scope.agent == "Odin"
        assert isinstance(p.rule, ToolAllowlistRule)
        assert sorted(p.rule.tools) == ["read_file", "terminal"]

    def test_from_dict_tool_denylist(self) -> None:
        data = {
            "policies": [
                {
                    "name": "no-dangerous-shell",
                    "rule": {
                        "type": "tool_denylist",
                        "tools": ["shell_exec", "system"],
                    },
                    "action": "deny",
                }
            ]
        }
        engine = PolicyEngine.from_dict(data)
        ctx = _tool_ctx(tool_name="shell_exec")
        result = engine.evaluate(ctx)
        assert result.denied

    def test_from_dict_rate_limit(self) -> None:
        data = {
            "policies": [
                {
                    "name": "global-rate-2",
                    "rule": {
                        "type": "rate_limit",
                        "max_calls": 2,
                        "window_seconds": 60.0,
                    },
                    "action": "deny",
                    "priority": 100,
                }
            ]
        }
        engine = PolicyEngine.from_dict(data)
        ctx = _tool_ctx()
        # First 2 calls allowed
        for _ in range(2):
            assert engine.evaluate(ctx).allowed
        # 3rd call should be denied
        result = engine.evaluate(ctx)
        assert result.denied

    def test_from_dict_token_budget(self) -> None:
        data = {
            "policies": [
                {
                    "name": "budget-100",
                    "rule": {"type": "token_budget", "max_tokens": 100},
                    "action": "deny",
                }
            ]
        }
        engine = PolicyEngine.from_dict(data)
        # Manually increment the token counter for this session
        ctx = _llm_ctx()
        engine.state.increment(f"tokens:{ctx.session_id}", amount=150)
        result = engine.evaluate(ctx)
        assert result.denied

    def test_from_dict_regex(self) -> None:
        data = {
            "policies": [
                {
                    "name": "block-rce",
                    "rule": {
                        "type": "regex",
                        "field_name": "message",
                        "pattern": r"rm\s+-rf",
                    },
                    "action": "deny",
                }
            ]
        }
        engine = PolicyEngine.from_dict(data)
        ctx = _llm_ctx(message="please rm -rf /tmp")
        assert engine.evaluate(ctx).denied
        assert engine.evaluate(_llm_ctx(message="safe message")).allowed

    def test_from_dict_always_rule_default(self) -> None:
        data = {"policies": [{"name": "audit-everything"}]}
        engine = PolicyEngine.from_dict(data)
        p = engine.list_policies()[0]
        assert isinstance(p.rule, AlwaysRule)
        assert p.action == PolicyAction.LOG
        assert p.enabled is True
        assert p.priority == 50

    def test_from_dict_scope_empty_means_any(self) -> None:
        data = {
            "policies": [
                {"name": "global", "rule": {"type": "always"}, "action": "flag"}
            ]
        }
        engine = PolicyEngine.from_dict(data)
        p = engine.list_policies()[0]
        # Empty scope -> matches everything
        assert p.scope.agent == ""
        assert p.scope.tool == ""
        assert p.scope.matches(_tool_ctx(agent="Anyone", tool_name="any_tool"))

    def test_from_dict_scope_any_sentinel(self) -> None:
        """`*`, `any`, `all` should be treated as empty / match-all."""
        data = {
            "policies": [
                {
                    "name": "p",
                    "scope": {"agent": "*", "tool": "any", "session": "all"},
                    "rule": {"type": "always"},
                    "action": "log",
                }
            ]
        }
        engine = PolicyEngine.from_dict(data)
        p = engine.list_policies()[0]
        assert p.scope.agent == ""
        assert p.scope.tool == ""
        assert p.scope.session == ""

    def test_from_dict_disabled(self) -> None:
        data = {
            "policies": [
                {
                    "name": "off",
                    "enabled": False,
                    "rule": {"type": "always"},
                    "action": "deny",
                }
            ]
        }
        engine = PolicyEngine.from_dict(data)
        # Disabled policy should not affect evaluation
        result = engine.evaluate(_tool_ctx())
        assert result.allowed

    def test_from_dict_invalid_rule_type_raises(self) -> None:
        data = {"policies": [{"name": "bad", "rule": {"type": "mystery_rule"}}]}
        with pytest.raises(ValueError, match="Unknown rule type"):
            PolicyEngine.from_dict(data)

    def test_from_dict_missing_name_raises(self) -> None:
        data = {"policies": [{"rule": {"type": "always"}, "action": "log"}]}
        with pytest.raises(ValueError, match="missing required key 'name'"):
            PolicyEngine.from_dict(data)

    def test_from_dict_invalid_action_raises(self) -> None:
        data = {
            "policies": [
                {
                    "name": "x",
                    "rule": {"type": "always"},
                    "action": "obliterate",
                }
            ]
        }
        with pytest.raises(ValueError, match="Unknown action"):
            PolicyEngine.from_dict(data)


class TestPolicyEngineFromYaml:
    """Loading policies from YAML files / strings."""

    def test_from_yaml_string(self) -> None:
        yaml_text = (
            "policies:\n"
            "  - name: yml-test\n"
            "    rule:\n"
            "      type: tool_allowlist\n"
            "      tools: [terminal]\n"
            "    action: deny\n"
        )
        engine = PolicyEngine.from_yaml(yaml_text)
        policies = engine.list_policies()
        assert len(policies) == 1
        assert policies[0].name == "yml-test"
        assert isinstance(policies[0].rule, ToolAllowlistRule)

    def test_from_yaml_file(self, tmp_path) -> None:
        path = tmp_path / "policies.yaml"
        path.write_text(
            "policies:\n"
            "  - name: from-file\n"
            "    action: log\n"
            "    rule:\n"
            "      type: always\n"
        )
        engine = PolicyEngine.from_yaml(path)
        assert engine.list_policies()[0].name == "from-file"

    def test_from_yaml_empty(self) -> None:
        # Empty YAML -> empty engine
        engine = PolicyEngine.from_yaml("{}\n")
        assert engine.list_policies() == []

    def test_from_yaml_invalid_root_raises(self) -> None:
        # Root must be a mapping (list at top is invalid)
        with pytest.raises(ValueError, match="root must be a mapping"):
            PolicyEngine.from_yaml("- just\n- a\n- list\n")


# ── CircuitBreakerRule ───────────────────────────────────────────────────────


class TestCircuitBreakerRule:
    """Circuit-breaker rule — denies all calls once violation threshold is hit."""

    def test_closed_does_not_match_by_default(self):
        rule = CircuitBreakerRule(max_violations=3)
        state = PolicyState()
        ctx = _tool_ctx()
        # No prior violations → CLOSED → no match
        assert rule.evaluate(ctx, state) is False

    def test_trips_after_max_violations(self):
        rule = CircuitBreakerRule(
            max_violations=3, window_seconds=60.0, cooldown_seconds=10.0
        )
        state = PolicyState()
        ctx = _tool_ctx()

        # Record violations one by one
        for _ in range(2):
            rule.record_violation(ctx, state)
            assert rule.evaluate(ctx, state) is False  # still CLOSED

        # Third violation triggers the trip
        rule.record_violation(ctx, state)
        assert rule.evaluate(ctx, state) is True  # OPEN

    def test_open_stays_open_during_cooldown(self):
        rule = CircuitBreakerRule(
            max_violations=2, window_seconds=60.0, cooldown_seconds=10.0
        )
        state = PolicyState()
        ctx = _tool_ctx()

        # Trip the breaker
        for _ in range(2):
            rule.record_violation(ctx, state)
        assert rule.evaluate(ctx, state) is True  # Just tripped

        # Subsequent calls during cooldown all deny
        for _ in range(5):
            assert rule.evaluate(ctx, state) is True

    def test_resets_after_cooldown(self):
        rule = CircuitBreakerRule(
            max_violations=2, window_seconds=60.0, cooldown_seconds=0.1
        )
        state = PolicyState()
        ctx = _tool_ctx()

        for _ in range(2):
            rule.record_violation(ctx, state)
        assert rule.evaluate(ctx, state) is True  # OPEN

        # Wait for cooldown
        time.sleep(0.15)

        # After cooldown → reset → CLOSED → no match
        assert rule.evaluate(ctx, state) is False

    def test_separate_agents_have_independent_breakers(self):
        rule = CircuitBreakerRule(max_violations=2, scope_key="agent")
        state = PolicyState()

        # Trip breaker for Odin
        odin_ctx = _tool_ctx(agent="Odin", session="s1")
        for _ in range(2):
            rule.record_violation(odin_ctx, state)
        assert rule.evaluate(odin_ctx, state) is True

        # Different agent should still be CLOSED
        mimir_ctx = _tool_ctx(agent="Mimir", session="s2")
        assert rule.evaluate(mimir_ctx, state) is False

    def test_per_agent_breaker_affects_all_sessions(self):
        rule = CircuitBreakerRule(max_violations=2, scope_key="agent")
        state = PolicyState()

        s1 = _tool_ctx(agent="Odin", session="s1")
        s2 = _tool_ctx(agent="Odin", session="s2")

        # Trip in session s1
        for _ in range(2):
            rule.record_violation(s1, state)
        # Same agent, different session — also denied
        assert rule.evaluate(s2, state) is True

    def test_per_session_breaker_independent_per_session(self):
        rule = CircuitBreakerRule(max_violations=2, scope_key="session")
        state = PolicyState()

        s1 = _tool_ctx(agent="Odin", session="s1")
        s2 = _tool_ctx(agent="Mimir", session="s1")  # same session, different agent

        # Trip session s1
        for _ in range(2):
            rule.record_violation(s1, state)

        # Same session but different agent — still denied (scope=session)
        assert rule.evaluate(s2, state) is True

    def test_old_violations_drop_out_of_window(self):
        rule = CircuitBreakerRule(
            max_violations=3, window_seconds=0.1, cooldown_seconds=10.0
        )
        state = PolicyState()
        ctx = _tool_ctx()

        # Two old violations — outside the window
        state.set_list(
            f"cb:agent_session:{ctx.agent_name}:{ctx.session_id}:violations",
            [time.time() - 1.0, time.time() - 1.0],
        )

        # Even though max_violations is 3, only recent count matters
        assert rule.evaluate(ctx, state) is False


class TestCircuitBreakerWithPolicyEngine:
    """End-to-end: CircuitBreakerRule wired into a PolicyEngine."""

    def test_engine_denies_after_threshold_violations(self):
        engine = PolicyEngine()
        # Trip after 2 denials
        engine.add_policy(
            Policy(
                name="breaker",
                scope=PolicyScope(),
                rule=CircuitBreakerRule(
                    max_violations=2,
                    window_seconds=60.0,
                    cooldown_seconds=10.0,
                ),
                action=PolicyAction.DENY,
                priority=1,
            )
        )

        ctx = _tool_ctx()

        # First call — closed, allowed
        assert engine.evaluate(ctx).allowed

        # Trip after recording
        engine.evaluate(ctx)  # 2nd evaluation → records violation → trips
        # Now in cooldown — next call is denied
        result = engine.evaluate(ctx)
        assert result.denied
        assert "breaker" in result.matched_policies

    def test_engine_isolation_between_agents(self):
        engine = PolicyEngine()
        engine.add_policy(
            Policy(
                name="breaker",
                scope=PolicyScope(),
                rule=CircuitBreakerRule(
                    max_violations=2,
                    window_seconds=60.0,
                    cooldown_seconds=10.0,
                    scope_key="agent",
                ),
                action=PolicyAction.DENY,
                priority=1,
            )
        )

        # Trip Odin
        odin = _tool_ctx(agent="Odin")
        engine.evaluate(odin)
        engine.evaluate(odin)
        # Now Odin is OPEN
        assert engine.evaluate(odin).denied

        # Mimir is unaffected
        mimir = _tool_ctx(agent="Mimir")
        assert engine.evaluate(mimir).allowed

    def test_engine_resets_state_on_cooldown_elapsed(self):
        engine = PolicyEngine()
        engine.add_policy(
            Policy(
                name="breaker",
                scope=PolicyScope(),
                rule=CircuitBreakerRule(
                    max_violations=2,
                    window_seconds=60.0,
                    cooldown_seconds=0.1,  # very short
                ),
                action=PolicyAction.DENY,
                priority=1,
            )
        )

        ctx = _tool_ctx()
        engine.evaluate(ctx)
        engine.evaluate(ctx)
        # Now in cooldown
        assert engine.evaluate(ctx).denied

        # Wait for cooldown
        time.sleep(0.15)

        # After cooldown → CLOSED → allowed
        assert engine.evaluate(ctx).allowed

    def test_from_dict_circuit_breaker(self):
        data = {
            "policies": [
                {
                    "name": "global-circuit-breaker",
                    "priority": 1,
                    "action": "deny",
                    "scope": {},
                    "rule": {
                        "type": "circuit_breaker",
                        "max_violations": 2,
                        "window_seconds": 60.0,
                        "cooldown_seconds": 30.0,
                    },
                }
            ]
        }
        engine = PolicyEngine.from_dict(data)
        policies = engine.list_policies()
        assert len(policies) == 1
        assert policies[0].name == "global-circuit-breaker"
        assert isinstance(policies[0].rule, CircuitBreakerRule)
        assert policies[0].rule.max_violations == 2
        assert policies[0].rule.window_seconds == 60.0
        assert policies[0].rule.cooldown_seconds == 30.0

    def test_from_yaml_circuit_breaker(self):
        yaml_text = (
            "policies:\n"
            "  - name: cb-rule\n"
            "    rule:\n"
            "      type: circuit_breaker\n"
            "      max_violations: 5\n"
            "      window_seconds: 30.0\n"
            "      cooldown_seconds: 60.0\n"
            "      scope_key: agent\n"
            "    action: deny\n"
            "    priority: 5\n"
        )
        engine = PolicyEngine.from_yaml(yaml_text)
        rule = engine.list_policies()[0].rule
        assert isinstance(rule, CircuitBreakerRule)
        assert rule.max_violations == 5
        assert rule.window_seconds == 30.0
        assert rule.cooldown_seconds == 60.0
        assert rule.scope_key == "agent"

    def test_violation_recorded_only_when_rule_matches(self):
        """If the rule itself doesn't match (no violations yet), no trip happens."""
        rule = CircuitBreakerRule(max_violations=2)
        state = PolicyState()
        ctx = _tool_ctx()
        # No violations recorded, evaluate returns False
        assert rule.evaluate(ctx, state) is False
        # State should remain empty
        assert state.get_list(
            f"cb:agent_session:{ctx.agent_name}:{ctx.session_id}:violations"
        ) == []
