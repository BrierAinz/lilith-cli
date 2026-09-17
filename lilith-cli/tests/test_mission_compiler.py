from pathlib import Path

import pytest
from lilith_cli.mission.compiler import MissionCompiler
from lilith_cli.mission.model import MissionQuestion, MissionRoute


def _routes() -> list[MissionRoute]:
    return [
        MissionRoute(
            "safe",
            "Migración gradual",
            "Preserva compatibilidad y permite verificar cada tramo.",
            risks=("más pasos",),
            estimated_cost="medium",
        ),
        MissionRoute(
            "clean",
            "Reemplazo directo",
            "Reduce deuda de inmediato a costa de más riesgo.",
            risks=("breaking changes",),
            estimated_cost="low",
        ),
    ]


def test_compiler_selects_recommended_route_without_material_question(tmp_path: Path) -> None:
    result = MissionCompiler().compile(
        objective="Unificar dos implementaciones",
        project_root=str(tmp_path),
        success_criteria=["una sola implementación activa", "tests verdes"],
        routes=_routes(),
        recommended_route="safe",
    )
    assert result.mission.status == "ready"
    assert result.mission.selected_route == "safe"
    assert not result.needs_operator


def test_blocking_question_pauses_route_selection(tmp_path: Path) -> None:
    question = MissionQuestion(
        "¿Qué compatibilidad quieres conservar?",
        ("Compatibilidad total", "Migración gradual", "Romper compatibilidad"),
        "La respuesta cambia el plan de migración y el riesgo.",
        recommended_option=1,
    )
    result = MissionCompiler().compile(
        objective="Remasterizar CLI",
        project_root=str(tmp_path),
        success_criteria=["UX nueva implementada"],
        questions=[question],
        routes=_routes(),
        recommended_route="safe",
    )
    assert result.needs_operator
    assert result.mission.status == "awaiting_operator"
    assert result.mission.selected_route is None
    assert result.mission.questions[0].recommended_option == 1


def test_compiler_rejects_fake_or_duplicated_choices(tmp_path: Path) -> None:
    question = MissionQuestion(
        "¿Ruta?",
        ("A", "a"),
        "Cambia arquitectura.",
    )
    with pytest.raises(ValueError, match="distinct"):
        MissionCompiler().compile(
            objective="x",
            project_root=str(tmp_path),
            success_criteria=["done"],
            questions=[question],
        )
