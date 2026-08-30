"""Structured e2e: delegate_subagent returns a TASK_SCHEMA-valid ``data['structured']``.

When ``structured=True`` the delegate tool asks the sub-agent to
emit a JSON object matching :data:`TASK_SCHEMA` (summary, status,
deliverables, ...). The local validator
(``lilith_tools.task_schema.validate_task_response``) decides
whether the answer counts as structured. A passing e2e run must:

1. succeed (``ToolResult.success is True``),
2. populate ``data['structured']`` with a dict,
3. leave ``data['validation_errors']`` empty (or absent).

Two presets are exercised so a regression in one provider does not
silently pass via the other.
"""

from __future__ import annotations

import pytest

from lilith_tools import ToolRegistry


@pytest.fixture
def delegate_tool():
    tool_cls = ToolRegistry.get("delegate_subagent")
    assert tool_cls is not None, "delegate_subagent not registered"
    return tool_cls()


_STRUCTURED_PROMPT = (
    "Responde EXCLUSIVAMENTE con un objeto JSON válido, sin texto antes ni después, "
    "sin markdown, sin fences, sin comentarios. El JSON debe tener esta forma:\n"
    "{\n"
    '  "summary": "frase corta que resuma el resultado",\n'
    '  "status": "completed",\n'
    '  "deliverables": [],\n'
    '  "confidence": 0.9\n'
    "}\n"
    "TASK: contesta a la pregunta ¿cuánto es 2+2? en el campo summary."
)


def test_estructurado_batch_deepseek(delegate_tool, require_provider_keys) -> None:
    """batch-deepseek returns a validated structured dict."""
    require_provider_keys("batch-deepseek")

    result = delegate_tool.execute(
        preset="batch-deepseek",
        prompt=_STRUCTURED_PROMPT,
        structured=True,
        max_tokens=512,
    )

    assert result.success, f"batch-deepseek structured failed — error={result.error!r}"

    data = result.data or {}
    structured = data.get("structured")
    assert structured is not None, (
        f"batch-deepseek structured run returned no `structured` key — data={data!r}"
    )
    assert isinstance(structured, dict), (
        f"`structured` must be a dict, got {type(structured).__name__}"
    )
    # TASK_SCHEMA required top-level keys
    assert "summary" in structured, (
        f"`structured` missing required `summary` — got keys={list(structured)}"
    )
    assert structured.get("status") in {"completed", "failed", "blocked"}, (
        f"`structured.status` invalid — got {structured.get('status')!r}"
    )
    # numeric confidence is in [0, 1] per the schema; allow None as
    # some providers omit it
    confidence = structured.get("confidence")
    if confidence is not None:
        assert 0.0 <= float(confidence) <= 1.0, (
            f"`confidence` out of range — got {confidence!r}"
        )
    # validation_errors must be empty when structured succeeded
    errors = data.get("validation_errors") or []
    assert not errors, (
        f"validation_errors should be empty when structured validated, got {errors!r}"
    )


def test_estructurado_grok_research(delegate_tool, require_provider_keys) -> None:
    """grok-research returns a validated structured dict."""
    require_provider_keys("grok-research")

    result = delegate_tool.execute(
        preset="grok-research",
        prompt=_STRUCTURED_PROMPT,
        structured=True,
        max_tokens=512,
    )

    assert result.success, f"grok-research structured failed — error={result.error!r}"

    data = result.data or {}
    structured = data.get("structured")
    assert structured is not None, (
        f"grok-research structured run returned no `structured` key — data={data!r}"
    )
    assert isinstance(structured, dict), (
        f"`structured` must be a dict, got {type(structured).__name__}"
    )
    assert structured.get("status") in {"completed", "failed", "blocked"}, (
        f"`structured.status` invalid — got {structured.get('status')!r}"
    )
    assert "summary" in structured, structured
