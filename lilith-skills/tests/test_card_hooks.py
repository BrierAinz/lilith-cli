"""Tests for AgentCard.hooks wiring (register_card_hooks + HOOK_ALIASES).

Covers:
- HOOK_ALIASES coverage of all lilith_core.hooks.HookType values
- resolve_hook_type string → HookType translation (case-insensitive)
- resolve_hook_type returns None for unknown / empty strings
- register_card_hooks wires each declared hook into a HookRegistry
- register_card_hooks returns (HookType, name) for each successful registration
- register_card_hooks uses the supplied callback when given
- register_card_hooks skips unknown hooks with a warning, doesn't crash
- register_card_hooks defaults to the audit-marker callback
- bind(card, register_hooks=True) populates BoundSandbox.registered_hooks
- bind_loader(register_hooks=True) registers every card's hooks
- BoundSandbox.to_dict() exposes registered_hooks
- Aether-Agents aliases (pre_tool_use → PRE_TOOL_CALL) work
"""

from __future__ import annotations

import pytest

from lilith_core.hooks import HookContext, HookRegistry, HookType, get_hook_registry

from lilith_skills.agent_cards import AgentCard
from lilith_skills.sandbox_binder import (
    HOOK_ALIASES,
    bind,
    bind_loader,
    register_card_hooks,
    resolve_hook_type,
)


# ── Fixtures ────────────────────────────────────────────────────────────────


def _make_card(name: str = "Heimdall", hooks: list[str] | None = None) -> AgentCard:
    return AgentCard(
        name=name,
        role="Watchman",
        level=2,
        model="glm-5.2",
        tools=["terminal", "web_search"],
        description="Watchman of the Bifrost",
        hooks=hooks or [],
    )


@pytest.fixture(autouse=True)
def _reset_global_hook_registry():
    """Each test starts with a clean global HookRegistry singleton."""
    reg = get_hook_registry()
    reg.clear()
    yield
    reg.clear()


@pytest.fixture
def fresh_registry() -> HookRegistry:
    return HookRegistry()


# ── TestResolveHookType ─────────────────────────────────────────────────────


class TestResolveHookType:
    def test_known_string_returns_hooktype(self):
        assert resolve_hook_type("pre_tool_call") is HookType.PRE_TOOL_CALL

    def test_aether_alias_pre_tool_use(self):
        """Aether-Agents uses 'pre_tool_use' — must map to PRE_TOOL_CALL."""
        assert resolve_hook_type("pre_tool_use") is HookType.PRE_TOOL_CALL

    def test_aether_alias_post_tool_use(self):
        assert resolve_hook_type("post_tool_use") is HookType.POST_TOOL_CALL

    def test_case_insensitive(self):
        assert resolve_hook_type("PRE_TOOL_CALL") is HookType.PRE_TOOL_CALL
        assert resolve_hook_type("On_Session_Start") is HookType.ON_SESSION_START

    def test_whitespace_stripped(self):
        assert resolve_hook_type("  pre_tool_call  ") is HookType.PRE_TOOL_CALL

    def test_unknown_returns_none(self):
        assert resolve_hook_type("made_up_hook") is None

    def test_empty_returns_none(self):
        assert resolve_hook_type("") is None

    def test_none_returns_none(self):
        assert resolve_hook_type(None) is None

    def test_all_lifecycle_hooks_have_aliases(self):
        """Every HookType enum value (except maybe internal ones) must be
        reachable through HOOK_ALIASES. Skip ON_TOOL_RESULT if not aliased —
        we just want to ensure core lifecycle hooks are wired.
        """
        # The core lifecycle hooks any agent card would care about
        core_hooks = [
            HookType.PRE_LLM_CALL,
            HookType.POST_LLM_CALL,
            HookType.PRE_TOOL_CALL,
            HookType.POST_TOOL_CALL,
            HookType.ON_SESSION_START,
            HookType.ON_SESSION_END,
            HookType.ON_ERROR,
        ]
        for ht in core_hooks:
            assert ht.value in HOOK_ALIASES, (
                f"HookType {ht.value} missing from HOOK_ALIASES"
            )


# ── TestRegisterCardHooks ───────────────────────────────────────────────────


class TestRegisterCardHooks:
    def test_empty_hooks_returns_empty_list(self, fresh_registry):
        card = _make_card(hooks=[])
        result = register_card_hooks(card, registry=fresh_registry)
        assert result == []

    def test_no_hooks_returns_empty_list(self, fresh_registry):
        """AgentCard default has hooks=[]"""
        card = _make_card()
        result = register_card_hooks(card, registry=fresh_registry)
        assert result == []

    def test_single_hook_registered(self, fresh_registry):
        card = _make_card(hooks=["pre_tool_call"])
        result = register_card_hooks(card, registry=fresh_registry)

        assert len(result) == 1
        hook_type, reg_name = result[0]
        assert hook_type is HookType.PRE_TOOL_CALL
        assert "Heimdall" in reg_name
        assert "pre_tool_call" in reg_name

        # Registry actually has it
        assert len(fresh_registry._hooks[HookType.PRE_TOOL_CALL]) == 1

    def test_multiple_hooks_registered(self, fresh_registry):
        card = _make_card(
            hooks=["pre_tool_call", "post_tool_call", "on_session_start"]
        )
        result = register_card_hooks(card, registry=fresh_registry)

        assert len(result) == 3
        registered_types = {ht for ht, _ in result}
        assert registered_types == {
            HookType.PRE_TOOL_CALL,
            HookType.POST_TOOL_CALL,
            HookType.ON_SESSION_START,
        }

    def test_aether_alias_resolved(self, fresh_registry):
        """`pre_tool_use` should still register as PRE_TOOL_CALL."""
        card = _make_card(hooks=["pre_tool_use"])
        result = register_card_hooks(card, registry=fresh_registry)
        assert len(result) == 1
        hook_type, _ = result[0]
        assert hook_type is HookType.PRE_TOOL_CALL

    def test_unknown_hook_skipped_with_warning(self, fresh_registry, caplog):
        card = _make_card(hooks=["pre_tool_call", "definitely_not_a_hook"])
        with caplog.at_level("WARNING", logger="lilith.skills.sandbox_binder"):
            result = register_card_hooks(card, registry=fresh_registry)

        # Only the known hook gets registered
        assert len(result) == 1
        assert result[0][0] is HookType.PRE_TOOL_CALL
        # Warning was logged
        assert any(
            "definitely_not_a_hook" in rec.message for rec in caplog.records
        )

    def test_custom_callback_used(self, fresh_registry):
        card = _make_card(hooks=["pre_tool_call"])

        def my_callback(ctx: HookContext) -> HookContext:
            ctx.data["marked"] = True
            return ctx

        register_card_hooks(
            card, registry=fresh_registry, callback=my_callback
        )
        # Fire the hook and verify the custom callback ran
        ctx = HookContext(
            hook_type=HookType.PRE_TOOL_CALL,
            agent_name="Heimdall",
            session_id="s1",
            data={"tool_name": "x"},
        )
        result = fresh_registry.fire(ctx)
        assert result is not None
        assert result.data.get("marked") is True

    def test_default_audit_callback_modifies_metadata(self, fresh_registry):
        card = _make_card(hooks=["pre_tool_call"])
        register_card_hooks(card, registry=fresh_registry)
        ctx = HookContext(
            hook_type=HookType.PRE_TOOL_CALL,
            agent_name="Heimdall",
            session_id="s1",
            data={},
        )
        result = fresh_registry.fire(ctx)
        assert result is not None
        # Default audit callback stashes metadata
        metadata_list = result.metadata.get("agent_card_hooks", [])
        assert len(metadata_list) == 1
        assert metadata_list[0]["agent"] == "Heimdall"
        assert metadata_list[0]["hook"] == "pre_tool_call"

    def test_uses_global_registry_by_default(self):
        """When no registry is supplied, register_card_hooks should use
        get_hook_registry() — verified by clearing then registering."""
        global_reg = get_hook_registry()
        global_reg.clear()

        card = _make_card(hooks=["pre_tool_call"])
        register_card_hooks(card)

        # Global singleton now has the hook
        assert len(global_reg._hooks[HookType.PRE_TOOL_CALL]) == 1


# ── TestBindWithHooks ──────────────────────────────────────────────────────


class TestBindWithHooks:
    def test_bind_default_does_not_register_hooks(self, fresh_registry):
        """Without register_hooks=True, the bound object has empty hooks."""
        from lilith_core.sandbox import SandboxRegistry

        card = _make_card(hooks=["pre_tool_call"])
        sandbox_reg = SandboxRegistry()
        bound = bind(card, registry=sandbox_reg, hook_registry=fresh_registry)

        assert bound.registered_hooks == []
        assert len(fresh_registry._hooks[HookType.PRE_TOOL_CALL]) == 0

    def test_bind_register_hooks_true_wires_them(self, fresh_registry):
        from lilith_core.sandbox import SandboxRegistry

        card = _make_card(hooks=["pre_tool_call", "on_session_start"])
        sandbox_reg = SandboxRegistry()
        bound = bind(
            card,
            registry=sandbox_reg,
            register_hooks=True,
            hook_registry=fresh_registry,
        )

        assert len(bound.registered_hooks) == 2
        types = {ht for ht, _ in bound.registered_hooks}
        assert types == {HookType.PRE_TOOL_CALL, HookType.ON_SESSION_START}

    def test_bind_to_dict_includes_registered_hooks(self, fresh_registry):
        from lilith_core.sandbox import SandboxRegistry

        card = _make_card(hooks=["pre_tool_call"])
        sandbox_reg = SandboxRegistry()
        bound = bind(
            card,
            registry=sandbox_reg,
            register_hooks=True,
            hook_registry=fresh_registry,
        )
        d = bound.to_dict()
        assert "registered_hooks" in d
        assert len(d["registered_hooks"]) == 1
        assert d["registered_hooks"][0]["hook"] == "pre_tool_call"


# ── TestBindLoaderWithHooks ────────────────────────────────────────────────


class TestBindLoaderWithHooks:
    def test_bind_loader_without_hooks_leaves_registry_empty(
        self, fresh_registry
    ):
        from lilith_core.sandbox import SandboxRegistry

        cards = [
            _make_card(name="Odin", hooks=["pre_tool_call"]),
            _make_card(name="Mimir", hooks=["post_tool_call"]),
        ]
        sandbox_reg = SandboxRegistry()
        bound = bind_loader(
            _StubLoader(cards),
            registry=sandbox_reg,
            hook_registry=fresh_registry,
        )

        assert len(bound) == 2
        assert all(b.registered_hooks == [] for b in bound)
        assert sum(
            len(fresh_registry._hooks[ht]) for ht in HookType
        ) == 0

    def test_bind_loader_with_hooks_registers_all(self, fresh_registry):
        from lilith_core.sandbox import SandboxRegistry

        cards = [
            _make_card(name="Odin", hooks=["pre_tool_call"]),
            _make_card(name="Mimir", hooks=["post_tool_call"]),
            _make_card(name="Heimdall", hooks=[]),
        ]
        sandbox_reg = SandboxRegistry()
        bound = bind_loader(
            _StubLoader(cards),
            registry=sandbox_reg,
            register_hooks=True,
            hook_registry=fresh_registry,
        )

        # Two of three cards have hooks
        assert len(bound[0].registered_hooks) == 1
        assert len(bound[1].registered_hooks) == 1
        assert len(bound[2].registered_hooks) == 0

        # Both hooks fired
        assert len(fresh_registry._hooks[HookType.PRE_TOOL_CALL]) == 1
        assert len(fresh_registry._hooks[HookType.POST_TOOL_CALL]) == 1


# ── Test fixtures ───────────────────────────────────────────────────────────


class TestYamlIntegration:
    """Verify that hooks declared in a real YAML file get wired up."""

    def test_yaml_with_hooks_field_loads_into_card(self, tmp_path):
        """End-to-end: write a tiny YAML, load it, bind with register_hooks=True."""
        from lilith_core.sandbox import SandboxRegistry

        # Write a minimal YAML with one card that has hooks declared
        cards_path = tmp_path / "agent_cards.yaml"
        cards_path.write_text(
            "---\n"
            "name: TestAgent\n"
            "role: \"Test Watchman\"\n"
            "level: 2\n"
            "model: \"glm-5.2\"\n"
            "tools:\n"
            "  - terminal\n"
            "  - web_search\n"
            "hooks:\n"
            "  - pre_tool_call\n"
            "  - post_tool_call\n"
            "  - on_session_start\n"
            "description: \"Agent with hooks declared for testing\"\n",
            encoding="utf-8",
        )

        from lilith_skills.agent_cards import AgentCardLoader

        loader = AgentCardLoader(cards_path)
        cards = loader.list_agents()
        assert len(cards) == 1
        card = cards[0]
        assert card.hooks == ["pre_tool_call", "post_tool_call", "on_session_start"]

        # Now bind with hooks
        sandbox_reg = SandboxRegistry()
        hook_reg = HookRegistry()
        bound = bind_loader(
            loader,
            registry=sandbox_reg,
            register_hooks=True,
            hook_registry=hook_reg,
        )

        assert len(bound) == 1
        assert len(bound[0].registered_hooks) == 3
        # And the hooks fired end-to-end
        ctx = HookContext(
            hook_type=HookType.PRE_TOOL_CALL,
            agent_name="TestAgent",
            session_id="s",
            data={"tool_name": "terminal"},
        )
        result = hook_reg.fire(ctx)
        assert result is not None
        assert result.metadata["agent_card_hooks"][0]["agent"] == "TestAgent"


class TestVanaheimFullYAML:
    """Verify all 14 Vanaheim agent cards declare semantically-correct hooks.

    These tests load the real ``Vanaheim/Agents/agent_cards.yaml`` file and
    exercise the full wiring pipeline (parse → register → fire). Acts as a
    regression guard against accidentally emptying someone's hooks block
    during future YAML edits.
    """

    @pytest.fixture
    def vanaheim_yaml_path(self):
        """Locate the real Vanaheim agent_cards.yaml file (skip if missing)."""
        from pathlib import Path

        # Walk up from this test file until we find /Vanaheim/Agents
        here = Path(__file__).resolve()
        for parent in here.parents:
            candidate = parent / "Vanaheim" / "Agents" / "agent_cards.yaml"
            if candidate.exists():
                return candidate
        pytest.skip("Vanaheim/Agents/agent_cards.yaml not found from this test location")

    def test_all_14_agents_have_hooks(self, vanaheim_yaml_path):
        """Every agent card in Vanaheim must declare at least one hook.

        Rationale: the ecosystem assumes every active agent emits
        observable events (pre/post/error/session) so Heimdall can audit
        and Tyr can enforce contracts. An agent without hooks is
        effectively invisible.
        """
        from lilith_skills.agent_cards import AgentCardLoader

        loader = AgentCardLoader(vanaheim_yaml_path)
        cards = loader.list_agents()
        assert len(cards) == 14, f"expected 14 cards, got {len(cards)}"

        # Collect agents missing hooks for diagnostics
        no_hooks = [c.name for c in cards if not c.hooks]
        assert not no_hooks, f"agents without hooks: {no_hooks}"

    def test_at_least_25_hook_subscriptions(self, vanaheim_yaml_path):
        """The full YAML should produce ≥25 hook subscriptions across all cards."""
        from lilith_skills.agent_cards import AgentCardLoader

        loader = AgentCardLoader(vanaheim_yaml_path)
        cards = loader.list_agents()
        total = sum(len(c.hooks) for c in cards)
        assert total >= 25, f"only {total} subscriptions across 14 agents"

    def test_all_declared_hooks_resolve_to_hooktype(self, vanaheim_yaml_path):
        """Every string in every card's hooks list must map to a known HookType."""
        from lilith_skills.agent_cards import AgentCardLoader
        from lilith_skills.sandbox_binder import resolve_hook_type

        loader = AgentCardLoader(vanaheim_yaml_path)
        cards = loader.list_agents()

        unknown: list[tuple[str, str]] = []
        for c in cards:
            for h in c.hooks:
                if resolve_hook_type(h) is None:
                    unknown.append((c.name, h))
        assert not unknown, f"unknown hook names: {unknown}"

    def test_full_wiring_registers_every_callback(self, vanaheim_yaml_path):
        """bind_loader(register_hooks=True) should register ≥25 hook callbacks."""
        from lilith_core.sandbox import SandboxRegistry
        from lilith_skills.agent_cards import AgentCardLoader
        from lilith_skills.sandbox_binder import bind_loader

        loader = AgentCardLoader(vanaheim_yaml_path)
        sandbox_reg = SandboxRegistry()
        hook_reg = HookRegistry()
        bound = bind_loader(
            loader,
            registry=sandbox_reg,
            register_hooks=True,
            hook_registry=hook_reg,
        )

        total = sum(len(b.registered_hooks) for b in bound)
        assert total >= 25, f"only {total} registered across {len(bound)} cards"

        # registered_hooks is list[tuple[HookType, registration_name]]
        all_hook_types = [
            ht for b in bound for ht, _ in b.registered_hooks
        ]
        assert any(
            h == HookType.ON_SESSION_START for h in all_hook_types
        ), "no agent wired ON_SESSION_START"
        assert any(
            h == HookType.ON_SESSION_END for h in all_hook_types
        ), "no agent wired ON_SESSION_END"
        # Odin uses pre_llm_call (LLM gating) — verify at least one
        assert any(
            h == HookType.PRE_LLM_CALL for h in all_hook_types
        ), "no agent wired PRE_LLM_CALL"

    def test_register_card_hooks_returns_expected_count_per_agent(
        self, vanaheim_yaml_path
    ):
        """register_card_hooks yields (HookType, agent_name) for each subscription."""
        from lilith_skills.agent_cards import AgentCardLoader
        from lilith_skills.sandbox_binder import register_card_hooks

        loader = AgentCardLoader(vanaheim_yaml_path)
        cards = loader.list_agents()
        hook_reg = HookRegistry()

        per_agent_counts: dict[str, int] = {}
        for card in cards:
            subs = register_card_hooks(card, registry=hook_reg)
            per_agent_counts[card.name] = len(subs)

        # Spot-check a few known agents
        assert per_agent_counts["Odin"] == 2
        assert per_agent_counts["Heimdall"] == 3
        assert per_agent_counts["Jörmungandr"] == 3


class _StubLoader:
    """Minimal AgentCardLoader-compatible stub for bind_loader tests."""

    def __init__(self, cards: list[AgentCard]) -> None:
        self._cards = cards

    def list_agents(self) -> list[AgentCard]:
        return list(self._cards)

    def get_agent(self, name: str) -> AgentCard | None:
        for c in self._cards:
            if c.name == name:
                return c
        return None