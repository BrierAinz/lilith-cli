from __future__ import annotations

import os
import shutil
from dataclasses import asdict, dataclass
from typing import Any, ClassVar

from ..provider_health import ProviderHealthRegistry
from .compute import ComputeBroker, ComputeResource
from .learning import LearningEngine


@dataclass(frozen=True)
class ResourceHealth:
    resource_id: str
    available: bool
    state: str
    source: str
    reason: str
    last_error: str | None = None
    latency_ms: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ComputeHealthResolver:
    """Resolve no-quota local health for interchangeable compute resources."""

    CLI_BINARIES: ClassVar[dict[str, str]] = {
        "claude_code": "claude",
        "codex_cli": "codex",
    }

    def __init__(
        self,
        *,
        broker: ComputeBroker | None = None,
        provider_health: ProviderHealthRegistry | None = None,
        state_path=None,
    ) -> None:
        self.broker = broker or ComputeBroker()
        self.provider_health = provider_health or ProviderHealthRegistry()
        self.learning = LearningEngine(state_path=state_path)

    @staticmethod
    def _wrapper_available(adapter: str) -> tuple[bool, str]:
        try:
            from lilith_tools.cli_delegate import MUNINN_WRAPPER, VOR_WRAPPER
        except ImportError:
            return False, "lilith_tools cli adapters unavailable"
        wrappers = {
            "claude_code": MUNINN_WRAPPER,
            "codex_cli": VOR_WRAPPER,
        }
        wrapper = wrappers.get(adapter)
        if wrapper is None:
            binary = ComputeHealthResolver.CLI_BINARIES.get(adapter)
            if not binary:
                return False, f"unknown CLI adapter: {adapter}"
            resolved = shutil.which(binary)
            return bool(resolved), resolved or f"{binary} not found"
        exists = wrapper.is_file()
        return exists, str(wrapper) if exists else f"wrapper missing: {wrapper}"

    @staticmethod
    def _fabric_token_present() -> bool:
        try:
            from ..config import load_config

            env_name = load_config().fabric_token_env
        except (OSError, ValueError, AttributeError):
            env_name = "YGGDRASIL_FABRIC_TOKEN"
        return bool(os.environ.get(env_name))

    @staticmethod
    def _probe_fabric(timeout: float = 1.5) -> tuple[bool, str]:
        try:
            import httpx

            from ..config import load_config

            url = load_config().fabric_url.rstrip("/") + "/healthz"
            response = httpx.get(url, timeout=max(0.2, float(timeout)))
            if response.status_code == 200:
                return True, f"{url} -> 200"
            return False, f"{url} -> HTTP {response.status_code}"
        except Exception as exc:  # noqa: BLE001 - observational health probe boundary
            return False, f"{type(exc).__name__}: {exc}"

    def _health_for(
        self,
        resource: ComputeResource,
        *,
        learned_avoid: set[str],
        probe_fabric: bool,
    ) -> ResourceHealth:
        if resource.resource_id in learned_avoid:
            return ResourceHealth(
                resource.resource_id,
                False,
                "learned_open",
                "post_mortem",
                "recent repeated failures for this court role",
            )
        if resource.adapter == "fabric":
            circuit = self.provider_health.get("fabric")
            state = str(circuit.get("state") or "closed")
            if state == "open":
                return ResourceHealth(
                    resource.resource_id,
                    False,
                    state,
                    "provider_circuit",
                    "shared Fabric provider circuit is open",
                    str(circuit.get("last_error") or "") or None,
                    circuit.get("last_latency_ms"),
                )
            if not self._fabric_token_present():
                return ResourceHealth(
                    resource.resource_id,
                    False,
                    "unavailable",
                    "environment",
                    "Fabric token is not present in this process environment",
                )
            if probe_fabric:
                ok, detail = self._probe_fabric()
                if not ok:
                    return ResourceHealth(
                        resource.resource_id,
                        False,
                        "unavailable",
                        "transport_probe",
                        detail,
                    )
                reason = detail
            else:
                reason = f"Fabric circuit={state}; token present"
            return ResourceHealth(
                resource.resource_id,
                True,
                state,
                "provider_circuit",
                reason,
                str(circuit.get("last_error") or "") or None,
                circuit.get("last_latency_ms"),
            )
        if resource.transport == "cli":
            ok, detail = self._wrapper_available(resource.adapter)
            return ResourceHealth(
                resource.resource_id,
                ok,
                "ready" if ok else "unavailable",
                "local_adapter",
                detail,
            )
        return ResourceHealth(
            resource.resource_id,
            False,
            "unavailable",
            "configuration",
            f"unsupported transport: {resource.transport}",
        )

    def snapshot(
        self,
        *,
        court_agent: str | None = None,
        probe_fabric: bool = False,
    ) -> list[ResourceHealth]:
        learned_avoid = (
            self.learning.avoided_resources(court_agent)
            if court_agent
            else set()
        )
        return [
            self._health_for(
                resource,
                learned_avoid=learned_avoid,
                probe_fabric=probe_fabric,
            )
            for resource in self.broker.enabled()
        ]

    def healthy_ids(
        self,
        *,
        court_agent: str | None = None,
        probe_fabric: bool = False,
    ) -> set[str]:
        return {
            row.resource_id
            for row in self.snapshot(
                court_agent=court_agent,
                probe_fabric=probe_fabric,
            )
            if row.available
        }

    def observed_fabric_accounts(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for health in self.provider_health.snapshot():
            key = str(health.get("provider") or "")
            if not key.startswith("fabric-account:"):
                continue
            parts = key.split(":", 2)
            if len(parts) != 3:
                continue
            rows.append({
                "provider": parts[1],
                "account_id": parts[2],
                "state": health.get("state"),
                "successes": int(health.get("successes", 0) or 0),
                "failures": int(health.get("failures", 0) or 0),
                "consecutive_failures": int(health.get("consecutive_failures", 0) or 0),
                "last_latency_ms": health.get("last_latency_ms"),
                "last_error": health.get("last_error"),
                "updated_at": health.get("updated_at"),
            })
        rows.sort(key=lambda row: (str(row["provider"]), str(row["account_id"])))
        return rows

    def public_snapshot(
        self,
        *,
        court_agent: str | None = None,
        probe_fabric: bool = False,
    ) -> list[dict[str, Any]]:
        return [
            row.as_dict()
            for row in self.snapshot(
                court_agent=court_agent,
                probe_fabric=probe_fabric,
            )
        ]
