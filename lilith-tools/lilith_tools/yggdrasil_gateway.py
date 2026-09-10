"""Optional bridge from Lilith to the Yggdrasil provider/account router.

The Yggdrasil Router owns provider/account/model selection.  Lilith owns task
intent and execution policy.  This bridge deliberately keeps those concerns
separate and never serializes credential values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import importlib
import json
import os
from pathlib import Path
import re
import sys
from types import ModuleType
from typing import Any, Iterable

ROUTER_CONFIG_ENV = "YGGDRASIL_ROUTER_CONFIG"
ROUTER_HOME_ENV = "YGGDRASIL_ROUTER_HOME"
ROUTER_MODE_ENV = "YGGDRASIL_ROUTER_MODE"
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


def _account_from_dict(module: ModuleType, raw: Any, *, index: int) -> Any:
    if not isinstance(raw, dict):
        raise GatewayConfigError(f"accounts[{index}] must be an object")
    unknown = set(raw) - _ACCOUNT_FIELDS
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
        unknown_top = set(raw) - {"failure_threshold", "accounts"}
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
            cost_usd = 0.0
            found = False
            for account in getattr(self.router, "accounts", ()):
                if str(getattr(account, "account_id", "")) == account_id:
                    found = True
                    estimator = getattr(account, "estimated_cost", None)
                    if callable(estimator):
                        cost_usd = float(estimator(max(0, int(tokens_used))))
                    break
            if not found:
                raise GatewayConfigError(f"routed account no longer exists: {account_id}")
            self.router.record_success(
                account_id,
                tokens_used=max(0, int(tokens_used)),
                cost_usd=max(0.0, cost_usd),
            )
            return
        self.router.record_failure(account_id)


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
    actually used. Persistence across processes remains the router/Niflheim
    layer's responsibility.
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
