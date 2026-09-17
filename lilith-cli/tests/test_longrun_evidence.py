"""Pruebas de `longrun/evidence.py`: transcripcion, redaccion e informe.

Todo con `tmp_path`. Sin red y sin sleep.

Los objetos de `longrun/contract.py` y `longrun/journal.py` los escriben otras
manos en paralelo. Aqui se usan dobles ligeros con los mismos atributos: si
`report` necesitara el tipo real, dejaria de servir para lo unico que importa,
que es escribir el informe con lo que haya.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from lilith_cli.longrun.evidence import SECTIONS, Evidence, redact, report

# ── Dobles ligeros ──────────────────────────────────────────────────

class FakeJournal:
    """Mismo `entries(kind=...)` que `longrun.journal.Journal`."""

    def __init__(self):
        self._entries: list[dict] = []

    def append(self, kind: str, text: str, *, evidence: dict | None = None) -> dict:
        entry = {
            "at": "2026-09-16T00:00:00Z",
            "kind": kind,
            "text": text,
            "evidence": evidence,
            "seq": len(self._entries) + 1,
        }
        self._entries.append(entry)
        return entry

    def entries(self, *, kind: str | None = None) -> list[dict]:
        return [e for e in self._entries if kind is None or e["kind"] == kind]


def fake_contract(**overrides):
    created = overrides.pop("created_at", "2026-09-16T00:00:00Z")
    budget = SimpleNamespace(
        max_wall_seconds=86400,
        max_legs=20,
        max_iterations_per_leg=12,
        max_tokens=500_000,
        max_usd=25.0,
    )
    contract = SimpleNamespace(
        run_id="GU-20260916-000000-evidencia",
        goal="Sostener la guardia de la fabrica sin humano delante",
        end_criterion="La suite de la fabrica pasa dos veces seguidas",
        verify="pytest -q",
        project_root="D:/workspace/lilith-cli",
        budget=budget,
        created_at=created,
        deadline="2026-09-17T00:00:00Z",
    )
    for key, value in overrides.items():
        setattr(contract, key, value)
    return contract


def fake_consumed(legs=2, iterations=7, tokens=12345, usd=1.5):
    return SimpleNamespace(legs=legs, iterations=iterations, tokens=tokens, usd=usd)


def sections(text: str) -> dict[str, str]:
    """Trocea el informe por sus encabezados `## `."""
    found: dict[str, str] = {}
    current = None
    body: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if current is not None:
                found[current] = "\n".join(body).strip()
            current = line[3:].strip()
            body = []
        elif current is not None:
            body.append(line)
    if current is not None:
        found[current] = "\n".join(body).strip()
    return found


# ── 1. Ida y vuelta, y el seq que sobrevive al relanzamiento ────────

def test_record_y_events_dan_la_vuelta_con_seq_monotono(tmp_path):
    path = tmp_path / "evidence.jsonl"
    evidence = Evidence(path)

    primero = evidence.record("tool_call", {"cmd": "pytest -q"})
    segundo = evidence.record("tool_result", {"exit_code": 0, "stdout": "11 passed"})

    assert [e["seq"] for e in (primero, segundo)] == [1, 2]
    leidos = evidence.events()
    assert [e["seq"] for e in leidos] == [1, 2]
    assert [e["kind"] for e in leidos] == ["tool_call", "tool_result"]
    assert leidos[1]["payload"]["stdout"] == "11 passed"
    assert leidos[0]["at"].endswith("Z")


def test_el_seq_continua_al_reabrir_el_fichero(tmp_path):
    """El proceso muere y se relanza: el contador no puede volver a 1."""
    path = tmp_path / "evidence.jsonl"
    primera_vida = Evidence(path)
    primera_vida.record("leg", {"n": 1})
    primera_vida.record("leg", {"n": 2})

    otra_vida = Evidence(path)  # objeto nuevo sobre el mismo fichero
    tercero = otra_vida.record("leg", {"n": 3})

    assert tercero["seq"] == 3
    assert [e["seq"] for e in otra_vida.events()] == [1, 2, 3]


# ── 2. Una linea a medias no tumba la lectura ───────────────────────

def test_una_linea_corrupta_no_rompe_events_y_queda_contada(tmp_path):
    path = tmp_path / "evidence.jsonl"
    evidence = Evidence(path)
    evidence.record("tool_call", {"cmd": "pytest -q"})

    # Una caida a mitad de escritura deja la ultima linea truncada.
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"at": "2026-09-16T00:00:00Z", "kind": "tool_res')

    leidos = evidence.events()
    assert len(leidos) == 1
    assert evidence.corrupt_lines == 1

    # Y lo siguiente que se escriba se sigue leyendo.
    Evidence(path).record("tool_result", {"exit_code": 0})
    reabierto = Evidence(path)
    assert [e["kind"] for e in reabierto.events()] == ["tool_call", "tool_result"]
    assert reabierto.corrupt_lines == 1


# ── 3-6. redact ─────────────────────────────────────────────────────

def test_redact_oculta_el_valor_de_api_key_y_deja_la_clave_visible():
    salida = redact({"api_key": "abc123def456", "host": "api.example.com"})
    assert salida == {"api_key": "<secreto: api_key>", "host": "api.example.com"}


def test_redact_reconoce_la_forma_de_la_clave_aunque_el_nombre_sea_inocente():
    salida = redact({
        "nota": "".join(("sk-", "live-", "9f8e7d6c5b4a392817")),
        "cabecera": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
    })
    assert salida["nota"] == "<secreto: api_key>"
    assert salida["cabecera"] == "<secreto: bearer>"


def test_redact_es_recursivo():
    salida = redact({"a": [{"token": "".join(("ghp_", "0123456789abcdef"))}, {"b": {"password": "hunter2"}}]})
    assert salida == {"a": [{"token": "<secreto: token>"}, {"b": {"password": "<secreto: password>"}}]}


def test_redact_no_destroza_datos_normales():
    datos = {
        "intentos": 3,
        "ok": True,
        "nada": None,
        "coste": 1.25,
        "ruta": "D:/workspace/internal/spec.md",
        "ruta_posix": "/home/game/proyectos/lilith/longrun/evidence",
        "frase": "la guardia lleva doce horas sin incidencias",
        "lista": [1, 2.5, "pytest -q"],
    }
    assert redact(datos) == datos


# ── 7. El test que justifica la pieza ───────────────────────────────

def test_el_fichero_en_disco_no_contiene_el_secreto(tmp_path):
    """Se comprueban los BYTES del fichero, no lo que devolvio `record`.

    Verificar reconstruyendo la propia intencion confirma la intencion, no el
    resultado: es exactamente el fallo que esta capa existe para detectar.
    """
    secreto = "".join(("sk-", "live-", "9f8e7d6c5b4a39281706aabbccddeeff"))
    path = tmp_path / "evidence.jsonl"
    evidence = Evidence(path)
    evidence.record("tool_call", {
        "api_key": secreto,
        "cmd": f"curl -H 'Authorization: Bearer {secreto}' https://api.example.com/v1/jobs",
        "proyecto": "D:/workspace/lilith-cli",
    })

    crudo = path.read_bytes()
    assert secreto.encode("utf-8") not in crudo
    assert b"sk-live" not in crudo          # ni truncado ni con prefijo
    assert b"9f8e7d6c" not in crudo
    assert b"api_key" in crudo              # la clave donde aparecio, si
    assert b"<secreto:" in crudo
    assert b"D:/workspace/lilith-cli" in crudo  # el contexto util sobrevive


# ── 8-11. report ────────────────────────────────────────────────────

def test_report_produce_las_cinco_secciones_en_orden(tmp_path):
    journal = FakeJournal()
    journal.append("goal", "Sostener la guardia")
    journal.append("verification", "pytest -q: 11 passed")
    evidence = Evidence(tmp_path / "evidence.jsonl")
    evidence.record("tool_result", {"exit_code": 0, "stdout": "11 passed"})

    texto = report(fake_contract(), fake_consumed(), journal, evidence,
                   now=datetime(2026, 9, 16, 1, 0, tzinfo=UTC))

    encabezados = [linea[3:].strip() for linea in texto.splitlines() if linea.startswith("## ")]
    assert encabezados == list(SECTIONS)
    assert encabezados == [
        "HECHO", "NO VERIFICADO", "BLOQUEADO POR EL OPERADOR",
        "SIGUIENTE PASO", "PRESUPUESTO CONSUMIDO",
    ]
    cuerpo = sections(texto)
    assert "11 passed" in cuerpo["HECHO"]
    assert "GU-20260916-000000-evidencia" in texto
    assert "3600 s de 86400 s" in cuerpo["PRESUPUESTO CONSUMIDO"]
    assert "12345" in cuerpo["PRESUPUESTO CONSUMIDO"]


def test_report_vacio_dice_explicitamente_que_no_hay_nada(tmp_path):
    """Un hueco en blanco se lee como 'todo bien'."""
    texto = report(fake_contract(), fake_consumed(0, 0, 0, 0.0),
                   FakeJournal(), Evidence(tmp_path / "evidence.jsonl"))

    cuerpo = sections(texto)
    assert list(cuerpo) == list(SECTIONS)
    for nombre in SECTIONS:
        assert cuerpo[nombre].strip(), f"la seccion {nombre} quedo en blanco"
    for nombre in ("HECHO", "NO VERIFICADO", "BLOQUEADO POR EL OPERADOR"):
        assert "No hay" in cuerpo[nombre]
    assert cuerpo["SIGUIENTE PASO"].count("\n") == 0  # un solo paso, concreto
    assert "pytest -q" in cuerpo["SIGUIENTE PASO"]


def test_report_con_decision_ajena_dice_que_decision_hace_falta(tmp_path):
    journal = FakeJournal()
    journal.append("escalation",
                   "Hace falta decidir si se publica el paquete en el indice interno")
    texto = report(fake_contract(), fake_consumed(), journal,
                   Evidence(tmp_path / "evidence.jsonl"), stop_reason="decision_ajena")

    bloqueado = sections(texto)["BLOQUEADO POR EL OPERADOR"]
    assert "Decision que hace falta" in bloqueado
    assert "se publica el paquete en el indice interno" in bloqueado
    assert "`decision_ajena`" in texto


def test_report_no_mete_en_hecho_el_autoinforme_de_una_herramienta(tmp_path):
    """El 2026-09-15 `batch_edit` declaro ocho ediciones y guardo una."""
    journal = FakeJournal()
    journal.append("decision", "batch_edit declaro applied=true para ocho ediciones")
    journal.append("finding", "vor.ps1 -Prime dijo Done tras fallar la contrasena")
    evidence = Evidence(tmp_path / "evidence.jsonl")
    evidence.record("tool_call", {"cmd": "batch_edit", "ediciones": 8})  # sin resultado

    cuerpo = sections(report(fake_contract(), fake_consumed(), journal, evidence))

    assert "No hay nada verificado" in cuerpo["HECHO"]
    assert "applied=true" not in cuerpo["HECHO"]
    assert "batch_edit" not in cuerpo["HECHO"]
    assert "applied=true" in cuerpo["NO VERIFICADO"]
    assert "batch_edit" in cuerpo["NO VERIFICADO"]


def test_report_no_filtra_secretos_del_diario(tmp_path):
    journal = FakeJournal()
    journal.append("failure", "el proveedor rechazo la clave " + "".join(("sk-", "live-", "9f8e7d6c5b4a392817")))
    texto = report(fake_contract(), fake_consumed(), journal,
                   Evidence(tmp_path / "evidence.jsonl"))
    assert "sk-live" not in texto
    assert "<secreto: api_key>" in texto


@pytest.mark.parametrize("payload", [None, "no soy un dict", 7])
def test_record_rechaza_un_payload_que_no_es_diccionario(tmp_path, payload):
    evidence = Evidence(tmp_path / "evidence.jsonl")
    if payload is None:
        assert evidence.record("nota", None)["payload"] == {}
    else:
        with pytest.raises(TypeError):
            evidence.record("nota", payload)
