"""Redaccion de secretos, unica para toda la guardia larga.

Por que existe: habia dos. ``longrun/journal.py`` traia un ``_scrub`` que solo
miraba el TEXTO de cada cadena, y ``longrun/evidence.py`` un ``redact`` que
ademas mira el NOMBRE DE LA CLAVE. Las dos escriben JSONL a disco y las dos
citan la misma regla dura del taller, asi que la mas debil era una grieta con
forma de aprobado: ``journal.append(..., evidence={"api_key": "AKIA..."})``
escribia la clave entera, en claro, porque el valor por si solo no se parecia a
nada.

Regla de seguridad:

    Se informa ruta y tipo, nunca el valor.

De ahi salen las tres decisiones de este modulo:

* **Por nombre de clave y por forma del valor.** Cualquiera de las dos basta.
  Un secreto con nombre inocente lo coge la forma; un secreto con forma
  inocente lo coge el nombre.
* **Nada se escribe truncado ni con prefijo.** Media clave sigue siendo una
  filtracion: cuando algo es un secreto se sustituye ENTERO por su etiqueta.
* **El contexto util sobrevive.** Numeros, booleanos, ``None``, rutas de
  Windows y POSIX, y frases corrientes salen intactos, y de un tramo con
  secreto se conserva lo que no lo es (el nombre de la clave, el esquema y el
  host de una URL, el resto de la linea de comando).

Etiquetas. Se adopta el vocabulario de ``evidence.py`` —identificadores cortos
en snake_case— y NO el de ``journal.py`` (frases en espanol como
``<secreto: clave de OpenAI>``), por dos razones: tres de esas etiquetas ya
estaban fijadas por pruebas de la capa de evidencia, y una etiqueta corta se
compara y se agrega mejor en un informe que una frase. Ninguna regla se
perdio en la mudanza: las de la bitacora estan todas aqui, con etiqueta nueva.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

__all__ = ["LABELS", "mark", "redact", "redact_text"]


# ── Vocabulario ─────────────────────────────────────────────────────

LABELS = (
    "api_key",          # sk-/pk-/rk-, y todo `api_key=...` inline
    "token",            # nombre de clave generico
    "password",         # nombre de clave generico
    "bearer",           # cabecera Authorization
    "clave_probable",   # material criptografico por forma, sin tipo claro
    "github_token",     # ghp_/gho_/ghu_/ghs_/ghr_
    "gitlab_token",     # glpat-
    "slack_token",      # xoxa-/xoxb-/xoxp-/xoxr-/xoxs-
    "aws_key",          # AKIA... y hermanos
    "jwt",              # eyJ........
    "private_key",      # bloque PEM -----BEGIN ... PRIVATE KEY-----
    "url_credentials",  # https://usuario:clave@host
)


def mark(label: str) -> str:
    """Marca que sustituye a un secreto: tipo, nunca valor."""
    return f"<secreto: {label}>"


# ── Redaccion por NOMBRE de clave ───────────────────────────────────
#
# El orden importa: ``api_token`` no debe etiquetarse como ``api_key`` ni al
# reves, asi que las claves compuestas se miran antes que las simples.

_KEY_LABELS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("api_key", ("api_key", "apikey", "api-key")),
    ("token", ("token",)),
    ("password", ("password", "passwd", "contrasena", "contraseña")),
    ("bearer", ("authorization",)),
    (
        "clave_probable",
        ("secret", "secreto", "credential", "credencial", "private_key", "privatekey"),
    ),
)


def _key_label(key) -> str | None:
    if not isinstance(key, str):
        return None
    lowered = key.lower()
    for label, needles in _KEY_LABELS:
        if any(needle in lowered for needle in needles):
            return label
    return None


# ── Redaccion por FORMA del valor ───────────────────────────────────

_HEX_KEY = re.compile(r"^[0-9a-fA-F]{32,}$")
_B64_KEY = re.compile(r"^[A-Za-z0-9+/_-]{32,}={0,2}$")

_AWS_PREFIX = r"(?:AKIA|ASIA|ABIA|ACCA|AGPA|AIDA|AIPA|ANPA|ANVA|APKA|AROA)"

# Cuando la cadena ENTERA es el secreto se sustituye entera. El orden es el
# contrato: ``Bearer eyJ...`` es un bearer, no un JWT suelto.
_WHOLE_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("bearer", re.compile(r"bearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE)),
    ("private_key", re.compile(r"-----BEGIN[ A-Z0-9]*PRIVATE KEY-----[\s\S]*")),
    ("aws_key", re.compile(_AWS_PREFIX + r"[0-9A-Z]{12,}")),
    ("slack_token", re.compile(r"xox[abprs]-\S{6,}", re.IGNORECASE)),
    ("gitlab_token", re.compile(r"glpat-\S{6,}")),
    ("github_token", re.compile(r"gh[pousr]_\S{6,}")),
    (
        "jwt",
        re.compile(r"eyJ[A-Za-z0-9_=-]{4,}\.[A-Za-z0-9_=-]{4,}(?:\.[A-Za-z0-9_=-]*)?"),
    ),
    ("api_key", re.compile(r"(?:sk|pk|rk)[-_]\S{6,}", re.IGNORECASE)),
)


# Material criptografico real es variado: 32 digitos hexadecimales aleatorios
# traen unos 14 simbolos distintos, y caer por debajo de 8 es astronomicamente
# improbable. Una tirada del mismo caracter ("EEEE...", que ademas es hex
# valido) no es una clave, y marcarla rompe texto corriente de la bitacora.
_MIN_DISTINCT = 8


def _looks_like_key(text: str) -> bool:
    """Cadena base64/hex larga y sin espacios que huele a material criptografico."""
    candidate = text.strip()
    if len(candidate) < 32 or any(ch.isspace() for ch in candidate):
        return False
    if len(set(candidate)) < _MIN_DISTINCT:
        return False
    if _HEX_KEY.match(candidate):
        return True
    if not _B64_KEY.match(candidate):
        return False
    if "/" in candidate and not candidate.endswith("="):
        # Una ruta larga entra en el alfabeto base64. Entre romper datos
        # normales y dejar pasar una clave con barras sin relleno, se elige no
        # romper la ruta: las claves con nombre ya las coge ``_key_label``.
        return False
    return any(ch.isdigit() for ch in candidate) and any(ch.isalpha() for ch in candidate)


def _whole_value_label(text: str) -> str | None:
    stripped = text.strip()
    if not stripped:
        return None
    for label, pattern in _WHOLE_RULES:
        if pattern.fullmatch(stripped):
            return label
    if _looks_like_key(stripped):
        return "clave_probable"
    return None


# Patrones que pueden aparecer EMBEBIDOS en una linea mas larga (un comando de
# shell, un log, un extracto de resultado de herramienta). Se sustituye solo el
# tramo del secreto: el resto de la linea es contexto util y no es el secreto.


def _plain(label: str):
    return lambda _match, _label=label: mark(_label)


def _keep_group(label: str, group: int):
    """Conserva un tramo que NO es el secreto (el nombre de la clave, el esquema)."""
    return lambda match, _label=label, _group=group: match.group(_group) + mark(_label)


_EMBEDDED_RULES: tuple[tuple[re.Pattern[str], object], ...] = (
    (
        re.compile(
            r"-----BEGIN[ A-Z0-9]*PRIVATE KEY-----[\s\S]*?"
            r"(?:-----END[ A-Z0-9]*PRIVATE KEY-----|\Z)"
        ),
        _plain("private_key"),
    ),
    (
        # https://usuario:clave@host -> se conserva esquema y host.
        re.compile(r"([A-Za-z][A-Za-z0-9+.\-]*://)[^\s:/@]{1,256}:[^\s/@]{1,256}@"),
        lambda m: m.group(1) + mark("url_credentials") + "@",
    ),
    (re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE), _plain("bearer")),
    (re.compile(r"\b" + _AWS_PREFIX + r"[0-9A-Z]{12,}\b"), _plain("aws_key")),
    (
        re.compile(r"\beyJ[A-Za-z0-9_=-]{4,}\.[A-Za-z0-9_=-]{4,}(?:\.[A-Za-z0-9_=-]*)?"),
        _plain("jwt"),
    ),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE), _plain("slack_token")),
    (re.compile(r"\bglpat-[A-Za-z0-9._~+/=-]{12,}"), _plain("gitlab_token")),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}"), _plain("github_token")),
    (re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{16,}"), _plain("api_key")),
    (
        # `api_key=VALOR` con la clave y el valor en la MISMA cadena: se
        # conserva el nombre de la clave y se borra solo el valor.
        re.compile(
            r"(?i)\b((?:api[_-]?key|apikey|secret|token|password|passwd|credential"
            r"|auth[_-]?token)[\"']?\s*[:=]\s*[\"']?)([A-Za-z0-9._\-/+]{8,})"
        ),
        _keep_group("api_key", 1),
    ),
)


def redact_text(text: str) -> str:
    """Devuelve ``text`` con todo lo que parezca un secreto sustituido.

    Si la cadena ENTERA es un secreto, sale entera como marca. Si lleva uno
    dentro, sale solo el tramo sustituido y el resto intacto.
    """
    label = _whole_value_label(text)
    if label:
        return mark(label)
    redacted = text
    for pattern, repl in _EMBEDDED_RULES:
        redacted = pattern.sub(repl, redacted)
    return redacted


# ── Recorrido en profundidad ────────────────────────────────────────

def _redact(value, key: str | None):
    if isinstance(value, Mapping):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)) or (
        isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
    ):
        return [_redact(item, key) for item in value]

    label = _key_label(key)
    if label is not None:
        # El nombre de la clave ya declara que esto es un secreto, sea del tipo
        # que sea. ``None`` se deja pasar: no hay valor, no hay filtracion, y
        # marcarlo inventaria un secreto que no existe.
        return None if value is None else mark(label)

    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, (bytes, bytearray)):
        return redact_text(value.decode("utf-8", errors="replace"))
    return value


def redact(value: object) -> object:
    """Devuelve ``value`` con todo lo que parezca un secreto sustituido.

    Recorre diccionarios, listas y cadenas. Sustituye por ``"<secreto: tipo>"``
    conservando la clave o la ruta donde aparecio. Redacta por NOMBRE de clave
    (``api_key``, ``token``, ``password``, ``authorization``, ``secret``,
    ``credential``...) y por FORMA del valor (ver ``LABELS``).
    Los datos normales (numeros, booleanos, ``None``, rutas, frases) salen
    intactos.
    """
    return _redact(value, None)
