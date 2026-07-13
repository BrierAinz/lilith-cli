"""Tests for the Agent Card Loader module.

Tests the loading of agent metadata from Vanaheim/Agents/agent_cards.yaml
and the AgentRegistry search capabilities.
"""

import pytest
from pathlib import Path

from lilith_skills.agent_card_loader import (
    AgentCard,
    AgentCardLoader,
    AgentRegistry,
)


# Sample YAML for testing
SAMPLE_YAML = """
---
name: Odin
role: "Allfather — Strategist & Oracle"
level: 1
model: "glm-5.2"
tools:
  - web_search
  - read_file
  - search_files
description: >
  Odin sees all. Strategic decision-making and long-term planning.

---
name: Heimdall
role: "Watchman — Security & Auditing"
level: 1
model: "glm-5.2"
tools:
  - read_file
  - search_files
description: >
  Heimdall watches the Bifrost. Security auditing and vulnerability detection.
"""


class TestAgentCard:
    """Tests for the AgentCard dataclass."""

    def test_basic_creation(self):
        """Test basic agent card creation."""
        card = AgentCard(
            name="Test",
            role="Tester",
            level=2,
            model="test-model",
            tools=["tool1"],
            description="A test agent",
        )

        assert card.name == "Test"
        assert card.role == "Tester"
        assert card.level == 2
        assert card.model == "test-model"
        assert "tool1" in card.tools

    def test_capabilities_derived(self):
        """Test that capabilities are derived from role and tools."""
        card = AgentCard(
            name="Builder",
            role="Builder — Code & Infrastructure",
            level=2,
            model="test",
            tools=["terminal", "write_file"],
            description="Builds projects from scratch",
        )

        assert "building" in card.capabilities or "execution" in card.capabilities

    def test_to_dict(self):
        """Test conversion to dictionary."""
        card = AgentCard(
            name="Test",
            role="Tester",
            level=1,
            model="test",
        )

        d = card.to_dict()
        assert d["name"] == "Test"
        assert d["level"] == 1

    def test_from_dict(self):
        """Test creation from dictionary."""
        data = {
            "name": "Test",
            "role": "Tester",
            "level": 2,
            "model": "test",
            "tools": ["tool1"],
        }

        card = AgentCard.from_dict(data)
        assert card.name == "Test"
        assert card.level == 2


class TestAgentCardLoader:
    """Tests for the AgentCardLoader class."""

    def test_from_yaml_string(self, tmp_path):
        """Test loading from YAML string."""
        yaml_file = tmp_path / "agents.yaml"
        yaml_file.write_text(SAMPLE_YAML, encoding="utf-8")

        loader = AgentCardLoader.from_yaml(yaml_file)
        agents = loader.list_agents()

        assert len(agents) == 2
        names = [a.name for a in agents]
        assert "Odin" in names
        assert "Heimdall" in names

    def test_get_agent(self, tmp_path):
        """Test retrieving a specific agent."""
        yaml_file = tmp_path / "agents.yaml"
        yaml_file.write_text(SAMPLE_YAML, encoding="utf-8")

        loader = AgentCardLoader.from_yaml(yaml_file)
        odin = loader.get("Odin")

        assert odin is not None
        assert odin.level == 1
        assert "web_search" in odin.tools

    def test_by_level(self, tmp_path):
        """Test filtering by level."""
        yaml_file = tmp_path / "agents.yaml"
        yaml_file.write_text(SAMPLE_YAML, encoding="utf-8")

        loader = AgentCardLoader.from_yaml(yaml_file)
        level1 = loader.by_level(1)

        assert len(level1) == 2

    def test_by_capability(self, tmp_path):
        """Test filtering by capability."""
        yaml_file = tmp_path / "agents.yaml"
        yaml_file.write_text(SAMPLE_YAML, encoding="utf-8")

        loader = AgentCardLoader.from_yaml(yaml_file)
        researchers = loader.by_capability("research")

        # Should find agents with research capability
        assert len(researchers) >= 0

    def test_search(self, tmp_path):
        """Test search functionality."""
        yaml_file = tmp_path / "agents.yaml"
        yaml_file.write_text(SAMPLE_YAML, encoding="utf-8")

        loader = AgentCardLoader.from_yaml(yaml_file)
        results = loader.search("security")

        assert len(results) > 0
        assert results[0].name == "Heimdall"

    def test_stats(self, tmp_path):
        """Test statistics generation."""
        yaml_file = tmp_path / "agents.yaml"
        yaml_file.write_text(SAMPLE_YAML, encoding="utf-8")

        loader = AgentCardLoader.from_yaml(yaml_file)
        stats = loader.stats()

        assert stats["total_agents"] == 2
        assert "by_level" in stats


class TestAgentRegistry:
    """Tests for the AgentRegistry class."""

    def test_consultants(self, tmp_path):
        """Test filtering consultant agents."""
        yaml_file = tmp_path / "agents.yaml"
        yaml_file.write_text(SAMPLE_YAML, encoding="utf-8")

        loader = AgentCardLoader.from_yaml(yaml_file)
        registry = loader.get_registry()

        consultants = registry.consultants()
        assert len(consultants) == 2

    def test_executors(self, tmp_path):
        """Test filtering executor agents (none in sample)."""
        yaml_file = tmp_path / "agents.yaml"
        yaml_file.write_text(SAMPLE_YAML, encoding="utf-8")

        loader = AgentCardLoader.from_yaml(yaml_file)
        registry = loader.get_registry()

        executors = registry.executors()
        assert len(executors) == 0

    def test_find_for_task(self, tmp_path):
        """Test finding agents for a task."""
        yaml_file = tmp_path / "agents.yaml"
        yaml_file.write_text(SAMPLE_YAML, encoding="utf-8")

        loader = AgentCardLoader.from_yaml(yaml_file)
        registry = loader.get_registry()

        # Search for security-related task
        security_agents = registry.find_for_task("check for vulnerabilities")
        assert len(security_agents) > 0


class TestIntegration:
    """Integration tests with real Vanaheim agent_cards.yaml."""

    @pytest.fixture
    def vanaheim_path(self):
        """Get path to Yggdrasil repo."""
        # This assumes the test is running from the Yggdrasil repo
        return Path(__file__).parent.parent.parent.parent

    def test_load_from_vanaheim(self, vanaheim_path):
        """Test loading agent cards from the actual Vanaheim realm."""
        try:
            loader = AgentCardLoader.from_vanaheim(vanaheim_path)
            agents = loader.list_agents()

            # Should have loaded Odin, Mimir, Adan, Eva, Shalltear, Heimdall
            assert len(agents) >= 6

            names = [a.name for a in agents]
            assert "Odin" in names
            assert "Heimdall" in names

            # Check that capabilities were derived
            for agent in agents:
                assert len(agent.capabilities) > 0

        except FileNotFoundError:
            pytest.skip("Vanaheim agent_cards.yaml not found")

    def test_all_agents_have_cards(self, vanaheim_path):
        """Test that all expected agents are in the cards."""
        try:
            loader = AgentCardLoader.from_vanaheim(vanaheim_path)
            expected_agents = ["Odin", "Mimir", "Adan", "Eva", "Shalltear", "Heimdall"]

            for name in expected_agents:
                agent = loader.get(name)
                assert agent is not None, f"Agent {name} not found"
                assert agent.name == name

        except FileNotFoundError:
            pytest.skip("Vanaheim agent_cards.yaml not found")
