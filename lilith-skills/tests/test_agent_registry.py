"""Tests for lilith_skills.agent_registry."""
import pytest
import tempfile
from pathlib import Path

from lilith_skills.agent_cards import AgentCard, AgentCardLoader
from lilith_skills.agent_registry import AgentRegistry


def _write_cards_yaml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "agent_cards.yaml"
    p.write_text(content, encoding="utf-8")
    return p


@pytest.fixture
def registry() -> AgentRegistry:
    yaml = """
name: Odin
role: Code architect
level: 2
model: glm-5.2
tools: [terminal, file_edit, search]
description: All-father
---
name: Mimir
role: Research
level: 1
model: glm-5.2
tools: [web, search]
description: Wise
---
name: Eva
role: Creative
level: 1
model: claude-sonnet
tools: [image_gen]
description: Creative
---
name: Adan
role: Debug
level: 2
model: glm-5.2
tools: [terminal, debug]
description: Debug
---
name: Lilith
role: Chat
level: 1
model: glm-5.2
tools: [chat]
description: Default
"""
    with tempfile.TemporaryDirectory() as td:
        loader = AgentCardLoader(_write_cards_yaml(Path(td), yaml))
        return AgentRegistry(loader)


# ── Lookups ────────────────────────────────────────────────────


def test_registry_list(registry: AgentRegistry):
    assert len(registry.list_agents()) == 5


def test_registry_get(registry: AgentRegistry):
    card = registry.get("Odin")
    assert card is not None
    assert card.level == 2


def test_registry_by_level(registry: AgentRegistry):
    assert len(registry.by_level(2)) == 2
    assert len(registry.by_level(1)) == 3


def test_registry_by_tool(registry: AgentRegistry):
    assert len(registry.by_tool("terminal")) == 2  # Odin + Adan
    assert len(registry.by_tool("web")) == 1  # Mimir


def test_registry_by_model(registry: AgentRegistry):
    assert len(registry.by_model("glm-5.2")) == 4
    assert len(registry.by_model("claude-sonnet")) == 1


def test_registry_executors_consultants(registry: AgentRegistry):
    assert len(registry.executors()) == 2
    assert len(registry.consultants()) == 3


# ── Dispatch ───────────────────────────────────────────────────


def test_select_agent_by_intent_code(registry: AgentRegistry):
    card = registry.select_agent(intent="code")
    assert card is not None
    assert card.name == "Odin"


def test_select_agent_by_intent_research(registry: AgentRegistry):
    card = registry.select_agent(intent="research")
    assert card is not None
    assert card.name == "Mimir"


def test_select_agent_by_intent_debug(registry: AgentRegistry):
    card = registry.select_agent(intent="debug")
    assert card is not None
    assert card.name == "Adan"


def test_select_agent_with_required_tools(registry: AgentRegistry):
    card = registry.select_agent(required_tools=["terminal"])
    assert card is not None
    # Should pick an executor with terminal
    assert "terminal" in card.tools
    assert card.is_executor()


def test_select_agent_with_intent_and_tools(registry: AgentRegistry):
    card = registry.select_agent(intent="code", required_tools=["terminal"])
    assert card is not None
    assert card.name == "Odin"


def test_select_agent_prefer_level(registry: AgentRegistry):
    card = registry.select_agent(intent="chat", prefer_level=2)
    # No executor at chat intent, should fallback to consultant
    assert card is not None
    assert card.level == 1


def test_select_agent_no_intent(registry: AgentRegistry):
    card = registry.select_agent(prefer_level=2)
    assert card is not None
    assert card.is_executor()


def test_select_agent_unknown_intent(registry: AgentRegistry):
    # Unknown intent → just return any preferred level
    card = registry.select_agent(intent="unknown", prefer_level=2)
    assert card is not None


def test_select_agent_empty_registry():
    # Edge case: empty registry
    loader = AgentCardLoader("/nonexistent/cards.yaml")
    registry = AgentRegistry(loader)
    assert registry.select_agent(intent="code") is None


def test_select_agent_no_match_for_required_tools(registry: AgentRegistry):
    # No agent has all of these tools together
    card = registry.select_agent(required_tools=["terminal", "image_gen"])
    assert card is None


# ── Stats ───────────────────────────────────────────────────────


def test_stats(registry: AgentRegistry):
    s = registry.stats()
    assert s["total"] == 5
    assert s["executors"] == 2
    assert s["consultants"] == 3
    assert "Odin" in s["agents"]
    assert s["by_model"]["glm-5.2"] == 4
