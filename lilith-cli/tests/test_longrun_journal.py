"""Pruebas de ``lilith_cli.longrun.journal``.

Sin red, sin sleep: todo ocurre contra ``tmp_path``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from lilith_cli.agent import _trim_history_to_budget
from lilith_cli.longrun.journal import EXCERPT_CHARS, KINDS, Journal


def _journal(tmp_path):
    return Journal(tmp_path / "run" / "journal.jsonl")


# -- 1. Validacion de kind -------------------------------------------


def test_append_rechaza_un_kind_desconocido(tmp_path):
    journal = _journal(tmp_path)

    with pytest.raises(ValueError, match="desconocido"):
        journal.append("chisme", "algo")

    assert not journal.path.exists()


def test_append_acepta_todos_los_kinds_del_contrato(tmp_path):
    journal = _journal(tmp_path)

    for kind in KINDS:
        entry = journal.append(kind, f"texto de {kind}")
        assert entry["kind"] == kind
        assert entry["at"].endswith("Z")

    assert len(journal.entries()) == len(KINDS)


# -- 2. seq monotono que sobrevive al relanzamiento -------------------


def test_seq_es_monotono_y_continua_al_reabrir_el_journal(tmp_path):
    path = tmp_path / "run" / "journal.jsonl"

    primero = Journal(path)
    assert primero.append("goal", "sostener la guardia")["seq"] == 1
    assert primero.append("finding", "el disco esta lleno")["seq"] == 2

    # El proceso muere y se relanza: un objeto nuevo sobre el mismo fichero.
    segundo = Journal(path)
    assert segundo.append("decision", "liberar espacio")["seq"] == 3

    assert [e["seq"] for e in segundo.entries()] == [1, 2, 3]


# -- 3. Filtrado y orden ---------------------------------------------


def test_entries_filtra_por_kind_y_sin_filtro_devuelve_todo_en_orden(tmp_path):
    journal = _journal(tmp_path)
    journal.append("goal", "objetivo")
    journal.append("finding", "hallazgo 1")
    journal.append("decision", "decision 1")
    journal.append("finding", "hallazgo 2")

    todo = journal.entries()
    assert [e["text"] for e in todo] == [
        "objetivo",
        "hallazgo 1",
        "decision 1",
        "hallazgo 2",
    ]

    hallazgos = journal.entries(kind="finding")
    assert [e["text"] for e in hallazgos] == ["hallazgo 1", "hallazgo 2"]


# -- 4. Fichero truncado por una caida --------------------------------


def test_una_linea_corrupta_no_rompe_entries_y_queda_contada(tmp_path):
    journal = _journal(tmp_path)
    journal.append("goal", "objetivo")
    journal.append("finding", "hallazgo 1")
    journal.append("finding", "hallazgo 2")

    lineas = journal.path.read_text(encoding="utf-8").splitlines()
    lineas.insert(2, '{"at": "2026-09-16T00:00:00Z", "kind": "fin')  # linea truncada
    journal.path.write_text("\n".join(lineas) + "\n", encoding="utf-8")

    leidas = journal.entries()

    assert [e["text"] for e in leidas] == ["objetivo", "hallazgo 1", "hallazgo 2"]
    assert journal.corrupt_lines == 1


# -- 5. El objetivo nunca se omite ------------------------------------


def test_digest_nunca_omite_el_objetivo_aunque_no_quepa(tmp_path):
    journal = _journal(tmp_path)
    journal.append("goal", "O" * 300)
    journal.append("finding", "HALLAZGO-IRRELEVANTE")

    texto = journal.digest(40)

    assert len(texto) <= 40
    assert texto.startswith("OBJETIVO: OOO")
    assert texto.endswith(" [...]")
    assert "HALLAZGO-IRRELEVANTE" not in texto
    assert "\n" not in texto


# -- 6. El tope se respeta siempre ------------------------------------


def test_digest_nunca_supera_max_chars(tmp_path):
    journal = _journal(tmp_path)
    journal.append("goal", "G" * 120)
    journal.append("finding", "F" * 90)
    journal.append("decision", "D" * 90)
    journal.append("failure", "X" * 90)
    journal.append("escalation", "E" * 90)

    for max_chars in (0, 1, 5, 6, 7, 40, 131, 132, 200, 500, 5000):
        texto = journal.digest(max_chars)
        assert len(texto) <= max_chars, (max_chars, len(texto))

    # Con presupuesto de sobra entra todo.
    completo = journal.digest(5000)
    assert "G" * 120 in completo
    assert "E" * 90 in completo


def test_digest_de_un_journal_vacio_es_cadena_vacia(tmp_path):
    assert _journal(tmp_path).digest(500) == ""


# -- 7 y 8. Fallos abiertos frente a hallazgos ------------------------


def test_digest_prefiere_un_fallo_abierto_a_un_hallazgo_antiguo(tmp_path):
    journal = _journal(tmp_path)
    journal.append("goal", "G")  # "OBJETIVO: G" -> 11 caracteres
    journal.append("finding", "H" * 30)  # "[finding] " + 30 -> 40
    journal.append("failure", "F" * 30)  # "[failure] " + 30 -> 40

    # 11 del objetivo + 1 salto de linea + 40 de UNA sola entrada.
    texto = journal.digest(52)

    assert "F" * 30 in texto
    assert "H" * 30 not in texto


def test_una_verification_posterior_cierra_el_fallo_y_lo_desprioriza(tmp_path):
    journal = _journal(tmp_path)
    journal.append("goal", "G")
    journal.append("failure", "F" * 30)  # "[failure] " + 30 -> 40
    journal.append("verification", "V" * 25)  # "[verification] " + 25 -> 40
    journal.append("finding", "H" * 30)  # "[finding] " + 30 -> 40

    texto = journal.digest(52)

    # El fallo ya no cuenta como abierto: gana el hallazgo.
    assert "H" * 30 in texto
    assert "F" * 30 not in texto


# -- 9 y 10. absorb_dropped -------------------------------------------


def test_absorb_dropped_con_lista_vacia_no_escribe_nada(tmp_path):
    journal = _journal(tmp_path)

    assert journal.absorb_dropped([]) is None
    assert not journal.path.exists()
    assert journal.entries() == []


def test_absorb_dropped_registra_roles_herramienta_y_extracto(tmp_path):
    journal = _journal(tmp_path)
    resultado = "RESULTADO-" + ("Z" * 500)
    descartados = [
        {"role": "user", "content": "primera pregunta"},
        {"role": "user", "content": "segunda pregunta"},
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "name": "file_read",
            "content": resultado,
        },
    ]

    entrada = journal.absorb_dropped(descartados)

    assert entrada["kind"] == "compaction"
    assert journal.entries(kind="compaction") == [entrada]

    evidencia = entrada["evidence"]
    assert evidencia["mensajes"] == 3
    assert evidencia["por_rol"] == {"user": 2, "tool": 1}
    assert evidencia["herramientas"] == ["file_read"]
    assert len(evidencia["resultados"]) == 1
    extracto = evidencia["resultados"][0]["extracto"]
    assert evidencia["resultados"][0]["herramienta"] == "file_read"
    assert extracto == resultado[:EXCERPT_CHARS]
    assert len(extracto) == 200

    # Y quedo en disco, no solo en el valor devuelto.
    en_disco = json.loads(journal.path.read_text(encoding="utf-8").splitlines()[-1])
    assert en_disco["evidence"]["resultados"][0]["extracto"] == extracto


def test_absorb_dropped_deduce_el_nombre_desde_los_tool_calls(tmp_path):
    journal = _journal(tmp_path)
    descartados = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_9",
                    "type": "function",
                    "function": {"name": "search_files", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_9", "content": "tres coincidencias"},
    ]

    entrada = journal.absorb_dropped(descartados)

    assert entrada["evidence"]["herramientas"] == ["search_files"]
    assert entrada["evidence"]["resultados"][0]["herramienta"] == "search_files"
    assert "search_files" in entrada["text"]


# -- 11. El test que justifica la pieza -------------------------------


def test_lo_que_el_recorte_real_tira_sigue_estando_en_el_digest(tmp_path):
    """Simula ``agent._trim_history_to_budget`` y comprueba que no se pierde."""
    journal = _journal(tmp_path)
    journal.append("goal", "Dejar la suite verde y el informe escrito")
    journal.append("decision", "Arrancar por los tests de contrato")

    history = [
        {"role": "user", "content": "A" * 400},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "file_read", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": "PISTA-CLAVE: el venv apunta a un interprete movido. " + "B" * 400,
        },
        {"role": "assistant", "content": "C" * 200},
        {"role": "user", "content": "sigue"},
    ]

    # El recorte real: recorta los resultados de herramienta y luego tira los
    # mensajes mas viejos con pop(0) hasta que el resto cabe.
    trimmed = _trim_history_to_budget(history, 300, 5_000)
    dropped = history[: len(history) - len(trimmed)]
    assert dropped, "el recorte deberia haber tirado algo"

    entrada = journal.absorb_dropped(dropped)
    assert entrada is not None

    texto = journal.digest(2_000)

    # El objetivo sigue ahi...
    assert "Dejar la suite verde y el informe escrito" in texto
    # ...y lo descartado dejo huella.
    assert "Recorte de contexto" in texto
    assert str(len(dropped)) in texto

    # La evidencia guarda el rastro fino de lo que el historial ya no tiene.
    evidencia = journal.entries(kind="compaction")[0]["evidence"]
    assert evidencia["mensajes"] == len(dropped)
    assert sum(evidencia["por_rol"].values()) == len(dropped)
    assert evidencia["resultados"], "el resultado de herramienta era uno de los tirados"
    assert "PISTA-CLAVE" in evidencia["resultados"][0]["extracto"]
    assert "file_read" in evidencia["herramientas"]


# -- Extra: regla del taller, ni un secreto en la bitacora ------------


def test_la_bitacora_guarda_el_tipo_del_secreto_pero_nunca_el_valor(tmp_path):
    journal = _journal(tmp_path)

    journal.absorb_dropped(
        [
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "name": "file_read",
                "content": 'api_key="AKIA1234567890abcdefXYZ" en config.toml',
            }
        ]
    )

    crudo = journal.path.read_text(encoding="utf-8")
    assert "AKIA1234567890abcdefXYZ" not in crudo
    assert "<secreto:" in crudo
    assert "config.toml" in crudo


# -- Redaccion: el nombre de la clave tambien delata un secreto -------
#
# Estas pruebas leen los BYTES de journal.jsonl, no el dict que devolvio
# ``append``: verificar reconstruyendo la propia intencion confirma la
# intencion, no el resultado, y lo que hay que demostrar es que el secreto no
# llego al disco.


def _bytes_de(journal) -> bytes:
    return journal.path.read_bytes()


def test_un_secreto_nombrado_por_la_clave_no_llega_al_disco(tmp_path):
    """El fallo que justifica la tanda: el valor no se parecia a nada.

    ``_scrub`` solo miraba el texto de cada cadena; el nombre de la clave del
    diccionario no lo miraba nadie, asi que esto escribia la clave entera.
    """
    journal = _journal(tmp_path)

    aws_key = "".join(("AKIA", "1234567890ABCDEF"))
    journal.append(
        "finding",
        "fallo de auth",
        # "lilith-2026" no se parece a un secreto: no tiene prefijo, ni largo,
        # ni entropia. Lo unico que lo delata es DONDE estaba guardado, que es
        # justo lo que no se miraba. Sin el, este test lo salvaria la forma de
        # la clave de AWS y no probaria lo que dice probar.
        evidence={"api_key": aws_key, "token": "lilith-2026"},
    )

    crudo = _bytes_de(journal)
    assert aws_key.encode("utf-8") not in crudo
    assert b"AKIA" not in crudo  # ni truncado ni con prefijo
    assert b"lilith-2026" not in crudo
    assert b"api_key" in crudo  # la clave donde aparecio, si
    assert b"<secreto: api_key>" in crudo
    assert b"<secreto: token>" in crudo
    assert b"fallo de auth" in crudo


def test_un_secreto_anidado_en_dicts_y_listas_no_llega_al_disco(tmp_path):
    journal = _journal(tmp_path)
    secreto = "s3cr3t0-de-cabecera-1234567890"

    journal.append(
        "failure",
        "el proveedor rechazo la peticion",
        evidence={
            "auth": {"headers": {"authorization": f"Bearer {secreto}"}},
            "intentos": [
                {"password": "hunter2-del-operador"},
                {"nota": "el segundo intento tampoco entro"},
            ],
        },
    )

    crudo = _bytes_de(journal)
    assert secreto.encode("utf-8") not in crudo
    assert b"hunter2" not in crudo
    assert b"<secreto: bearer>" in crudo
    assert b"<secreto: password>" in crudo
    assert b"el segundo intento tampoco entro" in crudo


# Un caso por formato de los que antes se escapaban. El valor va EMBEBIDO en
# una frase, que es como llega de verdad desde un resultado de herramienta.
FORMATOS = [
    ("aws", "".join(("AKIA", "1234567890ABCDEF")), b"aws_key"),
    (
        "jwt",
        (
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0"
            ".dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
        ),
        b"jwt",
    ),
    (
        "clave_privada",
        (
            "".join(("-----BEGIN RSA ", "PRIVATE KEY-----\n")) +
            "MIIEpAIBAAKCAQEA7sHtGpqT4mZ1kQv9Xw2nB3cD4eF5gH6iJ7kL8mN9oP0qR1sT\n"
            "-----END RSA PRIVATE KEY-----"
        ),
        b"private_key",
    ),
    ("slack", "".join(("xoxb-", "123456789012-", "abcdefghijklmnopqrstuvwx")), b"slack_token"),
    ("gitlab", "".join(("glpat-", "ABCdef1234567890xyz")), b"gitlab_token"),
    ("github", "".join(("ghp_", "0123456789abcdefghijABCDEFGHIJ0123")), b"github_token"),
    ("openai", "".join(("sk-", "live-", "9f8e7d6c5b4a392817065544")), b"api_key"),
]


@pytest.mark.parametrize("nombre,valor,etiqueta", FORMATOS, ids=[f[0] for f in FORMATOS])
def test_cada_formato_de_secreto_se_sustituye_antes_de_tocar_el_disco(
    tmp_path, nombre, valor, etiqueta
):
    journal = _journal(tmp_path)

    journal.append(
        "finding",
        f"la herramienta devolvio {valor} al leer config.toml",
        evidence={"salida": f"linea 3: {valor} (fin)"},
    )

    crudo = _bytes_de(journal)
    trozo = max(valor.replace("\n", " ").split(), key=len)
    assert trozo.encode("utf-8") not in crudo, f"{nombre}: el valor llego al disco"
    assert etiqueta in crudo
    assert b"config.toml" in crudo  # el contexto util sobrevive


def test_una_url_con_credenciales_pierde_la_clave_y_conserva_el_host(tmp_path):
    journal = _journal(tmp_path)

    journal.append(
        "decision",
        "clonar desde https://lilith:hunter2-del-operador@git.example.com/fabrica.git",
    )

    crudo = _bytes_de(journal)
    assert b"hunter2-del-operador" not in crudo
    assert b"<secreto: url_credentials>" in crudo
    assert b"git.example.com/fabrica.git" in crudo


def test_los_datos_normales_llegan_al_disco_intactos(tmp_path):
    journal = _journal(tmp_path)
    datos = {
        "ruta": "D:/workspace/internal/x.md",
        "ruta_posix": "/home/game/proyectos/lilith/longrun/evidence",
        "intentos": 3,
        "coste": 1.25,
        "ok": True,
        "nada": None,
        "frase": "la guardia lleva doce horas sin incidencias",
        "lista": [1, 2.5, "pytest -q"],
    }

    journal.append("verification", "pytest -q: 31 passed", evidence=datos)

    en_disco = json.loads(_bytes_de(journal).decode("utf-8").splitlines()[-1])
    assert en_disco["evidence"] == datos
    assert en_disco["text"] == "pytest -q: 31 passed"
    assert "<secreto:" not in _bytes_de(journal).decode("utf-8")


# -- Storage compaction: archive completo + journal activo acotado ---


def test_storage_compaction_archives_full_journal_and_preserves_critical_entries(tmp_path):
    import hashlib

    journal = _journal(tmp_path)
    journal.append("goal", "MISSION-GOAL")
    journal.append("failure", "closed failure")
    journal.append("verification", "closed failure verified")
    journal.append("decision", "keep this decision")
    for index in range(20):
        journal.append("finding", f"old finding {index}")
    journal.append("failure", "OPEN-FAILURE")
    journal.append("escalation", "OVERLORD-ESCALATION")
    for index in range(10):
        journal.append("finding", f"recent finding {index}")

    original = journal.path.read_bytes()
    original_entries = len(journal.entries())
    result = journal.compact_storage(max_entries=10, keep_recent=5)

    assert result["compacted"] is True
    archive = Path(result["archive"])
    assert archive.is_file()
    assert archive.read_bytes() == original
    assert result["sha256"] == hashlib.sha256(original).hexdigest()
    assert result["original_entries"] == original_entries
    active = journal.entries()
    texts = [str(row.get("text") or "") for row in active]
    assert "MISSION-GOAL" in texts
    assert "OPEN-FAILURE" in texts
    assert "OVERLORD-ESCALATION" in texts
    assert any(row.get("kind") == "verification" for row in active)
    assert any(row.get("kind") == "compaction" for row in active)
    assert len(active) < original_entries

    highest = max(int(row.get("seq", 0)) for row in active)
    appended = Journal(journal.path).append("finding", "after compaction")
    assert appended["seq"] == highest + 1


def test_storage_compaction_refuses_to_hide_corrupt_lines(tmp_path):
    journal = _journal(tmp_path)
    for index in range(12):
        journal.append("finding", f"entry {index}")
    with journal.path.open("a", encoding="utf-8") as handle:
        handle.write('{"kind":"finding"')

    result = journal.compact_storage(max_entries=5, keep_recent=2)

    assert result["compacted"] is False
    assert result["reason"] == "corrupt_lines_present"
    assert result["corrupt_lines"] == 1
    assert not (journal.path.parent / "archive").exists()
