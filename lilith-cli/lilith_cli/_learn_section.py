"""Source for /learn slash command block. Appended to extra_commands.py by _learn_section.py.

The :class:`LearnSkillSuggester` here is the pure logic that turns the
delegation post-mortems stored in ``~/.yggdrasil/orchestration_state.json``
into candidate ``DelegationSkill`` templates. It is separated from the
REPL/renderer glue in ``extra_commands.py`` so the unit tests can drive it
directly with a synthetic state JSON.

Public API
----------

* :class:`LearnSkillSuggester` — pure logic; takes a list of post_mortems
  (+ optional tasks for prompt-context lookup) and yields suggested skills.
* :func:`suggest_from_state_path` — convenience wrapper that reads the
  state JSON from a given path and returns suggestions.
* :func:`save_suggestion` — turns a suggestion into a real
  :class:`lilith_skills.delegation_skills.DelegationSkill` and persists it
  via :class:`DelegationSkillRegistry`.

The renderer and the REPL wiring (``/learn``, ``/learn save <n>``) live
in ``extra_commands.py`` and reuse this module.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

# Minimum number of successful post-mortems for a preset before we
# propose a skill from it. Single-event patterns are not statistically
# meaningful; two is the minimum to call it a "pattern".
_MIN_SUCCESS_FOR_SUGGESTION = 2


@dataclass
class SkillSuggestion:
    """One row in the ``/learn`` output table.

    Carries enough data to render the table AND to materialise a real
    :class:`DelegationSkill` via :func:`save_suggestion`.
    """

    index: int
    name: str
    description: str
    preset: str
    prompt_template: str
    agentic: bool = False
    structured: bool = False
    max_tokens: int | None = None
    sample_tasks: list[str] = field(default_factory=list)
    success_count: int = 0


def suggest_from_state_path(state_path: Path) -> list[SkillSuggestion]:
    """Read the state JSON at ``state_path`` and return suggestions.

    Returns an empty list (NOT raises) if the file is missing or invalid —
    the caller decides whether to surface a user-facing message.
    """
    if not state_path.exists():
        return []
    try:
        import json

        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    post_mortems = data.get("post_mortems") or []
    tasks = data.get("tasks") or []
    return suggest_from_post_mortems(post_mortems, tasks=tasks)


def suggest_from_post_mortems(
    post_mortems: Iterable[dict[str, Any]],
    *,
    tasks: Iterable[dict[str, Any]] | None = None,
    min_success: int = _MIN_SUCCESS_FOR_SUGGESTION,
) -> list[SkillSuggestion]:
    """Group successful post-mortems by preset and propose one skill per group.

    Algorithm
    ---------

    1. Keep only entries with ``success is True`` and a non-empty preset.
    2. Group by ``preset``.
    3. For each group with ``>= min_success`` entries, derive:
       - ``name``: ``<preset>-pattern`` slug (validated against the
         registry's name regex so the user can save it).
       - ``description``: short text mentioning the preset + count.
       - ``prompt_template``: a generic template with ``{TASK}``,
         ``{PROJECT}``, ``{CONTEXT}`` placeholders that the user fills in.
       - ``agentic``/``structured``/``max_tokens``: best-effort inference
         from the post-mortem metadata when present.
       - ``sample_tasks``: up to 3 short task descriptions pulled from the
         matching tasks (when ``tasks`` is supplied) so the user can see
         what the skill would have handled.

    Returns the suggestions in descending order of success_count so the
    most-used patterns are at the top of the ``/learn`` table.
    """
    task_index: dict[str, dict[str, Any]] = {}
    for task in tasks or []:
        tid = str(task.get("id") or "").strip()
        if tid:
            task_index[tid] = task

    # Bucket: preset -> list of (post_mortem, task_description).
    grouped: dict[str, list[tuple[dict[str, Any], str]]] = defaultdict(list)
    for pm in post_mortems:
        if not isinstance(pm, dict):
            continue
        if not pm.get("success"):
            continue
        preset = str(pm.get("preset") or "").strip()
        if not preset:
            continue
        task_id = str(pm.get("task_id") or "").strip()
        task = task_index.get(task_id) or {}
        description = str(
            task.get("description") or task.get("title") or ""
        ).strip()
        grouped[preset].append((pm, description))

    suggestions: list[SkillSuggestion] = []
    index = 1
    for preset in sorted(grouped, key=lambda p: (-len(grouped[p]), p)):
        entries = grouped[preset]
        if len(entries) < min_success:
            continue
        # Majority vote on flags (only meaningful when present in any entry).
        agentic_votes = Counter(
            bool(pm.get("agentic"))
            for pm, _ in entries
            if pm.get("agentic") is not None
        )
        structured_votes = Counter(
            bool(pm.get("structured"))
            for pm, _ in entries
            if pm.get("structured") is not None
        )
        max_tokens_votes = Counter(
            int(pm["max_tokens"])
            for pm, _ in entries
            if pm.get("max_tokens") is not None
        )

        sample_tasks = [
            desc for _, desc in entries if desc
        ][:3]

        prompt_template = _build_prompt_template(preset)
        suggestions.append(
            SkillSuggestion(
                index=index,
                name=_slugify(f"{preset}-pattern"),
                description=(
                    f"Patron detectado en el preset '{preset}': "
                    f"{len(entries)} delegaciones exitosas. "
                    "Plantilla generalista basada en los task descriptions observados."
                ),
                preset=preset,
                prompt_template=prompt_template,
                agentic=bool(agentic_votes and agentic_votes.most_common(1)[0][0]),
                structured=bool(
                    structured_votes and structured_votes.most_common(1)[0][0]
                ),
                max_tokens=(
                    max_tokens_votes.most_common(1)[0][0]
                    if max_tokens_votes else None
                ),
                sample_tasks=sample_tasks,
                success_count=len(entries),
            )
        )
        index += 1

    return suggestions


def save_suggestion(
    suggestion: SkillSuggestion,
    *,
    skills_root: Path | None = None,
) -> Path:
    """Materialise a suggestion as a real ``DelegationSkill`` YAML on disk.

    Returns the path of the new YAML. Re-raises the registry's
    :class:`ValueError` on validation failure so the caller can surface a
    user-friendly error.
    """
    from lilith_skills.delegation_skills import DelegationSkill, DelegationSkillRegistry

    skill = DelegationSkill(
        name=suggestion.name,
        description=suggestion.description,
        preset=suggestion.preset,
        prompt_template=suggestion.prompt_template,
        agentic=suggestion.agentic,
        structured=suggestion.structured,
        max_tokens=suggestion.max_tokens,
    )
    registry = DelegationSkillRegistry(
        root=skills_root if skills_root is not None else None,
        seed_defaults=False,
    )
    return registry.save(skill)


# ── Helpers ─────────────────────────────────────────────────────────────


_NAME_SLUG_RE = re.compile(r"[^a-z0-9_-]+")


def _slugify(text: str) -> str:
    """Lowercase + collapse non-[a-z0-9_-] to '-'; trim trailing dashes.

    The registry's name regex is ``^[a-z0-9][a-z0-9_-]{0,63}$`` so the
    slug MUST start with a lowercase letter or digit.
    """
    s = text.strip().lower()
    s = _NAME_SLUG_RE.sub("-", s).strip("-")
    # Truncate to 63 chars while keeping the pattern suffix when possible.
    if len(s) > 63:
        s = s[:63].rstrip("-")
    if not s or not (s[0].isalnum()):
        s = (s or "skill").lstrip("-") or "skill"
    return s


def _build_prompt_template(preset: str) -> str:
    """Generic delegation template. All three required placeholders are present."""
    return (
        f"Eres el sub-agente '{preset}' de Hlidskjalf.\\n\\n"
        "Tarea: {TASK}\\n"
        "Proyecto: {PROJECT}\\n"
        "Contexto adicional:\\n{CONTEXT}\\n\\n"
        "Si tienes herramientas disponibles, usalas para verificar tu "
        "respuesta antes de devolverla."
    )
