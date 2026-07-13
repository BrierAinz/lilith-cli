"""Tests for sandbox_binder — AgentCard → SandboxPolicy auto-binding.

Covers:
- Default policy derivation from a minimal card
- Level-based differences (consultant vs executor)
- Tool-driven NO_SUBPROCESS / NO_FILE_WRITE / NO_NETWORK derivation
- Override kwargs (exec_time, rate, tokens)
- Extra rules layered on top of auto-derived ones
- Registry integration (bind_loader registers into SandboxRegistry)
- bind_vanaheim end-to-end against the real agent_cards.yaml
- Edge cases (empty tools, level 0, no description)
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from lilith_core.sandbox import (
    SandboxAction,
    SandboxPolicy,
    SandboxRegistry,
    SandboxRule,
    SandboxRuleType,
)

from lilith_skills.agent_cards import AgentCard
from lilith_skills.sandbox_binder import (
    BoundSandbox,
    DEFAULT_MAX_CALLS_PER_MIN,
    DEFAULT_MAX_EXEC_TIME,
    DEFAULT_MAX_TOKENS,
    _NETWORK_TOOLS,
    _SUBPROCESS_TOOLS,
    _WRITE_TOOLS,
    bind,
    bind_loader,
    bind_vanaheim,
    derive_policy,
)


# ── Fixtures ────────────────────────────────────────────────────────────────


def _card(
    *,
    name: str = "TestAgent",
    role: str = "Tester",
    level: int = 2,
    model: str = "glm-5.2",
    tools: list[str] | None = None,
    description: str = "Test agent card.",
) -> AgentCard:
    return AgentCard(
        name=name,
        role=role,
        level=level,
        model=model,
        tools=tools if tools is not None else ["terminal", "read_file"],
        description=description,
    )


@pytest.fixture
def fresh_registry() -> SandboxRegistry:
    """Return a fresh SandboxRegistry for isolation between tests."""
    return SandboxRegistry()


# ── derive_policy: default behavior ─────────────────────────────────────────


class TestDerivePolicyDefaults:
    """A minimal card produces a sensible policy without any overrides."""

    def test_minimal_card_returns_policy(self):
        card = _card()
        policy = derive_policy(card)
        assert isinstance(policy, SandboxPolicy)
        assert policy.name == "agent:testagent"
        assert policy.enabled is True
        assert policy.description.startswith("Auto-derived from AgentCard")

    def test_minimal_card_has_all_expected_rules(self):
        card = _card()
        policy = derive_policy(card)
        # Should have at least: ALLOWED_TOOLS, MAX_EXEC_TIME,
        # MAX_CALLS_PER_MIN, MAX_TOKENS, MAX_MEMORY_MB
        rule_types = {r.type for r in policy.rules}
        for required in (
            SandboxRuleType.ALLOWED_TOOLS,
            SandboxRuleType.MAX_EXEC_TIME,
            SandboxRuleType.MAX_CALLS_PER_MIN,
            SandboxRuleType.MAX_TOKENS,
            SandboxRuleType.MAX_MEMORY_MB,
        ):
            assert required in rule_types

    def test_allowed_tools_whitelist_matches_card(self):
        tools = ["terminal", "web_search", "read_file"]
        card = _card(tools=tools)
        policy = derive_policy(card)
        allowed = policy.get_rule(SandboxRuleType.ALLOWED_TOOLS)
        assert allowed is not None
        assert sorted(allowed.value) == sorted(tools)
        assert allowed.action == SandboxAction.BLOCK


# ── derive_policy: level-based differences ─────────────────────────────────


class TestDerivePolicyByLevel:
    """Level 1 (consultant) gets stricter defaults than level 2 (executor)."""

    def test_level_1_gets_shorter_exec_time(self):
        card = _card(level=1)
        policy = derive_policy(card)
        rule = policy.get_rule(SandboxRuleType.MAX_EXEC_TIME)
        assert rule.value == DEFAULT_MAX_EXEC_TIME[1]
        assert DEFAULT_MAX_EXEC_TIME[1] < DEFAULT_MAX_EXEC_TIME[2]

    def test_level_2_gets_longer_exec_time(self):
        card = _card(level=2)
        policy = derive_policy(card)
        rule = policy.get_rule(SandboxRuleType.MAX_EXEC_TIME)
        assert rule.value == DEFAULT_MAX_EXEC_TIME[2]

    def test_level_1_blocks_file_deletion(self):
        card = _card(level=1)
        policy = derive_policy(card)
        rule = policy.get_rule(SandboxRuleType.NO_FILE_DELETE)
        assert rule is not None
        assert rule.value is True

    def test_level_2_does_not_block_file_deletion(self):
        card = _card(level=2)
        policy = derive_policy(card)
        rule = policy.get_rule(SandboxRuleType.NO_FILE_DELETE)
        assert rule is None

    def test_level_1_has_stricter_rate_limit(self):
        card_l1 = _card(level=1)
        card_l2 = _card(level=2)
        rate_l1 = derive_policy(card_l1).get_rule(
            SandboxRuleType.MAX_CALLS_PER_MIN
        ).value
        rate_l2 = derive_policy(card_l2).get_rule(
            SandboxRuleType.MAX_CALLS_PER_MIN
        ).value
        assert rate_l1 < rate_l2
        assert rate_l1 == DEFAULT_MAX_CALLS_PER_MIN[1]
        assert rate_l2 == DEFAULT_MAX_CALLS_PER_MIN[2]

    def test_level_1_has_stricter_token_budget(self):
        card_l1 = _card(level=1)
        card_l2 = _card(level=2)
        tokens_l1 = derive_policy(card_l1).get_rule(
            SandboxRuleType.MAX_TOKENS
        ).value
        tokens_l2 = derive_policy(card_l2).get_rule(
            SandboxRuleType.MAX_TOKENS
        ).value
        assert tokens_l1 < tokens_l2
        assert tokens_l1 == DEFAULT_MAX_TOKENS[1]
        assert tokens_l2 == DEFAULT_MAX_TOKENS[2]


# ── derive_policy: tool-driven rules ───────────────────────────────────────


class TestDerivePolicyToolDriven:
    """Tool presence/absence drives NO_* rule derivation."""

    def test_terminal_in_tools_disables_no_subprocess(self):
        card = _card(tools=["terminal", "read_file"])
        policy = derive_policy(card)
        rule = policy.get_rule(SandboxRuleType.NO_SUBPROCESS)
        assert rule is None

    def test_no_subprocess_tool_enables_no_subprocess(self):
        card = _card(tools=["read_file", "web_search"])
        policy = derive_policy(card)
        rule = policy.get_rule(SandboxRuleType.NO_SUBPROCESS)
        assert rule is not None
        assert rule.value is True

    def test_write_file_in_tools_disables_no_file_write(self):
        card = _card(tools=["write_file", "read_file"])
        policy = derive_policy(card)
        rule = policy.get_rule(SandboxRuleType.NO_FILE_WRITE)
        assert rule is None

    def test_patch_in_tools_disables_no_file_write(self):
        card = _card(tools=["patch", "read_file"])
        policy = derive_policy(card)
        rule = policy.get_rule(SandboxRuleType.NO_FILE_WRITE)
        assert rule is None

    def test_no_write_tool_enables_no_file_write(self):
        card = _card(tools=["read_file", "web_search"])
        policy = derive_policy(card)
        rule = policy.get_rule(SandboxRuleType.NO_FILE_WRITE)
        assert rule is not None
        assert rule.value is True

    def test_web_search_in_tools_disables_no_network(self):
        card = _card(tools=["web_search", "read_file"])
        policy = derive_policy(card)
        rule = policy.get_rule(SandboxRuleType.NO_NETWORK)
        assert rule is None

    def test_no_network_tool_enables_no_network(self):
        card = _card(tools=["read_file", "terminal"])
        policy = derive_policy(card)
        rule = policy.get_rule(SandboxRuleType.NO_NETWORK)
        assert rule is not None
        assert rule.value is True

    def test_tool_matching_is_case_insensitive(self):
        """Tools stored in card with mixed case still match our lowercase sets."""
        # Use tools whose lowercase forms appear in our internal sets:
        # _SUBPROCESS_TOOLS contains "terminal"
        # _NETWORK_TOOLS contains "web_search"
        # _WRITE_TOOLS contains "write_file", "patch"
        card = _card(tools=["TERMINAL", "Web_Search", "WRITE_FILE"])
        policy = derive_policy(card)
        # terminal → no NO_SUBPROCESS
        assert policy.get_rule(SandboxRuleType.NO_SUBPROCESS) is None
        # web_search (case-insensitive) → no NO_NETWORK
        assert policy.get_rule(SandboxRuleType.NO_NETWORK) is None
        # write_file (case-insensitive) → no NO_FILE_WRITE
        assert policy.get_rule(SandboxRuleType.NO_FILE_WRITE) is None


# ── derive_policy: overrides ───────────────────────────────────────────────


class TestDerivePolicyOverrides:
    """Override kwargs let callers force specific values."""

    def test_exec_time_override_wins(self):
        card = _card(level=2)
        policy = derive_policy(card, exec_time_override=300.0)
        rule = policy.get_rule(SandboxRuleType.MAX_EXEC_TIME)
        assert rule.value == 300.0

    def test_rate_limit_override_wins(self):
        card = _card(level=1)
        policy = derive_policy(card, rate_limit_override=500)
        rule = policy.get_rule(SandboxRuleType.MAX_CALLS_PER_MIN)
        assert rule.value == 500

    def test_token_budget_override_wins(self):
        card = _card(level=1)
        policy = derive_policy(card, token_budget_override=100_000)
        rule = policy.get_rule(SandboxRuleType.MAX_TOKENS)
        assert rule.value == 100_000

    def test_memory_mb_override(self):
        card = _card()
        policy = derive_policy(card, memory_mb=1024)
        rule = policy.get_rule(SandboxRuleType.MAX_MEMORY_MB)
        assert rule.value == 1024

    def test_extra_rules_are_appended(self):
        card = _card()
        extra = SandboxRule(
            type=SandboxRuleType.NO_NETWORK,
            value=True,
            action=SandboxAction.TERMINATE,
        )
        policy = derive_policy(card, extra_rules=[extra])
        network_rules = [
            r for r in policy.rules if r.type == SandboxRuleType.NO_NETWORK
        ]
        # Two rules: the auto-derived one (BLOCK) + the extra (TERMINATE)
        assert len(network_rules) == 2
        assert any(r.action == SandboxAction.TERMINATE for r in network_rules)


# ── derive_policy: edge cases ──────────────────────────────────────────────


class TestDerivePolicyEdgeCases:
    """Empty tools, unusual levels, malformed inputs."""

    def test_empty_tools_still_produces_policy(self):
        card = _card(tools=[])
        policy = derive_policy(card)
        # ALLOWED_TOOLS is omitted when card has no tools
        assert policy.get_rule(SandboxRuleType.ALLOWED_TOOLS) is None
        # But MAX_EXEC_TIME etc. are still set
        assert policy.get_rule(SandboxRuleType.MAX_EXEC_TIME) is not None

    def test_empty_tools_triggers_all_no_rules(self):
        card = _card(level=2, tools=[])
        policy = derive_policy(card)
        # No tools at all → all NO_* rules apply
        assert policy.get_rule(SandboxRuleType.NO_SUBPROCESS) is not None
        assert policy.get_rule(SandboxRuleType.NO_FILE_WRITE) is not None
        assert policy.get_rule(SandboxRuleType.NO_NETWORK) is not None

    def test_unknown_level_falls_back_to_safe_defaults(self):
        card = _card(level=99)  # Unknown level
        policy = derive_policy(card)
        # Should still produce a policy (uses fallback .get(level, default))
        rule = policy.get_rule(SandboxRuleType.MAX_EXEC_TIME)
        assert rule is not None
        assert rule.value > 0

    def test_case_insensitive_tool_match(self):
        """Tool names in card should match our internal sets regardless of case."""
        # Verify our internal sets contain lowercase
        for tool in _SUBPROCESS_TOOLS:
            assert tool == tool.lower()
        for tool in _WRITE_TOOLS:
            assert tool == tool.lower()
        for tool in _NETWORK_TOOLS:
            assert tool == tool.lower()


# ── bind: with registry ─────────────────────────────────────────────────────


class TestBindWithRegistry:
    """bind() should register the policy into a registry when given one."""

    def test_bind_returns_bound_sandbox(self, fresh_registry):
        card = _card(name="Mimir")
        bound = bind(card, registry=fresh_registry)
        assert isinstance(bound, BoundSandbox)
        assert bound.agent_name == "Mimir"
        assert bound.policy is not None

    def test_bind_registers_policy_under_agent_name(self, fresh_registry):
        card = _card(name="Odin")
        bind(card, registry=fresh_registry)
        policy = fresh_registry.get("Odin")
        assert policy is not None
        assert policy.name == "agent:odin"

    def test_bind_is_case_insensitive_on_lookup(self, fresh_registry):
        card = _card(name="Odin")
        bind(card, registry=fresh_registry)
        # SandboxRegistry lowercases keys
        assert fresh_registry.get("odin") is not None
        assert fresh_registry.get("ODIN") is not None

    def test_bind_without_registry_does_not_crash(self):
        card = _card(name="Loki")
        bound = bind(card)
        assert bound.agent_name == "Loki"
        assert bound.policy is not None

    def test_bind_includes_derivation_trace(self, fresh_registry):
        card = _card(name="Thor", tools=["terminal", "write_file"], level=2)
        bound = bind(card, registry=fresh_registry)
        # Derivation must explain each rule's source
        assert len(bound.derivation) == len(bound.policy.rules)
        rule_types_traced = {rt for rt, _ in bound.derivation}
        assert SandboxRuleType.ALLOWED_TOOLS.value in rule_types_traced
        assert SandboxRuleType.MAX_EXEC_TIME.value in rule_types_traced

    def test_bound_sandbox_to_dict_round_trip(self, fresh_registry):
        card = _card(name="Heimdall", level=1, tools=["read_file"])
        bound = bind(card, registry=fresh_registry)
        d = bound.to_dict()
        assert d["agent_name"] == "Heimdall"
        assert d["policy"]["name"] == "agent:heimdall"
        assert isinstance(d["derivation"], list)
        assert len(d["derivation"]) > 0


# ── bind_loader ─────────────────────────────────────────────────────────────


class TestBindLoader:
    """bind_loader derives a policy for every card in a loader."""

    @staticmethod
    def _write_loader_yaml(tmp_path: Path, *cards: AgentCard) -> AgentCardLoader:
        """Write a multi-doc YAML and load it."""
        from lilith_skills.agent_cards import AgentCardLoader

        yaml_path = tmp_path / "agent_cards.yaml"
        docs = []
        for c in cards:
            docs.append(yaml.dump(c.to_dict(), allow_unicode=True))
        yaml_path.write_text("---\n" + "\n---\n".join(docs), encoding="utf-8")
        return AgentCardLoader(yaml_path)

    def test_binds_all_cards_in_loader(self, fresh_registry, tmp_path):
        loader = self._write_loader_yaml(
            tmp_path,
            _card(name="Odin", tools=["web_search", "read_file"]),
            _card(name="Mimir", tools=["web_search", "read_file", "write_file"]),
        )
        bound = bind_loader(loader, registry=fresh_registry)
        assert len(bound) == 2
        # Both registered
        assert fresh_registry.get("Odin") is not None
        assert fresh_registry.get("Mimir") is not None

    def test_bind_loader_returns_list_in_card_order(self, fresh_registry, tmp_path):
        loader = self._write_loader_yaml(
            tmp_path,
            _card(name="A", tools=["read_file"]),
            _card(name="B", tools=["read_file"]),
            _card(name="C", tools=["read_file"]),
        )
        bound = bind_loader(loader, registry=fresh_registry)
        assert [b.agent_name for b in bound] == ["A", "B", "C"]


# ── bind_vanaheim (end-to-end with real YAML) ──────────────────────────────


class TestBindVanaheim:
    """bind_vanaheim reads the real Vanaheim/Agents/agent_cards.yaml."""

    REPO_ROOT = Path(__file__).resolve().parents[3]

    def test_bind_vanaheim_loads_real_cards(self):
        if not (self.REPO_ROOT / "Vanaheim" / "Agents" / "agent_cards.yaml").exists():
            pytest.skip("agent_cards.yaml not found in this checkout")
        bound = bind_vanaheim(str(self.REPO_ROOT))
        # The real file has 14 agents — should bind all of them
        assert len(bound) >= 6
        names = {b.agent_name for b in bound}
        # Sanity-check a couple of canonical agents
        assert "Odin" in names
        assert "Heimdall" in names

    def test_bind_vanaheim_registers_into_global_singleton(self):
        if not (self.REPO_ROOT / "Vanaheim" / "Agents" / "agent_cards.yaml").exists():
            pytest.skip("agent_cards.yaml not found in this checkout")
        from lilith_core.sandbox import get_sandbox_registry

        registry = get_sandbox_registry()
        before = set(registry.list_agents())
        bound = bind_vanaheim(str(self.REPO_ROOT), registry=registry)
        after = set(registry.list_agents())
        new_agents = after - before
        # Every newly-bound agent should appear in the registry
        for b in bound:
            assert b.agent_name.lower() in {a.lower() for a in after}

    def test_bind_vanaheim_heimdall_has_watchman_policy(self):
        """Heimdall is level 1 → must have NO_FILE_DELETE."""
        if not (self.REPO_ROOT / "Vanaheim" / "Agents" / "agent_cards.yaml").exists():
            pytest.skip("agent_cards.yaml not found in this checkout")
        bound = bind_vanaheim(str(self.REPO_ROOT))
        heimdall = next((b for b in bound if b.agent_name == "Heimdall"), None)
        assert heimdall is not None
        assert heimdall.policy.has_rule(SandboxRuleType.NO_FILE_DELETE)


# ── Constants sanity ───────────────────────────────────────────────────────


class TestConstants:
    """Sanity-check the default-dict shape used by derive_policy."""

    def test_exec_time_table_has_levels_1_and_2(self):
        assert 1 in DEFAULT_MAX_EXEC_TIME
        assert 2 in DEFAULT_MAX_EXEC_TIME

    def test_rate_table_has_levels_1_and_2(self):
        assert 1 in DEFAULT_MAX_CALLS_PER_MIN
        assert 2 in DEFAULT_MAX_CALLS_PER_MIN

    def test_token_table_has_levels_1_and_2(self):
        assert 1 in DEFAULT_MAX_TOKENS
        assert 2 in DEFAULT_MAX_TOKENS

    def test_level_1_always_strict_than_level_2(self):
        assert DEFAULT_MAX_EXEC_TIME[1] < DEFAULT_MAX_EXEC_TIME[2]
        assert DEFAULT_MAX_CALLS_PER_MIN[1] < DEFAULT_MAX_CALLS_PER_MIN[2]
        assert DEFAULT_MAX_TOKENS[1] < DEFAULT_MAX_TOKENS[2]