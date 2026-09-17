"""Pruebas de ``lilith_cli.longrun.redaction``: la redaccion unica.

Habia dos redacciones, una por mano: la de la bitacora miraba solo el texto y
la de la evidencia miraba ademas el nombre de la clave. Aqui se fija lo que
ahora comparten, para que nadie vuelva a tener media.

Sin red, sin sleep, sin disco: esto es una funcion pura.
"""

from __future__ import annotations

import pytest
from lilith_cli.longrun import evidence as evidence_mod
from lilith_cli.longrun import journal as journal_mod
from lilith_cli.longrun.redaction import LABELS, redact, redact_text

# ── 1. Una sola implementacion, no dos ──────────────────────────────

def test_la_bitacora_y_la_evidencia_comparten_la_misma_redaccion():
    """Si esto se rompe, ha vuelto a haber dos niveles de proteccion."""
    from lilith_cli.longrun import redaction

    assert evidence_mod.redact is redaction.redact
    assert journal_mod.redact is redaction.redact
    assert journal_mod.redact_text is redaction.redact_text


# ── 2. Por NOMBRE de clave ──────────────────────────────────────────

@pytest.mark.parametrize(
    "clave,etiqueta",
    [
        ("api_key", "api_key"),
        ("apiKey", "api_key"),
        ("api-key", "api_key"),
        ("token", "token"),
        ("auth_token", "token"),
        ("password", "password"),
        ("contrasena", "password"),
        ("authorization", "bearer"),
        ("secret", "clave_probable"),
        ("credential", "clave_probable"),
        ("private_key", "clave_probable"),
    ],
)
def test_el_nombre_de_la_clave_basta_para_redactar(clave, etiqueta):
    """El valor no se parece a nada: solo lo delata donde estaba guardado."""
    assert redact({clave: "12345678"}) == {clave: f"<secreto: {etiqueta}>"}


def test_una_clave_con_valor_nulo_no_inventa_un_secreto():
    assert redact({"api_key": None}) == {"api_key": None}


# ── 3. Por FORMA del valor ──────────────────────────────────────────

FORMAS = [
    ("".join(("sk-", "live-", "9f8e7d6c5b4a392817065544")), "api_key"),
    ("".join(("ghp_", "0123456789abcdefghijABCDEFGHIJ0123")), "github_token"),
    ("".join(("glpat-", "ABCdef1234567890xyz")), "gitlab_token"),
    ("".join(("xoxb-", "123456789012-", "abcdefghijklmnopqrstuvwx")), "slack_token"),
    ("".join(("AKIA", "1234567890ABCDEF")), "aws_key"),
    (
        (
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0"
            ".dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
        ),
        "jwt",
    ),
    ("Bearer 9f8e7d6c5b4a392817065544aabbccdd", "bearer"),
    ("d41d8cd98f00b204e9800998ecf8427e1234abcd", "clave_probable"),
    (
        (
            "".join(("-----BEGIN OPENSSH ", "PRIVATE KEY-----\n")) +
            "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAABlwAAAAdzc2gt\n"
            "-----END OPENSSH PRIVATE KEY-----"
        ),
        "private_key",
    ),
]


@pytest.mark.parametrize("valor,etiqueta", FORMAS, ids=[f[1] for f in FORMAS])
def test_la_forma_del_valor_basta_aunque_la_clave_sea_inocente(valor, etiqueta):
    assert redact({"nota": valor}) == {"nota": f"<secreto: {etiqueta}>"}
    assert etiqueta in LABELS


# ``clave_probable`` no tiene forma propia: es "un blob largo sin espacios".
# Embebido en una frase es indistinguible de un SHA de git, y este taller
# escribe SHAs en la bitacora constantemente. Se queda en valor completo a
# proposito; los formatos con prefijo reconocible si se cazan dentro de texto.
EMBEBIBLES = [caso for caso in FORMAS if caso[1] != "clave_probable"]


@pytest.mark.parametrize("valor,etiqueta", EMBEBIBLES, ids=[f[1] for f in EMBEBIBLES])
def test_el_secreto_embebido_en_una_linea_se_sustituye_entero(valor, etiqueta):
    """El resto de la linea es contexto util; el secreto no sobrevive ni a trozos."""
    salida = redact_text(f"la herramienta dijo: {valor} (fin)")

    assert f"<secreto: {etiqueta}>" in salida
    for trozo in valor.split():
        assert trozo not in salida
    assert salida.startswith("la herramienta dijo: ")
    assert salida.endswith("(fin)")


def test_una_url_con_credenciales_conserva_esquema_y_host():
    salida = redact_text("git clone https://lilith:hunter2@git.example.com/fabrica.git")

    assert salida == (
        "git clone https://<secreto: url_credentials>@git.example.com/fabrica.git"
    )


def test_una_asignacion_inline_conserva_el_nombre_de_la_clave():
    """La regla que traia la bitacora: clave y valor en la MISMA cadena."""
    salida = redact_text('api_key="AKIA1234567890abcdefXYZ" en config.toml')

    assert "AKIA1234567890abcdefXYZ" not in salida
    assert salida.startswith('api_key="<secreto: ')
    assert salida.endswith(" en config.toml")


# ── 4. Lo que NO se toca ────────────────────────────────────────────

def test_los_datos_normales_salen_intactos():
    datos = {
        "intentos": 3,
        "ok": True,
        "nada": None,
        "coste": 1.25,
        "ruta": "D:/workspace/internal/x.md",
        "ruta_larga": "D:/workspace/internal/spec.md",
        "ruta_posix": "/home/game/proyectos/lilith/longrun/evidence",
        "frase": "la guardia lleva doce horas sin incidencias",
        "comando": "pytest -q",
        "url": "https://api.example.com/v1/jobs",
        "lista": [1, 2.5, "pytest -q"],
    }
    assert redact(datos) == datos


def test_una_tirada_del_mismo_caracter_no_es_material_criptografico():
    """``"E" * 90`` es hexadecimal valido y no es una clave de nada.

    Una clave hexadecimal real trae unos catorce simbolos distintos en sus
    primeros 32 caracteres; exigir variedad no deja pasar ninguna y salva el
    texto corriente de la bitacora, que si escribe tiradas asi.
    """
    assert redact_text("E" * 90) == "E" * 90
    assert redact_text("-" * 40) == "-" * 40


def test_redactar_dos_veces_da_lo_mismo():
    """La marca no puede confundirse con un secreto nuevo."""
    una = redact_text("clave " + "".join(("sk-", "live-", "9f8e7d6c5b4a392817065544")) + " en el log")
    assert redact_text(una) == una


def test_un_blob_suelto_solo_se_redacta_como_valor_completo():
    """Limite declarado: sin prefijo reconocible, solo cuenta el valor entero.

    Un SHA de git es 40 hexadecimales y aparece en el texto de la bitacora todo
    el rato. Redactarlo dentro de una frase destruiria evidencia util; como
    valor de una clave, en cambio, no hay nada que perder.
    """
    blob = "d41d8cd98f00b204e9800998ecf8427e1234abcd"

    assert redact({"nota": blob}) == {"nota": "<secreto: clave_probable>"}
    assert redact_text(f"el arbol quedo en {blob}") == f"el arbol quedo en {blob}"
