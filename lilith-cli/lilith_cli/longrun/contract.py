"""Contrato y presupuesto de una guardia larga.

Una guardia de mas de 24 horas corre sin nadie delante y atraviesa caidas del
proceso, reinicios de la maquina y cortes del proveedor. Lo unico que impide
que eso se convierta en una fuga de tiempo y de dinero es un presupuesto que
viva en disco y no en memoria.

De ahi la regla que manda en este modulo: **el reloj se calcula siempre desde
``created_at``, nunca acumulando**. Un contador acumulado se pone a cero con el
proceso que lo llevaba; una resta contra una marca de tiempo guardada sobrevive
a que la maquina se apague entera.

Estructura en disco (``~/.yggdrasil/long-runs/<run_id>/``):

* ``contract.json`` — inmutable tras crearse.
* ``consumed.json`` — mutable, escritura atomica.

Las dos escrituras usan ``lilith_cli.task_workspace.atomic_json``: no hay otra
funcion de escritura atomica en este arbol y no se escribe una nueva.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..task_workspace import atomic_json

__all__ = [
    "Budget",
    "RunContract",
    "Consumed",
    "wall_seconds",
    "exhausted",
    "remaining",
]


CONTRACT_FILENAME = "contract.json"
CONSUMED_FILENAME = "consumed.json"

SLUG_MAX_CHARS = 24
SLUG_FALLBACK = "guardia"

_NON_SLUG = re.compile(r"[^a-z0-9]+")


# ── Tiempo ──────────────────────────────────────────────────────────
#
# Todas las marcas son ISO 8601 UTC con sufijo `Z` y sin microsegundos. Sin
# microsegundos porque el deadline se compara y se resta: media milesima
# arrastrada convierte "exactamente created_at + max_wall_seconds" en una
# igualdad que falla por 1e-6 y nadie entiende por que.


def _now() -> datetime:
    """Instante actual, con zona UTC explicita."""
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    """Normaliza un ``datetime`` a UTC; uno sin zona se asume ya en UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _moment(now: datetime | None) -> datetime:
    """Resuelve el parametro ``now`` de las funciones publicas."""
    if now is None:
        return _now()
    if not isinstance(now, datetime):
        raise ValueError(
            f"«now» debe ser un datetime o None, no {type(now).__name__}."
        )
    return _as_utc(now)


def _iso(dt: datetime) -> str:
    """ISO 8601 UTC con sufijo ``Z``, sin microsegundos."""
    moment = _as_utc(dt).replace(microsecond=0)
    return moment.isoformat().replace("+00:00", "Z")


def _parse(texto: str, *, campo: str = "marca de tiempo") -> datetime:
    """Lee una marca ISO 8601 y la devuelve en UTC. Lanza ``ValueError``."""
    if not isinstance(texto, str) or not texto.strip():
        raise ValueError(f"El contrato no declara «{campo}».")
    limpio = texto.strip()
    if limpio.endswith(("Z", "z")):
        limpio = limpio[:-1] + "+00:00"
    try:
        return _as_utc(datetime.fromisoformat(limpio))
    except ValueError as exc:
        raise ValueError(
            f"«{campo}» no es una marca ISO 8601 valida: {texto!r}."
        ) from exc


def _slug(goal: str) -> str:
    """Convierte el objetivo en un slug de ``[a-z0-9-]`` de 24 caracteres o menos."""
    bruto = _NON_SLUG.sub("-", str(goal or "").lower())
    bruto = re.sub(r"-{2,}", "-", bruto)
    recortado = bruto[:SLUG_MAX_CHARS].strip("-")
    return recortado or SLUG_FALLBACK


# ── Presupuesto ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class Budget:
    """Los topes de una guardia. Los tres primeros son obligatorios y > 0."""

    max_wall_seconds: int
    max_legs: int
    max_iterations_per_leg: int
    max_tokens: int | None = None
    max_usd: float | None = None

    def as_dict(self) -> dict:
        return {
            "max_wall_seconds": self.max_wall_seconds,
            "max_legs": self.max_legs,
            "max_iterations_per_leg": self.max_iterations_per_leg,
            "max_tokens": self.max_tokens,
            "max_usd": self.max_usd,
        }


_BUDGET_OBLIGATORIOS = (
    ("max_wall_seconds", "el tope de reloj"),
    ("max_legs", "el tope de tramos"),
    ("max_iterations_per_leg", "el tope de iteraciones por tramo"),
)

_BUDGET_OPCIONALES = (
    ("max_tokens", "el tope de tokens"),
    ("max_usd", "el tope de gasto"),
)


def _validar_budget(budget: Budget) -> None:
    if not isinstance(budget, Budget):
        raise ValueError("El contrato no lleva un «Budget».")
    for campo, descripcion in _BUDGET_OBLIGATORIOS:
        valor = getattr(budget, campo)
        if isinstance(valor, bool) or not isinstance(valor, (int, float)):
            raise ValueError(
                f"{descripcion.capitalize()} («{campo}») debe ser un numero; "
                f"llego {valor!r}."
            )
        if valor <= 0:
            raise ValueError(
                f"{descripcion.capitalize()} («{campo}») debe ser positivo; "
                f"llego {valor!r}."
            )
    for campo, descripcion in _BUDGET_OPCIONALES:
        valor = getattr(budget, campo)
        if valor is None:
            # Un tope ausente no limita; eso es deliberado y valido.
            continue
        if isinstance(valor, bool) or not isinstance(valor, (int, float)):
            raise ValueError(
                f"{descripcion.capitalize()} («{campo}») debe ser un numero o "
                f"None; llego {valor!r}."
            )
        if valor <= 0:
            raise ValueError(
                f"{descripcion.capitalize()} («{campo}») debe ser positivo si "
                f"se declara; llego {valor!r}."
            )


def _budget_desde(data: object) -> Budget:
    """Reconstruye un ``Budget`` desde el sub-diccionario ``budget``.

    Explicito a proposito: pasarle el diccionario entero a ``Budget(**data)``
    revienta en cuanto el fichero gane un campo, y ``dataclasses.asdict`` a
    ciegas sobre el contrato anida lo que no toca.
    """
    if not isinstance(data, dict):
        raise ValueError("El contrato guardado no lleva un bloque «budget».")
    faltan = [campo for campo, _ in _BUDGET_OBLIGATORIOS if campo not in data]
    if faltan:
        raise ValueError(
            "Al bloque «budget» del contrato le faltan campos: "
            + ", ".join(faltan)
            + "."
        )
    return Budget(
        max_wall_seconds=data["max_wall_seconds"],
        max_legs=data["max_legs"],
        max_iterations_per_leg=data["max_iterations_per_leg"],
        max_tokens=data.get("max_tokens"),
        max_usd=data.get("max_usd"),
    )


# ── Contrato ────────────────────────────────────────────────────────


_CONTRATO_OBLIGATORIOS = (
    "run_id",
    "goal",
    "end_criterion",
    "project_root",
    "budget",
    "created_at",
    "deadline",
)


@dataclass(frozen=True)
class RunContract:
    """Lo que la guardia se compromete a hacer y con cuanto presupuesto."""

    run_id: str
    goal: str
    end_criterion: str
    verify: str
    project_root: str
    budget: Budget
    created_at: str
    deadline: str

    # -- Validacion ---------------------------------------------------

    def validate(self) -> None:
        """Lanza ``ValueError`` (en espanol) si el contrato no se sostiene."""
        if not isinstance(self.goal, str) or not self.goal.strip():
            raise ValueError("El objetivo («goal») no puede estar vacio.")
        if not isinstance(self.end_criterion, str) or not self.end_criterion.strip():
            raise ValueError(
                "El criterio de fin («end_criterion») no puede estar vacio: sin "
                "el, nadie sabe cuando parar."
            )
        if not isinstance(self.project_root, str) or not self.project_root.strip():
            raise ValueError("La raiz del proyecto («project_root») no puede estar vacia.")
        if not Path(self.project_root).is_dir():
            raise ValueError(
                f"La raiz del proyecto no es un directorio existente: "
                f"{self.project_root!r}."
            )

        _validar_budget(self.budget)

        creado = _parse(self.created_at, campo="created_at")
        plazo = _parse(self.deadline, campo="deadline")
        if plazo <= creado:
            raise ValueError(
                f"El plazo («deadline», {self.deadline}) debe ser posterior a la "
                f"creacion («created_at», {self.created_at})."
            )

    # -- Disco --------------------------------------------------------

    def as_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "goal": self.goal,
            "end_criterion": self.end_criterion,
            "verify": self.verify,
            "project_root": self.project_root,
            "budget": self.budget.as_dict(),
            "created_at": self.created_at,
            "deadline": self.deadline,
        }

    def save(self, directory: Path) -> Path:
        """Escribe ``contract.json`` de forma atomica y devuelve su ruta."""
        path = Path(directory) / CONTRACT_FILENAME
        atomic_json(path, self.as_dict())
        return path

    @classmethod
    def load(cls, directory: Path) -> "RunContract":
        """Lee ``contract.json``. Lanza ``ValueError`` si falta o esta roto."""
        path = Path(directory) / CONTRACT_FILENAME
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise ValueError(
                f"No hay contrato que cargar en {path}: el fichero no existe."
            ) from exc
        except OSError as exc:
            raise ValueError(f"No se pudo leer el contrato en {path}: {exc}.") from exc

        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise ValueError(
                f"El contrato en {path} no es JSON valido: {exc}."
            ) from exc
        if not isinstance(data, dict):
            raise ValueError(
                f"El contrato en {path} deberia ser un objeto JSON, no "
                f"{type(data).__name__}."
            )
        faltan = [campo for campo in _CONTRATO_OBLIGATORIOS if campo not in data]
        if faltan:
            raise ValueError(
                f"Al contrato en {path} le faltan campos: " + ", ".join(faltan) + "."
            )

        return cls(
            run_id=data["run_id"],
            goal=data["goal"],
            end_criterion=data["end_criterion"],
            verify=data.get("verify", ""),
            project_root=data["project_root"],
            budget=_budget_desde(data["budget"]),
            created_at=data["created_at"],
            deadline=data["deadline"],
        )

    # -- Alta ---------------------------------------------------------

    @classmethod
    def create(
        cls,
        *,
        goal: str,
        end_criterion: str,
        verify: str,
        project_root: str,
        budget: Budget,
        now: datetime | None = None,
    ) -> "RunContract":
        """Crea un contrato nuevo: ``run_id``, ``created_at`` y ``deadline``.

        No valida: ``validate()`` es un paso aparte y explicito, para que un
        test (o el operador) pueda construir un contrato dudoso y preguntarle
        despues que tiene de malo.
        """
        creado = _moment(now)
        plazo = creado + timedelta(seconds=budget.max_wall_seconds)
        run_id = f"GU-{creado:%Y%m%d}-{creado:%H%M%S}-{_slug(goal)}"
        return cls(
            run_id=run_id,
            goal=goal,
            end_criterion=end_criterion,
            verify=verify,
            project_root=str(project_root),
            budget=budget,
            created_at=_iso(creado),
            deadline=_iso(plazo),
        )


# ── Consumo ─────────────────────────────────────────────────────────


_CONSUMED_CAMPOS = (("legs", int), ("iterations", int), ("tokens", int), ("usd", float))


@dataclass
class Consumed:
    """Lo gastado hasta ahora. Mutable: la guardia lo va sumando."""

    legs: int = 0
    iterations: int = 0
    tokens: int = 0
    usd: float = 0.0

    def as_dict(self) -> dict:
        return {
            "legs": self.legs,
            "iterations": self.iterations,
            "tokens": self.tokens,
            "usd": self.usd,
        }

    def save(self, directory: Path) -> Path:
        """Escribe ``consumed.json`` de forma atomica y devuelve su ruta."""
        path = Path(directory) / CONSUMED_FILENAME
        atomic_json(path, self.as_dict())
        return path

    @classmethod
    def load(cls, directory: Path) -> "Consumed":
        """Lee ``consumed.json``; si aun no existe, devuelve ``Consumed()``.

        Que falte es normal (la guardia todavia no ha gastado nada). Que este
        corrupto no lo es: eso se dice, no se disimula con ceros, porque un
        cero inventado reabre un presupuesto ya agotado.
        """
        path = Path(directory) / CONSUMED_FILENAME
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return cls()
        except OSError as exc:
            raise ValueError(f"No se pudo leer el consumo en {path}: {exc}.") from exc

        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise ValueError(
                f"El consumo en {path} no es JSON valido: {exc}."
            ) from exc
        if not isinstance(data, dict):
            raise ValueError(
                f"El consumo en {path} deberia ser un objeto JSON, no "
                f"{type(data).__name__}."
            )

        valores: dict = {}
        for campo, tipo in _CONSUMED_CAMPOS:
            valor = data.get(campo, 0)
            if isinstance(valor, bool) or not isinstance(valor, (int, float)):
                raise ValueError(
                    f"El campo «{campo}» del consumo en {path} deberia ser un "
                    f"numero; llego {valor!r}."
                )
            valores[campo] = tipo(valor)
        return cls(**valores)


# ── Reloj y presupuesto ─────────────────────────────────────────────


def wall_seconds(contract: RunContract, now: datetime | None = None) -> float:
    """Segundos transcurridos desde ``created_at``.

    Siempre por resta contra la marca guardada, nunca acumulando: eso es lo
    que hace que el presupuesto sobreviva a un reinicio de la maquina.
    """
    creado = _parse(contract.created_at, campo="created_at")
    return (_moment(now) - creado).total_seconds()


def exhausted(
    contract: RunContract,
    consumed: Consumed,
    now: datetime | None = None,
) -> str | None:
    """Etiqueta del primer tope agotado, o ``None`` si queda presupuesto.

    Orden fijo: ``"reloj"``, ``"tramos"``, ``"tokens"``, ``"gasto"``. El reloj
    va primero porque es el unico que sigue corriendo mientras nadie mira.
    Un tope a ``None`` no limita: ausencia de tope no es tope a cero.
    """
    budget = contract.budget

    if wall_seconds(contract, now) >= budget.max_wall_seconds:
        return "reloj"
    if consumed.legs >= budget.max_legs:
        return "tramos"
    if budget.max_tokens is not None and consumed.tokens >= budget.max_tokens:
        return "tokens"
    if budget.max_usd is not None and consumed.usd >= budget.max_usd:
        return "gasto"
    return None


def remaining(
    contract: RunContract,
    consumed: Consumed,
    now: datetime | None = None,
) -> dict:
    """Lo que queda de cada tope, saturado en cero.

    Nunca negativo: un resto negativo se lee como "hay margen" en cualquier
    aritmetica posterior, y eso es justo lo contrario de lo que significa.
    """
    budget = contract.budget
    transcurridos = wall_seconds(contract, now)

    tokens_restantes: int | None = None
    if budget.max_tokens is not None:
        tokens_restantes = max(0, int(budget.max_tokens) - int(consumed.tokens))

    usd_restantes: float | None = None
    if budget.max_usd is not None:
        usd_restantes = max(0.0, float(budget.max_usd) - float(consumed.usd))

    return {
        "segundos": max(0.0, float(budget.max_wall_seconds) - transcurridos),
        "tramos": max(0, int(budget.max_legs) - int(consumed.legs)),
        "tokens": tokens_restantes,
        "usd": usd_restantes,
    }
