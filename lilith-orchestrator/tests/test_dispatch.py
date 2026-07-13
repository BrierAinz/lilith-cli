"""Tests for lilith_orchestrator.dispatch."""
import pytest
import tempfile
from pathlib import Path

from lilith_skills.agent_cards import AgentCardLoader
from lilith_skills.agent_registry import AgentRegistry
from lilith_orchestrator.dispatch import (
    TaskDispatcher,
    _extract_intent_from_text,
    _extract_tools_from_text,
)


def _make_registry() -> AgentRegistry:
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
name: Adan
role: Debug
level: 2
model: glm-5.2
tools: [terminal, debug]
description: Debug
"""
    with tempfile.TemporaryDirectory() as td:
        loader = AgentCardLoader(Path(td) / "cards.yaml")
        Path(loader.cards_path).write_text(yaml, encoding="utf-8")
        # Re-instantiate after writing
        loader = AgentCardLoader(loader.cards_path)
        return AgentRegistry(loader)


@pytest.fixture
def dispatcher() -> TaskDispatcher:
    return TaskDispatcher(_make_registry())


# ── Intent extraction helper ──────────────────────────────────


def test_extract_intent_code():
    assert _extract_intent_from_text("write a function to parse JSON") == "code"


def test_extract_intent_research():
    assert _extract_intent_from_text("research the history of X") == "research"


def test_extract_intent_debug():
    # Use a description that ONLY matches debug keywords
    assert _extract_intent_from_text("traceback shows exception, crash on startup") == "debug"


def test_extract_intent_prefers_stronger_signal():
    # 'traceback' is debug-only and not in any other intent list.
    assert _extract_intent_from_text("getting a traceback on startup") == "debug"


def test_extract_intent_chat_fallback():
    # No keywords match → chat
    assert _extract_intent_from_text("tell me a joke") == "chat"


def test_extract_tools_terminal():
    tools = _extract_tools_from_text("run this in terminal")
    assert "terminal" in tools


def test_extract_tools_web():
    tools = _extract_tools_from_text("search the web for X")
    assert "web" in tools or "search" in tools


# ── Direct route() ─────────────────────────────────────────────


def test_route_by_intent_code(dispatcher: TaskDispatcher):
    card = dispatcher.route(intent="code")
    assert card is not None
    assert card.name == "Odin"


def test_route_by_intent_research(dispatcher: TaskDispatcher):
    card = dispatcher.route(intent="research")
    assert card is not None
    assert card.name == "Mimir"


def test_route_with_required_tools(dispatcher: TaskDispatcher):
    card = dispatcher.route(intent="code", required_tools=["terminal"])
    assert card is not None
    assert card.name == "Odin"


def test_route_no_match(dispatcher: TaskDispatcher):
    card = dispatcher.route(required_tools=["nonexistent-tool"])
    assert card is None


# ── route_task() — free-form descriptions ───────────────────────


def test_route_task_code_description(dispatcher: TaskDispatcher):
    card = dispatcher.route_task("write a function to parse JSON")
    assert card is not None
    assert card.name == "Odin"


def test_route_task_research_description(dispatcher: TaskDispatcher):
    card = dispatcher.route_task("research the history of Yggdrasil")
    assert card is not None
    assert card.name == "Mimir"


def test_route_task_debug_description(dispatcher: TaskDispatcher):
    card = dispatcher.route_task("debug this exception, fix the crash")
    assert card is not None
    # Could be Odin (code) or Adan (debug) — both have terminal
    # but Adan is the preferred debug agent
    assert card.name in ("Adan", "Odin")


def test_route_task_chat_description(dispatcher: TaskDispatcher):
    card = dispatcher.route_task("hello there, how are you?")
    assert card is not None
    # Chat → no specific agent in this registry
    # Should fall back to level preference (2 = executor)
    assert card.is_executor()


# ── explain() ───────────────────────────────────────────────────


def test_explain(dispatcher: TaskDispatcher):
    card = dispatcher.route(intent="code")
    assert card is not None
    info = dispatcher.explain(card)
    assert info["agent"] == "Odin"
    assert info["level"] == 2
    assert info["is_executor"] is True
    assert "terminal" in info["tools"]
