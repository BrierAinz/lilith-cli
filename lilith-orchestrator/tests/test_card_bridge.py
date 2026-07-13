"""Tests for lilith_orchestrator.card_bridge — Vanaheim AgentCard →
SubAgentDefinition mapping (Fase 1.2a).

Covers:
- ``agent_type_for_card`` casefolds and preserves unicode diacritics
- ``card_to_subagent`` full field mapping per the closed spec
- card with no tools → ``[]`` (never the ``["*"]`` wildcard)
- tools are not canonicalized (case preserved verbatim)
- explicit parameter overrides on ``card_to_subagent``
- ``register_card_subagent`` overwrite=False respects existing entries
  (the 8 default personas must not be clobbered)
- ``register_card_subagent`` overwrite=True replaces
- ``register_loader_subagents`` returns the registered definitions and
  skips existing agent types when overwrite=False
- ``register_vanaheim_subagents`` registers all 14 Vanaheim cards and
  returns the definitions (real YAML; skipped when the file is absent)
- full mapping of a real card from the Vanaheim YAML
- unicode name ``Jörmungandr`` → ``jörmungandr`` round-trips through the
  registry
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lilith_orchestrator.subagents import (
    SubAgentDefinition,
    clear_registry,
    get_agent,
    register,
)
from lilith_orchestrator.card_bridge import (
    agent_type_for_card,
    card_to_subagent,
    register_card_subagent,
    register_loader_subagents,
    register_vanaheim_subagents,
)
from lilith_skills.agent_cards import AgentCard, AgentCardLoader


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_registry():
    """Each test gets a pristine sub-agent registry (mirrors test_subagents.py)."""
    clear_registry()
    yield
    clear_registry()


def _card(**overrides) -> AgentCard:
    """Build an AgentCard with sane Vanaheim-flavoured defaults."""
    base = {
        "name": "Odin",
        "role": "Allfather — Strategist & Oracle",
        "level": 2,
        "model": "glm-5.2",
        "tools": ["terminal", "web_search"],
        "description": "Orchestrator of the Aesir",
    }
    base.update(overrides)
    return AgentCard(**base)


@pytest.fixture
def vanaheim_yaml_path():
    """Locate the real Vanaheim/Agents/agent_cards.yaml (skip if absent).

    Matches the convention used in lilith-skills/tests/test_card_hooks.py:
    walk up from this test file until the canonical path is found.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "Vanaheim" / "Agents" / "agent_cards.yaml"
        if candidate.exists():
            return candidate
    pytest.skip("Vanaheim/Agents/agent_cards.yaml not found from this test location")


class _StubLoader:
    """Minimal AgentCardLoader-compatible stub (has list_agents())."""

    def __init__(self, cards: list[AgentCard]) -> None:
        self._cards = cards

    def list_agents(self) -> list[AgentCard]:
        return list(self._cards)


# ── agent_type_for_card ───────────────────────────────────────────────────────


class TestAgentTypeForCard:
    def test_casefolds_ascii(self):
        assert agent_type_for_card(_card(name="Odin")) == "odin"
        assert agent_type_for_card(_card(name="HEIMDALL")) == "heimdall"
        assert agent_type_for_card(_card(name="Mimir")) == "mimir"

    def test_preserves_unicode_diacritics(self):
        # casefold lowers case but keeps the combining character: Jörmungandr → jörmungandr
        card = _card(name="Jörmungandr")
        at = agent_type_for_card(card)
        assert at == "jörmungandr"
        assert "ö" in at
        # round-trips: re-casefolding is idempotent
        assert at == at.casefold()

    def test_german_sharp_s(self):
        # casefold is stronger than lower: "ß" → "ss"
        assert agent_type_for_card(_card(name="Straße")) == "strasse"


# ── card_to_subagent mapping ──────────────────────────────────────────────────


class TestCardToSubagent:
    def test_full_mapping(self):
        card = _card()
        defn = card_to_subagent(card)

        assert isinstance(defn, SubAgentDefinition)
        assert defn.agent_type == "odin"
        assert defn.system_prompt == (
            "Allfather — Strategist & Oracle\n\nOrchestrator of the Aesir"
        )
        assert defn.when_to_use == "Orchestrator of the Aesir"
        assert defn.allowed_tools == ["terminal", "web_search"]
        assert defn.disallowed_tools == []
        assert defn.model_preference == "glm-5.2"
        assert defn.max_concurrency is None
        assert defn.tags == ["vanaheim", "level:2", "Odin"]

    def test_no_tools_maps_to_empty_list_not_wildcard(self):
        defn = card_to_subagent(_card(tools=[]))
        # The SubAgentDefinition dataclass defaults allowed_tools to ["*"] —
        # the bridge must override that with [] for a tool-less card.
        assert defn.allowed_tools == []
        assert defn.allowed_tools != ["*"]

    def test_tools_not_canonicalized(self):
        # Case and order preserved verbatim (no .lower(), no sorting)
        defn = card_to_subagent(_card(tools=["Terminal", "WEB_Search", "patch"]))
        assert defn.allowed_tools == ["Terminal", "WEB_Search", "patch"]

    def test_explicit_param_overrides(self):
        card = _card()
        defn = card_to_subagent(
            card,
            agent_type="custom-key",
            allowed_tools=["read_file"],
            disallowed_tools=["terminal"],
            max_concurrency=2,
        )
        assert defn.agent_type == "custom-key"
        assert defn.allowed_tools == ["read_file"]
        assert defn.disallowed_tools == ["terminal"]
        assert defn.max_concurrency == 2

    def test_model_preference_none_when_empty(self):
        defn = card_to_subagent(_card(model=""))
        assert defn.model_preference is None

    def test_when_to_use_falls_back_to_role(self):
        card = _card(description="")
        defn = card_to_subagent(card)
        # description empty → when_to_use is the role
        assert defn.when_to_use == card.role
        # system_prompt = "role\n\n" .strip() → role
        assert defn.system_prompt == card.role

    def test_system_prompt_strips_when_both_empty(self):
        defn = card_to_subagent(_card(role="", description=""))
        assert defn.system_prompt == ""
        assert defn.when_to_use == ""

    def test_tags_include_level_and_name(self):
        defn = card_to_subagent(_card(name="Skadi", level=1))
        assert defn.tags == ["vanaheim", "level:1", "Skadi"]


# ── register_card_subagent ────────────────────────────────────────────────────


class TestRegisterCardSubagent:
    def test_fresh_returns_true_and_registers(self):
        card = _card()
        assert register_card_subagent(card, overwrite=False) is True
        defn = get_agent("odin")
        assert defn is not None
        assert defn.system_prompt == (
            "Allfather — Strategist & Oracle\n\nOrchestrator of the Aesir"
        )

    def test_overwrite_false_respects_existing(self):
        # Simulate a pre-existing persona under the same agent_type (e.g. one
        # of the 8 default personas, or a prior Vanaheim registration).
        existing = SubAgentDefinition(
            agent_type="odin",
            system_prompt="PRE-EXISTING — DO NOT CLOBBER",
        )
        register(existing)

        result = register_card_subagent(_card(), overwrite=False)

        assert result is False
        # The existing entry is untouched.
        assert get_agent("odin") is existing
        assert get_agent("odin").system_prompt == "PRE-EXISTING — DO NOT CLOBBER"

    def test_overwrite_true_replaces_existing(self):
        existing = SubAgentDefinition(
            agent_type="odin",
            system_prompt="PRE-EXISTING",
        )
        register(existing)

        result = register_card_subagent(_card(), overwrite=True)

        assert result is True
        after = get_agent("odin")
        assert after is not existing
        assert "Allfather" in after.system_prompt

    def test_does_not_register_hooks(self):
        # The bridge must leave hook wiring to sandbox_binder.bind_loader.
        # Concretely: the produced definition carries no hook metadata and
        # nothing besides the SubAgentDefinition is registered.
        register_card_subagent(_card(hooks=["pre_tool_call"]))
        defn = get_agent("odin")
        assert defn is not None
        # SubAgentDefinition has no hooks attribute — the card's hooks are
        # simply not carried over.
        assert not hasattr(defn, "hooks")


# ── register_loader_subagents ─────────────────────────────────────────────────


class TestRegisterLoaderSubagents:
    def test_returns_definitions_in_card_order(self):
        cards = [
            _card(name="Odin", level=2),
            _card(name="Mimir", level=1, tools=["web_search"]),
        ]
        defs = register_loader_subagents(_StubLoader(cards), overwrite=False)

        assert len(defs) == 2
        assert all(isinstance(d, SubAgentDefinition) for d in defs)
        assert [d.agent_type for d in defs] == ["odin", "mimir"]
        assert [d.tags for d in defs] == [
            ["vanaheim", "level:2", "Odin"],
            ["vanaheim", "level:1", "Mimir"],
        ]

    def test_overwrite_false_skips_existing(self):
        register(SubAgentDefinition(agent_type="odin", system_prompt="KEEP"))
        cards = [_card(name="Odin"), _card(name="Mimir")]

        defs = register_loader_subagents(_StubLoader(cards), overwrite=False)

        # Only Mimir is newly registered; Odin is preserved.
        assert len(defs) == 1
        assert defs[0].agent_type == "mimir"
        assert get_agent("odin").system_prompt == "KEEP"

    def test_overwrite_true_replaces_all(self):
        register(SubAgentDefinition(agent_type="odin", system_prompt="OLD"))
        cards = [_card(name="Odin"), _card(name="Mimir")]

        defs = register_loader_subagents(_StubLoader(cards), overwrite=True)

        assert len(defs) == 2
        assert get_agent("odin").system_prompt != "OLD"

    def test_empty_loader_returns_empty_list(self):
        assert register_loader_subagents(_StubLoader([]), overwrite=False) == []


# ── register_vanaheim_subagents (real YAML) ───────────────────────────────────


class TestRegisterVanaheimSubagents:
    def test_registers_14_and_returns_definitions(self, vanaheim_yaml_path):
        # agent_cards.yaml lives at <repo>/Vanaheim/Agents/agent_cards.yaml
        repo_root = vanaheim_yaml_path.parents[2]

        defs = register_vanaheim_subagents(repo_root, overwrite=False)

        assert len(defs) == 14, f"expected 14 Vanaheim cards, got {len(defs)}"
        assert all(isinstance(d, SubAgentDefinition) for d in defs)
        # Every definition is tagged as a Vanaheim agent.
        assert all(d.tags[0] == "vanaheim" for d in defs)
        # agent_types are casefolded card names — no uppercase letters.
        assert all(d.agent_type == d.agent_type.casefold() for d in defs)
        # Each is retrievable from the registry.
        for d in defs:
            assert get_agent(d.agent_type) is d

    def test_overwrite_false_preserves_default_persona(self, vanaheim_yaml_path):
        # Pre-register one of the 8 default personas under a key that collides
        # with nothing in Vanaheim (no Vanaheim card is named "researcher").
        register(SubAgentDefinition(agent_type="researcher", system_prompt="MIMIR-DEFAULT"))
        repo_root = vanaheim_yaml_path.parents[2]

        defs = register_vanaheim_subagents(repo_root, overwrite=False)

        assert len(defs) == 14
        # The default persona survives untouched.
        assert get_agent("researcher").system_prompt == "MIMIR-DEFAULT"


# ── Real-card mapping + unicode (real YAML) ───────────────────────────────────


class TestVanaheimRealCardMapping:
    def test_full_mapping_of_real_card(self, vanaheim_yaml_path):
        loader = AgentCardLoader(vanaheim_yaml_path)
        heimdall = loader.get_agent("Heimdall")
        if heimdall is None:
            pytest.skip("Heimdall not present in Vanaheim YAML")

        defn = card_to_subagent(heimdall)

        assert defn.agent_type == "heimdall"
        assert defn.system_prompt == f"{heimdall.role}\n\n{heimdall.description}".strip()
        assert defn.when_to_use == (heimdall.description or heimdall.role)
        assert defn.allowed_tools == list(heimdall.tools)  # verbatim
        assert defn.disallowed_tools == []
        assert defn.model_preference == (heimdall.model or None)
        assert defn.max_concurrency is None
        assert defn.tags == ["vanaheim", f"level:{heimdall.level}", "Heimdall"]

    def test_unicode_jormungandr_round_trips(self, vanaheim_yaml_path):
        loader = AgentCardLoader(vanaheim_yaml_path)
        card = loader.get_agent("Jörmungandr")
        if card is None:
            pytest.skip("Jörmungandr not present in Vanaheim YAML")

        # agent_type is the casefolded (unicode-preserving) name
        at = agent_type_for_card(card)
        assert at == "jörmungandr"
        assert "ö" in at

        # Registering + retrieving round-trips the unicode key exactly.
        assert register_card_subagent(card, overwrite=False) is True
        defn = get_agent("jörmungandr")
        assert defn is not None
        assert defn.tags == ["vanaheim", f"level:{card.level}", "Jörmungandr"]

    def test_no_card_registers_wildcard_tools(self, vanaheim_yaml_path):
        # Defensive: across the entire real YAML, no card may expand to ["*"].
        loader = AgentCardLoader(vanaheim_yaml_path)
        for card in loader.list_agents():
            defn = card_to_subagent(card)
            assert defn.allowed_tools != ["*"], (
                f"card {card.name!r} mapped to wildcard tools"
            )
            # And it equals the card's tools verbatim.
            assert defn.allowed_tools == list(card.tools)
