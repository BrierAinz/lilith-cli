from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .model import (
    MissionBudget,
    MissionQuestion,
    MissionRoute,
    MissionSpec,
)


@dataclass(frozen=True)
class CompileResult:
    mission: MissionSpec
    needs_operator: bool
    recommended_route: str | None


class MissionCompiler:
    """Validate a model-proposed mission before any execution is allowed."""

    MAX_QUESTIONS = 5
    MAX_ROUTES = 4

    def compile(
        self,
        *,
        objective: str,
        project_root: str,
        success_criteria: Iterable[str],
        constraints: Iterable[str] = (),
        assumptions: Iterable[str] = (),
        questions: Iterable[MissionQuestion] = (),
        routes: Iterable[MissionRoute] = (),
        recommended_route: str | None = None,
        budget: MissionBudget | None = None,
        authority_profile: str = "sovereign-local",
    ) -> CompileResult:
        root = str(Path(project_root).expanduser().resolve())
        criteria = self._clean(success_criteria)
        if not criteria:
            raise ValueError("mission requires explicit success criteria")
        question_list = list(questions)
        route_list = list(routes)
        if len(question_list) > self.MAX_QUESTIONS:
            raise ValueError("too many operator questions for one mission intake")
        if len(route_list) > self.MAX_ROUTES:
            raise ValueError("too many candidate routes for one mission intake")
        self._validate_questions(question_list)
        self._validate_routes(route_list, recommended_route)

        blocking = [item for item in question_list if item.blocking]
        selected_route = None if blocking else recommended_route
        mission = MissionSpec(
            objective=objective.strip(),
            project_root=root,
            success_criteria=criteria,
            constraints=self._clean(constraints),
            assumptions=self._clean(assumptions),
            questions=question_list,
            routes=route_list,
            selected_route=selected_route,
            authority_profile=authority_profile,
            budget=budget or MissionBudget(),
            status="awaiting_operator" if blocking else "ready",
        )
        mission.validate()
        return CompileResult(
            mission=mission,
            needs_operator=bool(blocking),
            recommended_route=recommended_route,
        )

    @staticmethod
    def _clean(values: Iterable[str]) -> list[str]:
        return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))

    @classmethod
    def _validate_questions(cls, questions: list[MissionQuestion]) -> None:
        for question in questions:
            if not question.question.strip().endswith("?"):
                raise ValueError("operator question must be a complete question")
            if not question.reason.strip():
                raise ValueError("operator question requires a material reason")
            if not 2 <= len(question.options) <= 5:
                raise ValueError("operator question requires 2..5 distinct options")
            normalized = [item.strip().casefold() for item in question.options]
            if any(not item for item in normalized) or len(set(normalized)) != len(normalized):
                raise ValueError("operator question options must be non-empty and distinct")
            if question.recommended_option is not None and not (
                0 <= question.recommended_option < len(question.options)
            ):
                raise ValueError("recommended question option is out of range")

    @classmethod
    def _validate_routes(
        cls,
        routes: list[MissionRoute],
        recommended_route: str | None,
    ) -> None:
        ids = [route.route_id.strip() for route in routes]
        if len(ids) != len(set(ids)) or any(not ident for ident in ids):
            raise ValueError("mission routes require unique non-empty ids")
        for route in routes:
            if not route.label.strip() or not route.rationale.strip():
                raise ValueError("each route requires label and rationale")
        if recommended_route is not None and recommended_route not in set(ids):
            raise ValueError("recommended route is not one of the candidate routes")
