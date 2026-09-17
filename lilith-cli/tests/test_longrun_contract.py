"""Pruebas de ``lilith_cli.longrun.contract``.

Todo contra ``tmp_path``, con ``datetime`` explicitos. Sin red y sin ``sleep``:
un test que duerme para medir el reloj mide el planificador del sistema
operativo, no el contrato.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

import pytest

from lilith_cli.longrun.contract import (
    Budget,
    Consumed,
    RunContract,
    exhausted,
    remaining,
    wall_seconds,
)


T0 = datetime(2026, 9, 16, 4, 30, 0, tzinfo=timezone.utc)

RUN_ID_RE = re.compile(r"^GU-\d{8}-\d{6}-[a-z0-9-]+$")


def _budget(**overrides) -> Budget:
    campos = {
        "max_wall_seconds": 86400,
        "max_legs": 10,
        "max_iterations_per_leg": 12,
        "max_tokens": None,
        "max_usd": None,
    }
    campos.update(overrides)
    return Budget(**campos)


def _contract(tmp_path, *, goal="Sostener la guardia de la fabrica", now=T0, **budget_kw):
    return RunContract.create(
        goal=goal,
        end_criterion="La suite de la fabrica pasa dos veces seguidas",
        verify="pytest -q",
        project_root=str(tmp_path),
        budget=_budget(**budget_kw),
        now=now,
    )


def _parse(texto: str) -> datetime:
    return datetime.fromisoformat(texto.replace("Z", "+00:00"))


# -- 1. Alta: run_id y deadline --------------------------------------


def test_create_fija_run_id_y_deadline(tmp_path):
    contract = _contract(tmp_path, goal="Arreglar el bot de Telegram", max_wall_seconds=3600)

    assert RUN_ID_RE.match(contract.run_id), contract.run_id
    assert contract.run_id.startswith("GU-20260916-043000-")
    assert contract.created_at == "2026-09-16T04:30:00Z"
    assert contract.deadline.endswith("Z")

    creado = _parse(contract.created_at)
    plazo = _parse(contract.deadline)
    assert plazo - creado == timedelta(seconds=contract.budget.max_wall_seconds)


# -- 2. Slug saneado --------------------------------------------------


def test_el_slug_se_sanea(tmp_path):
    contract = _contract(tmp_path, goal="Arreglar  EL bot!! de Telegram")
    slug = contract.run_id.split("-", 3)[3]

    assert re.fullmatch(r"[a-z0-9-]+", slug), slug
    assert "--" not in slug
    assert not slug.startswith("-") and not slug.endswith("-")
    assert len(slug) <= 24


def test_un_objetivo_sin_letras_cae_en_guardia(tmp_path):
    contract = _contract(tmp_path, goal="!!! ??? ...")

    assert contract.run_id.endswith("-guardia")


# -- 3. Ida y vuelta por disco ----------------------------------------


def test_save_y_load_devuelven_el_mismo_contrato(tmp_path):
    original = _contract(tmp_path, max_tokens=500_000, max_usd=25.0)
    original.validate()

    path = original.save(tmp_path)
    assert path == tmp_path / "contract.json"
    assert path.exists()

    recargado = RunContract.load(tmp_path)

    assert recargado == original
    assert recargado.budget == original.budget
    assert recargado.budget.max_tokens == 500_000
    assert recargado.budget.max_usd == 25.0


def test_load_se_queja_en_espanol_si_falta_o_esta_roto(tmp_path):
    with pytest.raises(ValueError, match="no existe"):
        RunContract.load(tmp_path)

    (tmp_path / "contract.json").write_text("{esto no es json", encoding="utf-8")
    with pytest.raises(ValueError, match="no es JSON valido"):
        RunContract.load(tmp_path)

    data = _contract(tmp_path).as_dict()
    del data["budget"]["max_legs"]
    (tmp_path / "contract.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="max_legs"):
        RunContract.load(tmp_path)


# -- 4. Validacion ----------------------------------------------------


def test_validate_rechaza_los_contratos_que_no_se_sostienen(tmp_path):
    bueno = _contract(tmp_path)
    bueno.validate()  # el caso sano no lanza

    # goal vacio
    with pytest.raises(ValueError, match="objetivo"):
        _contract(tmp_path, goal="").validate()

    # end_criterion en blanco
    import dataclasses

    with pytest.raises(ValueError, match="criterio de fin"):
        dataclasses.replace(bueno, end_criterion="   ").validate()

    # project_root inexistente
    with pytest.raises(ValueError, match="directorio existente"):
        dataclasses.replace(bueno, project_root=str(tmp_path / "no-existe")).validate()

    # max_wall_seconds 0
    with pytest.raises(ValueError, match="max_wall_seconds"):
        _contract(tmp_path, max_wall_seconds=0).validate()

    # max_usd negativo
    with pytest.raises(ValueError, match="max_usd"):
        _contract(tmp_path, max_usd=-1.0).validate()


# -- 5. Consumo -------------------------------------------------------


def test_consumed_ausente_da_ceros_y_la_ida_y_vuelta_conserva(tmp_path):
    vacio = Consumed.load(tmp_path)
    assert (vacio.legs, vacio.iterations, vacio.tokens, vacio.usd) == (0, 0, 0, 0.0)

    gastado = Consumed(legs=3, iterations=27, tokens=120_000, usd=4.25)
    path = gastado.save(tmp_path)
    assert path == tmp_path / "consumed.json"

    recargado = Consumed.load(tmp_path)
    assert recargado == gastado

    (tmp_path / "consumed.json").write_text("{roto", encoding="utf-8")
    with pytest.raises(ValueError, match="no es JSON valido"):
        Consumed.load(tmp_path)


# -- 6. Reloj ---------------------------------------------------------


def test_wall_seconds_cuenta_desde_created_at(tmp_path):
    contract = _contract(tmp_path)

    assert wall_seconds(contract, T0 + timedelta(minutes=90)) == 5400.0


# -- 7. El reloj gana ------------------------------------------------


def test_exhausted_reloj_con_el_resto_intacto(tmp_path):
    contract = _contract(tmp_path, max_wall_seconds=3600)
    intacto = Consumed()

    assert exhausted(contract, intacto, T0 + timedelta(minutes=59)) is None
    assert exhausted(contract, intacto, T0 + timedelta(hours=2)) == "reloj"


# -- 8. Tramos, tokens, gasto y su orden ------------------------------


def test_exhausted_cubre_los_cuatro_topes_y_su_orden(tmp_path):
    contract = _contract(tmp_path, max_legs=5, max_tokens=1000, max_usd=2.0)
    dentro = T0 + timedelta(minutes=1)

    assert exhausted(contract, Consumed(), dentro) is None
    assert exhausted(contract, Consumed(legs=5), dentro) == "tramos"
    assert exhausted(contract, Consumed(tokens=1000), dentro) == "tokens"
    assert exhausted(contract, Consumed(usd=2.0), dentro) == "gasto"

    # Prioridad: el reloj gana aunque tambien se hayan agotado los tramos.
    agotado = Consumed(legs=5, tokens=1000, usd=2.0)
    assert exhausted(contract, agotado, T0 + timedelta(days=2)) == "reloj"
    # Y los tramos ganan a los tokens y al gasto.
    assert exhausted(contract, agotado, dentro) == "tramos"
    assert exhausted(contract, Consumed(tokens=1000, usd=2.0), dentro) == "tokens"


# -- 9. Un tope ausente no limita -------------------------------------


def test_un_tope_ausente_no_limita(tmp_path):
    contract = _contract(tmp_path, max_tokens=None, max_usd=None)
    derrochador = Consumed(legs=1, iterations=3, tokens=9_000_000, usd=1234.56)

    assert exhausted(contract, derrochador, T0 + timedelta(minutes=5)) is None

    restos = remaining(contract, derrochador, T0 + timedelta(minutes=5))
    assert restos["tokens"] is None
    assert restos["usd"] is None


# -- 10. El reloj sobrevive a un reinicio -----------------------------


def test_el_reloj_sobrevive_a_un_reinicio(tmp_path):
    """La prueba que justifica la pieza.

    El proceso muere, la maquina se reinicia y otro proceso carga el contrato
    desde disco. Si el reloj se acumulara en memoria, aqui volveria a cero y la
    guardia se pasaria del presupuesto sin enterarse.
    """
    original = _contract(tmp_path, max_wall_seconds=86400)
    original.save(tmp_path)

    despues = T0 + timedelta(hours=20)
    esperado = wall_seconds(original, despues)

    # Otro proceso, sin nada en memoria: solo el fichero.
    recargado = RunContract.load(tmp_path)

    assert wall_seconds(recargado, despues) == esperado == 72000.0
    assert exhausted(recargado, Consumed(), despues) is None
    assert exhausted(recargado, Consumed(), T0 + timedelta(hours=25)) == "reloj"


# -- 11. Restos no negativos ------------------------------------------


def test_remaining_nunca_es_negativo(tmp_path):
    contract = _contract(tmp_path, max_wall_seconds=3600, max_legs=2,
                         max_tokens=1000, max_usd=2.0)
    pasado = Consumed(legs=7, iterations=40, tokens=9999, usd=50.0)

    restos = remaining(contract, pasado, T0 + timedelta(days=3))

    assert restos == {"segundos": 0.0, "tramos": 0, "tokens": 0, "usd": 0.0}

    a_medias = remaining(contract, Consumed(legs=1, tokens=250, usd=0.5),
                         T0 + timedelta(minutes=10))
    assert a_medias == {"segundos": 3000.0, "tramos": 1, "tokens": 750, "usd": 1.5}
    assert all(
        valor is None or valor >= 0 for valor in a_medias.values()
    )
