"""Optional bridge from Lilith to the Yggdrasil provider/account router.

The Yggdrasil Router owns provider/account/model selection.  Lilith owns task
intent and execution policy.  This bridge deliberately keeps those concerns
separate and never serializes credential values.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields as dataclass_fields, is_dataclass
from datetime import date, datetime, timezone
import importlib
import importlib.util
import json
import logging
import os
from pathlib import Path
import re
import sys
from types import ModuleType
from typing import Any, Iterable

ROUTER_CONFIG_ENV = "YGGDRASIL_ROUTER_CONFIG"
ROUTER_HOME_ENV = "YGGDRASIL_ROUTER_HOME"
ROUTER_MODE_ENV = "YGGDRASIL_ROUTER_MODE"
ROUTER_STATE_ENV = "YGGDRASIL_ROUTER_STATE"
_VALID_MODES = {"shadow", "enforce"}
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ENV_GATEWAY_CACHE: tuple[tuple[str, int, str], Any] | None = None

_ACCOUNT_FIELDS = {
    "account_id",
    "provider",
    "secret_ref",
    "capabilities",
    "models",
    "status",
    "priority",
    "weight",
    "quota_remaining",
    "token_quota_remaining",
    "spent_today_usd",
    "daily_budget_usd",
    "cost_per_1k_tokens_usd",
    "consecutive_failures",
    "circuit_open",
    "rate_limited_until",
    "budget_day",
    "daily_quota",
    "daily_token_quota",
}

# _ACCOUNT_FIELDS es el RESPALDO, no la verdad.  La verdad es el contrato del
# router, y copiarlo a mano aqui fue el fallo original: el adaptador se quedo
# atras y rechazaba `runtime`, que Account SI acepta.  Derivandolo del propio
# dataclass, un campo nuevo del router entra solo y el desfase no se repite.
_ACCOUNT_FIELDS_CACHE: dict[int, frozenset[str]] = {}

# Version de esquema que este adaptador entiende.  Solo se compara el mayor.
_SUPPORTED_SCHEMA_MAJOR = 1

_TOP_LEVEL_FIELDS = {"failure_threshold", "accounts", "schema_version"}
LOGGER = logging.getLogger(__name__)


def _account_fields(module: ModuleType) -> frozenset[str]:
    """Campos admitidos en una cuenta, segun el contrato real del router."""

    account_cls = getattr(module, "Account", None)
    cache_key = id(account_cls)
    cached = _ACCOUNT_FIELDS_CACHE.get(cache_key)
    if cached is not None:
        return cached

    derived: frozenset[str]
    try:
        if account_cls is not None and is_dataclass(account_cls):
            derived = frozenset(f.name for f in dataclass_fields(account_cls))
        else:
            derived = frozenset(_ACCOUNT_FIELDS)
    except Exception:
        # Si la introspeccion falla por lo que sea, se sigue validando con la
        # lista de respaldo.  Lo que no se hace nunca es dejar de validar.
        derived = frozenset(_ACCOUNT_FIELDS)

    if not derived:
        derived = frozenset(_ACCOUNT_FIELDS)

    _ACCOUNT_FIELDS_CACHE[cache_key] = derived
    return derived


def _check_schema_version(raw: Any) -> None:
    """Falla diciendo la verdad cuando el fichero es de un esquema mas nuevo.

    Rechazar `schema_version` como 'campo no soportado' era el peor sintoma
    posible: convertia 'tu adaptador es viejo' en 'campo desconocido: runtime',
    que manda a depurar al sitio equivocado.
    """

    if raw is None:
        return

    texto = str(raw).strip()
    if not texto:
        return

    # El Fabric escribe un identificador con espacio de nombres:
    # "ygg.router.accounts/1.0".  Tambien se admite "1.2", "v2.0" o "2".
    ultimo = texto.rsplit("/", 1)[-1].lstrip("vV")
    mayor_txt = ultimo.split(".", 1)[0]
    if not mayor_txt.isdigit():
        # Sin version legible no se puede comparar nada.  Tumbar la carga aqui
        # seria repetir el defecto que este modulo acaba de dejar atras:
        # rechazar metadatos que no afectan al comportamiento.  La validacion
        # de campos de mas abajo sigue siendo la guardia de verdad.
        return

    mayor = int(mayor_txt)

    if mayor > _SUPPORTED_SCHEMA_MAJOR:
        raise GatewayConfigError(
            f"router config schema {texto} is newer than supported "
            f"({_SUPPORTED_SCHEMA_MAJOR}.x); update lilith-tools"
        )


class GatewayError(RuntimeError):
    """Base error for Yggdrasil Router integration."""


class GatewayUnavailable(GatewayError):
    """The router package/configuration is not available."""


class GatewayConfigError(GatewayError):
    """Router configuration is invalid or incompatible."""


class GatewayNoRoute(GatewayError):
    """No eligible Yggdrasil account can satisfy the request."""


@dataclass(frozen=True)
class GatewayDecision:
    """Sanitized routing result; ``secret_ref`` stays internal by default."""

    account_id: str
    provider: str
    model: str | None
    score: float
    estimated_cost_usd: float
    reason: str
    secret_ref: str = field(repr=False)

    def public_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "provider": self.provider,
            "model": self.model,
            "score": self.score,
            "estimated_cost_usd": self.estimated_cost_usd,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class GatewayProbe:
    configured: bool
    available: bool
    mode: str
    account_count: int = 0
    config_path: str | None = None
    source: str | None = None
    error: str = ""

    def public_dict(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "available": self.available,
            "mode": self.mode,
            "account_count": self.account_count,
            "config_path": self.config_path,
            "source": self.source,
            "error": self.error,
        }


def router_mode() -> str:
    """Return the configured bridge mode.

    ``shadow`` is intentionally the default: explicit router configuration can
    be validated without silently replacing the credential used by Lilith.
    ``enforce`` must be opted into separately.
    """

    mode = os.environ.get(ROUTER_MODE_ENV, "shadow").strip().lower() or "shadow"
    if mode not in _VALID_MODES:
        allowed = ", ".join(sorted(_VALID_MODES))
        raise GatewayConfigError(f"invalid {ROUTER_MODE_ENV}: {mode!r}; expected {allowed}")
    return mode


def _parse_datetime(value: Any, *, field_name: str) -> datetime | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise GatewayConfigError(f"{field_name} must be ISO-8601 text or null")
    raw = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise GatewayConfigError(f"invalid {field_name}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_date(value: Any, *, field_name: str) -> date | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise GatewayConfigError(f"{field_name} must be ISO date text or null")
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise GatewayConfigError(f"invalid {field_name}") from exc


def _candidate_router_package(explicit_home: Path | str | None = None) -> Path | None:
    candidates: list[Path] = []
    raw_home = explicit_home or os.environ.get(ROUTER_HOME_ENV)
    if raw_home:
        home = Path(raw_home).expanduser()
        candidates.extend([home, home / "yggdrasil_router"])

    for parent in Path(__file__).resolve().parents:
        candidates.append(parent / "Vanaheim" / "Core" / "yggdrasil_router")

    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if (resolved / "__init__.py").is_file():
            return resolved
    return None


def _load_router_module(explicit_home: Path | str | None = None) -> tuple[ModuleType, str]:
    try:
        module = importlib.import_module("yggdrasil_router")
        return module, "installed"
    except ModuleNotFoundError as exc:
        if exc.name != "yggdrasil_router":
            raise GatewayUnavailable(f"router import failed: {type(exc).__name__}") from exc

    package_dir = _candidate_router_package(explicit_home)
    if package_dir is None:
        raise GatewayUnavailable("yggdrasil_router package not installed and checkout not found")

    parent = str(package_dir.parent)
    inserted = parent not in sys.path
    if inserted:
        sys.path.insert(0, parent)
    try:
        module = importlib.import_module("yggdrasil_router")
    except Exception as exc:
        raise GatewayUnavailable(f"router checkout import failed: {type(exc).__name__}") from exc
    finally:
        if inserted:
            try:
                sys.path.remove(parent)
            except ValueError:
                pass
    return module, str(package_dir)


def _load_router_state_module(router_module: ModuleType) -> ModuleType | None:
    """Load ``state.py`` beside the exact router package used by the gateway."""

    package_file = getattr(router_module, "__file__", None)
    if not package_file:
        return None
    state_path = Path(package_file).resolve().parent / "state.py"
    if not state_path.is_file():
        return None

    module_name = f"{router_module.__name__}.state"
    existing = sys.modules.get(module_name)
    if existing is not None:
        existing_file = getattr(existing, "__file__", None)
        if existing_file and Path(existing_file).resolve() == state_path:
            return existing

    spec = importlib.util.spec_from_file_location(module_name, state_path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(module_name)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        if previous is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous
        raise
    return module


def _account_from_dict(module: ModuleType, raw: Any, *, index: int) -> Any:
    if not isinstance(raw, dict):
        raise GatewayConfigError(f"accounts[{index}] must be an object")
    unknown = set(raw) - _account_fields(module)
    if unknown:
        raise GatewayConfigError(
            f"accounts[{index}] has unsupported fields: {', '.join(sorted(unknown))}"
        )
    data = dict(raw)
    for required in ("account_id", "provider", "secret_ref", "capabilities"):
        if required not in data:
            raise GatewayConfigError(f"accounts[{index}] missing {required}")
    data["capabilities"] = set(data.get("capabilities") or [])
    data["models"] = set(data.get("models") or [])
    if "status" in data:
        try:
            data["status"] = module.AccountStatus(str(data["status"]))
        except Exception as exc:
            raise GatewayConfigError(f"accounts[{index}] has invalid status") from exc
    if "rate_limited_until" in data:
        data["rate_limited_until"] = _parse_datetime(
            data["rate_limited_until"], field_name=f"accounts[{index}].rate_limited_until"
        )
    if "budget_day" in data:
        data["budget_day"] = _parse_date(
            data["budget_day"], field_name=f"accounts[{index}].budget_day"
        )
    try:
        return module.Account(**data)
    except Exception as exc:
        raise GatewayConfigError(f"accounts[{index}] incompatible with router contract") from exc


class YggdrasilGateway:
    """Load a Yggdrasil Router snapshot and expose a Lilith-safe API."""

    def __init__(
        self,
        config_path: Path | str,
        *,
        router_home: Path | str | None = None,
        router_module: ModuleType | None = None,
    ) -> None:
        self.config_path = Path(config_path).expanduser().resolve()
        if not self.config_path.is_file():
            raise GatewayConfigError(f"router config not found: {self.config_path}")

        if router_module is None:
            self._module, self.source = _load_router_module(router_home)
        else:
            self._module = router_module
            self.source = "injected"

        required = ("Account", "AccountStatus", "RouteRequest", "YggdrasilRouter")
        missing = [name for name in required if not hasattr(self._module, name)]
        if missing:
            raise GatewayUnavailable(f"router module missing public symbols: {', '.join(missing)}")

        try:
            raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise GatewayConfigError("router config is not valid JSON") from exc
        if not isinstance(raw, dict):
            raise GatewayConfigError("router config root must be an object")
        _check_schema_version(raw.get("schema_version"))
        # Las claves `nota*` son comentarios del fichero de configuracion: no
        # afectan al comportamiento y no deben tumbar la carga.  Cualquier otra
        # clave desconocida sigue siendo un error (una errata debe doler).
        unknown_top = {
            clave
            for clave in set(raw) - _TOP_LEVEL_FIELDS
            if not str(clave).startswith("nota")
        }
        if unknown_top:
            raise GatewayConfigError(
                f"router config has unsupported fields: {', '.join(sorted(unknown_top))}"
            )
        accounts_raw = raw.get("accounts")
        if not isinstance(accounts_raw, list):
            raise GatewayConfigError("router config accounts must be a list")
        accounts = [
            _account_from_dict(self._module, item, index=index)
            for index, item in enumerate(accounts_raw)
        ]
        failure_threshold = int(raw.get("failure_threshold", 3) or 3)
        self.router = self._module.YggdrasilRouter(
            accounts,
            failure_threshold=failure_threshold,
        )
        self.account_count = len(accounts)
        self._state_module: ModuleType | None = None
        self._state: Any = None
        if os.environ.get(ROUTER_STATE_ENV) != "off":
            try:
                self._state_module = _load_router_state_module(self._module)
                if self._state_module is not None:
                    self._state = self._state_module.load_state()
                    self._state.apply_to(self.router)
            except Exception:
                self._state_module = None
                self._state = None
                LOGGER.exception("Could not apply router state; routing without persistence")

    def _persist_state(self, update: Any) -> None:
        if self._state_module is None or self._state is None:
            update(self.router)
            return
        try:
            self._state = self._state_module.update_router_state(self.router, update)
        except Exception:
            LOGGER.exception("Could not persist router state; routing remains available")

    @classmethod
    def from_env(
        cls,
        *,
        router_module: ModuleType | None = None,
    ) -> "YggdrasilGateway | None":
        value = os.environ.get(ROUTER_CONFIG_ENV, "").strip()
        if not value:
            return None
        # Injected modules are test/specialized callers and deliberately bypass
        # the shared process cache.
        if router_module is not None:
            return cls(value, router_module=router_module)

        global _ENV_GATEWAY_CACHE
        path = Path(value).expanduser().resolve()
        try:
            mtime_ns = path.stat().st_mtime_ns
        except OSError:
            mtime_ns = -1
        key = (str(path), mtime_ns, os.environ.get(ROUTER_HOME_ENV, "").strip())
        if _ENV_GATEWAY_CACHE is not None and _ENV_GATEWAY_CACHE[0] == key:
            return _ENV_GATEWAY_CACHE[1]
        gateway = cls(path)
        _ENV_GATEWAY_CACHE = (key, gateway)
        return gateway

    def route(
        self,
        *,
        capability: str,
        estimated_tokens: int = 0,
        preferred_provider: str | None = None,
        preferred_model: str | None = None,
        max_cost_usd: float | None = None,
        tags: Iterable[str] = (),
        now: datetime | None = None,
    ) -> GatewayDecision:
        instant = now or datetime.now(timezone.utc)
        self.router.tick(instant)
        request = self._module.RouteRequest(
            capability=str(capability),
            estimated_tokens=max(0, int(estimated_tokens)),
            preferred_provider=preferred_provider,
            preferred_model=preferred_model,
            max_cost_usd=max_cost_usd,
            tags=frozenset(str(tag) for tag in tags),
        )
        try:
            raw = self.router.route(request)
        except Exception as exc:
            if type(exc).__name__ == "NoRouteAvailable":
                raise GatewayNoRoute(str(exc)) from exc
            raise GatewayError(f"router execution failed: {type(exc).__name__}") from exc
        return GatewayDecision(
            account_id=str(raw.account_id),
            provider=str(raw.provider),
            model=str(raw.model) if raw.model is not None else None,
            score=float(raw.score),
            estimated_cost_usd=float(raw.estimated_cost_usd),
            reason=str(raw.reason),
            secret_ref=str(raw.secret_ref),
        )


    def record_outcome(
        self,
        account_id: str,
        *,
        success: bool,
        tokens_used: int = 0,
    ) -> None:
        """Update the router's in-process quota/circuit state after execution."""
        if success:
            def record_success(router: Any) -> None:
                cost_usd = 0.0
                for account in getattr(router, "accounts", ()):
                    if str(getattr(account, "account_id", "")) == account_id:
                        estimator = getattr(account, "estimated_cost", None)
                        if callable(estimator):
                            cost_usd = float(estimator(max(0, int(tokens_used))))
                        break
                else:
                    raise GatewayConfigError(
                        f"routed account no longer exists: {account_id}"
                    )
                router.record_success(
                    account_id,
                    tokens_used=max(0, int(tokens_used)),
                    cost_usd=max(0.0, cost_usd),
                )

            self._persist_state(record_success)
            return
        self._persist_state(lambda router: router.record_failure(account_id))


def clear_gateway_cache() -> None:
    """Forget the process-local router snapshot (primarily for reload/tests)."""
    global _ENV_GATEWAY_CACHE
    _ENV_GATEWAY_CACHE = None


def record_gateway_outcome(
    route: dict[str, Any] | None,
    *,
    success: bool,
    usage: dict[str, Any] | None = None,
) -> None:
    """Feed a real enforced delegation result back into the cached router.

    Shadow decisions never consume quota because the selected account was not
    actually used. Enforced outcomes update the cached router and persist quota
    and circuit state through the router's bounded cross-process transaction.
    Persistence failures are logged and never replace the provider result.
    """
    if not route or route.get("mode") != "enforce" or route.get("status") != "selected":
        return
    account_id = str(route.get("account_id") or "").strip()
    if not account_id:
        return
    data = usage if isinstance(usage, dict) else {}
    total_tokens = data.get("total_tokens")
    if not isinstance(total_tokens, (int, float)) or isinstance(total_tokens, bool):
        total_tokens = sum(
            value
            for key in ("prompt_tokens", "completion_tokens")
            for value in [data.get(key, 0)]
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        )
    try:
        gateway = YggdrasilGateway.from_env()
        if gateway is not None:
            gateway.record_outcome(
                account_id,
                success=bool(success),
                tokens_used=max(0, int(total_tokens or 0)),
            )
    except (GatewayError, KeyError, ValueError):
        # Telemetry/account bookkeeping must not replace the provider result.
        return


def resolve_env_secret(secret_ref: str) -> str:
    """Resolve an ``env://`` credential reference without logging its value."""

    if not secret_ref.startswith("env://"):
        raise GatewayUnavailable("Lilith enforce mode currently supports env:// secret refs only")
    name = secret_ref[6:].strip()
    if not _ENV_NAME_RE.fullmatch(name):
        raise GatewayConfigError("invalid env secret reference")
    value = os.environ.get(name)
    if not value:
        raise GatewayUnavailable(f"credential environment variable is not set: {name}")
    return value


def probe_gateway() -> GatewayProbe:
    """Return a sanitized health snapshot without attempting a provider call."""

    config_value = os.environ.get(ROUTER_CONFIG_ENV, "").strip()
    try:
        mode = router_mode()
    except GatewayError as exc:
        return GatewayProbe(
            configured=bool(config_value),
            available=False,
            mode="invalid",
            config_path=config_value or None,
            error=str(exc),
        )
    if not config_value:
        return GatewayProbe(configured=False, available=False, mode=mode)
    try:
        gateway = YggdrasilGateway(config_value)
    except GatewayError as exc:
        return GatewayProbe(
            configured=True,
            available=False,
            mode=mode,
            config_path=config_value,
            error=str(exc),
        )
    return GatewayProbe(
        configured=True,
        available=True,
        mode=mode,
        account_count=gateway.account_count,
        config_path=str(gateway.config_path),
        source=gateway.source,
    )
