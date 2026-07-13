"""Tests for lilith_skills.agent_cards (AgentCard, AgentCardLoader)."""
import pytest
import tempfile
from pathlib import Path

from lilith_skills.agent_cards import AgentCard, AgentCardLoader


# ── AgentCard ─────────────────────────────────────────────────────


def test_agent_card_creation():
    card = AgentCard(
        name="Odin",
        role="Orchestrator",
        level=2,
        model="glm-5.2",
        tools=["terminal", "web"],
        description="All-father",
    )
    assert card.name == "Odin"
    assert card.level == 2
    assert card.tools == ["terminal", "web"]
    assert card.hooks == []  # default


def test_agent_card_with_hooks():
    card = AgentCard(
        name="Mimir",
        role="Knowledge",
        level=1,
        model="glm-5.2",
        tools=["web"],
        description="Wise",
        hooks=["pre_tool_use", "post_tool_use"],
    )
    assert card.hooks == ["pre_tool_use", "post_tool_use"]


def test_agent_card_from_dict():
    data = {
        "name": "Thor",
        "role": "DevOps",
        "level": 2,
        "model": "deepseek",
        "tools": ["terminal"],
        "description": "Hammer",
    }
    card = AgentCard.from_dict(data)
    assert card.name == "Thor"
    assert card.level == 2
    assert card.tools == ["terminal"]


def test_agent_card_from_dict_defaults():
    card = AgentCard.from_dict({})
    assert card.name == ""
    assert card.level == 1
    assert card.model == "glm-5.2"
    assert card.tools == []


def test_agent_card_to_dict():
    card = AgentCard(
        name="Skadi",
        role="Debug",
        level=2,
        model="glm-5.2",
        tools=["terminal"],
        description="Hunter",
        hooks=["pre_tool_use"],
    )
    d = card.to_dict()
    assert d["name"] == "Skadi"
    assert d["hooks"] == ["pre_tool_use"]


def test_agent_card_is_executor():
    card = AgentCard(name="x", role="r", level=2, model="m", tools=[], description="")
    assert card.is_executor() is True
    card.level = 1
    assert card.is_executor() is False


def test_agent_card_is_consultant():
    card = AgentCard(name="x", role="r", level=1, model="m", tools=[], description="")
    assert card.is_consultant() is True
    card.level = 2
    assert card.is_consultant() is False


def test_agent_card_repr():
    card = AgentCard(name="Odin", role="r", level=2, model="glm-5.2", tools=[], description="")
    r = repr(card)
    assert "Odin" in r
    assert "level=2" in r


# ── AgentCardLoader ──────────────────────────────────────────────


def _write_cards_yaml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "agent_cards.yaml"
    p.write_text(content, encoding="utf-8")
    return p


def test_loader_init():
    with tempfile.TemporaryDirectory() as td:
        cards_path = _write_cards_yaml(Path(td), "")
        loader = AgentCardLoader(cards_path)
        assert loader.list_agents() == []


def test_loader_loads_cards():
    yaml = """
name: Odin
role: Orchestrator
level: 2
model: glm-5.2
tools: [terminal, web]
description: All-father
hooks: [pre_tool_use]
---
name: Mimir
role: Knowledge
level: 1
model: glm-5.2
tools: [web]
description: Wise
"""
    with tempfile.TemporaryDirectory() as td:
        cards_path = _write_cards_yaml(Path(td), yaml)
        loader = AgentCardLoader(cards_path)
        agents = loader.list_agents()
        assert len(agents) == 2
        assert agents[0].name == "Odin"
        assert agents[0].hooks == ["pre_tool_use"]
        assert agents[1].name == "Mimir"


def test_loader_skips_invalid_docs():
    yaml = """
- this is a list, not a dict
---
name: Valid
role: r
level: 1
model: m
tools: []
description: d
---
no_name_field: true
role: r
"""
    with tempfile.TemporaryDirectory() as td:
        cards_path = _write_cards_yaml(Path(td), yaml)
        loader = AgentCardLoader(cards_path)
        # Only the dict with 'name' should be loaded
        assert len(loader.list_agents()) == 1
        assert loader.list_agents()[0].name == "Valid"


def test_loader_get_agent_case_insensitive():
    yaml = """
name: Odin
role: r
level: 2
model: m
tools: []
description: d
"""
    with tempfile.TemporaryDirectory() as td:
        loader = AgentCardLoader(_write_cards_yaml(Path(td), yaml))
        assert loader.get_agent("Odin") is not None
        assert loader.get_agent("odin") is not None
        assert loader.get_agent("ODIN") is not None
        assert loader.get_agent("unknown") is None


def test_loader_by_level():
    yaml = """
name: A
role: r
level: 1
model: m
tools: []
description: d
---
name: B
role: r
level: 2
model: m
tools: []
description: d
---
name: C
role: r
level: 2
model: m
tools: []
description: d
"""
    with tempfile.TemporaryDirectory() as td:
        loader = AgentCardLoader(_write_cards_yaml(Path(td), yaml))
        assert len(loader.by_level(1)) == 1
        assert len(loader.by_level(2)) == 2


def test_loader_has_tool():
    yaml = """
name: A
role: r
level: 1
model: m
tools: [terminal, web]
description: d
---
name: B
role: r
level: 1
model: m
tools: [web]
description: d
"""
    with tempfile.TemporaryDirectory() as td:
        loader = AgentCardLoader(_write_cards_yaml(Path(td), yaml))
        assert len(loader.has_tool("terminal")) == 1
        assert len(loader.has_tool("web")) == 2
        assert len(loader.has_tool("TERMINAL")) == 1  # case-insensitive


def test_loader_by_hook():
    yaml = """
name: A
role: r
level: 1
model: m
tools: []
description: d
hooks: [pre_tool_use, post_tool_use]
---
name: B
role: r
level: 1
model: m
tools: []
description: d
hooks: [pre_tool_use]
"""
    with tempfile.TemporaryDirectory() as td:
        loader = AgentCardLoader(_write_cards_yaml(Path(td), yaml))
        assert len(loader.by_hook("pre_tool_use")) == 2
        assert len(loader.by_hook("post_tool_use")) == 1


def test_loader_by_role_keyword():
    yaml = """
name: A
role: Frontend Developer
level: 1
model: m
tools: []
description: d
---
name: B
role: Backend Developer
level: 1
model: m
tools: []
description: d
---
name: C
role: Designer
level: 1
model: m
tools: []
description: d
"""
    with tempfile.TemporaryDirectory() as td:
        loader = AgentCardLoader(_write_cards_yaml(Path(td), yaml))
        assert len(loader.by_role_keyword("developer")) == 2
        assert len(loader.by_role_keyword("designer")) == 1


def test_loader_executors_consultants():
    yaml = """
name: A
role: r
level: 1
model: m
tools: []
description: d
---
name: B
role: r
level: 2
model: m
tools: []
description: d
---
name: C
role: r
level: 3
model: m
tools: []
description: d
"""
    with tempfile.TemporaryDirectory() as td:
        loader = AgentCardLoader(_write_cards_yaml(Path(td), yaml))
        assert len(loader.consultants()) == 1
        assert len(loader.executors()) == 2  # level >= 2


def test_loader_stats():
    yaml = """
name: A
role: r
level: 1
model: m
tools: []
description: d
---
name: B
role: r
level: 2
model: m
tools: []
description: d
"""
    with tempfile.TemporaryDirectory() as td:
        loader = AgentCardLoader(_write_cards_yaml(Path(td), yaml))
        s = loader.stats()
        assert s["total_agents"] == 2
        assert s["consultants"] == 1
        assert s["executors"] == 1
        assert set(s["agents"]) == {"A", "B"}


def test_loader_to_dict():
    yaml = """
name: A
role: r
level: 1
model: m
tools: []
description: d
"""
    with tempfile.TemporaryDirectory() as td:
        loader = AgentCardLoader(_write_cards_yaml(Path(td), yaml))
        d = loader.to_dict()
        assert "source" in d
        assert "agents" in d
        assert d["agents"][0]["name"] == "A"


def test_loader_missing_file_silently_empty():
    loader = AgentCardLoader("/nonexistent/path/cards.yaml")
    assert loader.list_agents() == []


def test_loader_from_vanaheim_missing_raises():
    with tempfile.TemporaryDirectory() as td:
        with pytest.raises(FileNotFoundError):
            AgentCardLoader.from_vanaheim(td)


# ── Pydantic validation ────────────────────────────────────────


def test_pydantic_extra_fields_ignored():
    # Pydantic model_config extra="ignore" — unknown fields are dropped
    card = AgentCard.model_validate({
        "name": "Odin",
        "role": "r",
        "level": 2,
        "model": "m",
        "tools": [],
        "description": "d",
        "unknown_field": "should be ignored",
        "another_one": 42,
    })
    assert card.name == "Odin"
    assert not hasattr(card, "unknown_field")


def test_pydantic_string_coercion():
    # Numeric values get coerced to string per _ensure_str validator
    card = AgentCard.model_validate({
        "name": 123,  # int → str
        "role": "r",
        "level": 2,
        "model": "m",
        "tools": [],
        "description": "d",
    })
    assert card.name == "123"
    assert isinstance(card.name, str)


def test_pydantic_list_coercion():
    # None gets coerced to empty list
    card = AgentCard.model_validate({
        "name": "X",
        "role": "r",
        "level": 1,
        "model": "m",
        "tools": None,
        "description": "d",
    })
    assert card.tools == []
    # List with None elements is filtered to empty
    card2 = AgentCard.model_validate({
        "name": "X",
        "role": "r",
        "level": 1,
        "model": "m",
        "tools": ["a", "b"],
        "description": "d",
    })
    assert card2.tools == ["a", "b"]


def test_pydantic_level_ge_zero():
    # level: int = Field(ge=0) — negative level fails
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        AgentCard.model_validate({
            "name": "X", "role": "r", "level": -1,
            "model": "m", "tools": [], "description": "d",
        })


def test_pydantic_default_values():
    # All fields have defaults — empty dict produces a valid card
    card = AgentCard.model_validate({})
    assert card.name == ""
    assert card.role == ""
    assert card.level == 1
    assert card.model == "glm-5.2"
    assert card.tools == []
    assert card.hooks == []


def test_pydantic_serialize_roundtrip():
    # to_dict then model_validate should be lossless
    original = AgentCard(
        name="Loki",
        role="Testing",
        level=2,
        model="m",
        tools=["x", "y"],
        description="d",
        hooks=["pre_tool_use"],
    )
    restored = AgentCard.model_validate(original.to_dict())
    assert restored.name == original.name
    assert restored.level == original.level
    assert restored.tools == original.tools
    assert restored.hooks == original.hooks
