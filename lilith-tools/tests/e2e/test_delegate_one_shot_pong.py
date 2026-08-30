"""One-shot e2e: delegate to a preset and confirm the model responds PONG.

The cheapest smoke on the suite — a single-turn completion with no
tools. The orchestrator calls this when it just wants an answer from
another model without committing to a full mini-loop.

The PONG marker is intentionally trivial so the test deterministically
finishes inside one turn and the model has zero ambiguity on what
"correct" looks like.
"""

from __future__ import annotations

import pytest

from lilith_tools import ToolRegistry


@pytest.fixture
def delegate_tool():
    tool_cls = ToolRegistry.get("delegate_subagent")
    assert tool_cls is not None, "delegate_subagent not registered"
    return tool_cls()


_PONG_PROMPT = (
    "Responde EXACTAMENTE con la palabra PONG. Sin explicaciones, sin "
    "puntuación adicional, sin markdown. Solo PONG."
)


def test_one_shot_pong_deepseek(
    delegate_tool,
    require_provider_keys,
) -> None:
    """DeepSeek responds with PONG to a one-shot delegation."""
    require_provider_keys("batch-deepseek")

    result = delegate_tool.execute(
        preset="batch-deepseek",
        prompt=_PONG_PROMPT,
        structured=False,
        max_tokens=32,
    )

    assert result.success, (
        f"one-shot batch-deepseek failed — error={result.error!r}"
    )

    data = result.data or {}
    content = (data.get("content") or data.get("raw_content") or "")
    assert content.strip().upper() == "PONG", (
        f"expected exactly PONG, got content={content!r}"
    )


def test_one_shot_pong_grok(
    delegate_tool,
    require_provider_keys,
) -> None:
    """Grok responds with PONG to a one-shot delegation."""
    require_provider_keys("grok-research")

    result = delegate_tool.execute(
        preset="grok-research",
        prompt=_PONG_PROMPT,
        structured=False,
        max_tokens=32,
    )

    assert result.success, (
        f"one-shot grok-research failed — error={result.error!r}"
    )

    data = result.data or {}
    content = (data.get("content") or data.get("raw_content") or "")
    assert content.strip().upper() == "PONG", (
        f"expected exactly PONG, got content={content!r}"
    )
