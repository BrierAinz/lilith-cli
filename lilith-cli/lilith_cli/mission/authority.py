from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path, PureWindowsPath
from typing import ClassVar


@dataclass(frozen=True)
class AuthorityDecision:
    allowed: bool
    requires_operator: bool
    requires_elevation: bool
    domain: str
    reason: str
    preferred_recovery: str = "none"

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class AuthorityPolicy:
    """Authority profile selected by Ainz: sovereign-local / 3B."""

    _EXTERNAL: ClassVar[set[str]] = {
        "publish", "send_message", "accept_contract", "purchase", "payment",
        "credential_change", "account_change",
    }
    _ADMIN: ClassVar[set[str]] = {
        "service_change", "driver_change", "firewall_change", "registry_write",
        "system_install", "security_change", "boot_change", "reboot",
    }
    _REVERSIBLE_DESTRUCTIVE: ClassVar[set[str]] = {
        "delete", "replace_tree", "registry_delete", "service_delete",
    }
    _IRREVERSIBLE: ClassVar[set[str]] = {
        "format", "wipe_disk", "repartition", "delete_volume", "firmware_flash",
    }

    @staticmethod
    def _domain(path: str | None) -> str:
        if not path:
            return "logical"
        raw = str(path).strip()
        windows_drive = PureWindowsPath(raw).drive.upper()
        if windows_drive:
            if windows_drive == "D:":
                return "crown"
            if windows_drive == "C:":
                return "system"
            return "outside"
        native = Path(raw).expanduser()
        if native.is_absolute():
            # POSIX installations have no C:/D: distinction. Their absolute
            # workspace paths are the portable equivalent of crown scope.
            return "crown"
        return "outside"

    @staticmethod
    def _recovery(domain: str) -> str:
        if domain == "crown":
            return "trash_or_git_checkpoint"
        if domain == "system":
            return "system_checkpoint"
        return "operator_defined"

    def evaluate(
        self,
        action: str,
        *,
        path: str | None = None,
        recovery_available: bool = False,
    ) -> AuthorityDecision:
        action = str(action).strip().lower()
        domain = self._domain(path)
        needs_admin = domain == "system" or action in self._ADMIN
        if action in self._EXTERNAL:
            return AuthorityDecision(
                False, True, needs_admin, domain,
                "External consequence requires Overlord authority.",
            )
        if action in self._IRREVERSIBLE:
            return AuthorityDecision(
                False, True, True, domain,
                "Operation is not reliably reversible and must be escalated.",
                self._recovery(domain),
            )
        if domain == "outside":
            return AuthorityDecision(
                False, True, False, domain,
                "The sovereign-local profile covers C: and D: only.",
                self._recovery(domain),
            )
        if action in self._REVERSIBLE_DESTRUCTIVE and not recovery_available:
            return AuthorityDecision(
                False, False, needs_admin, domain,
                "Create a recovery point automatically, then retry the action.",
                self._recovery(domain),
            )
        return AuthorityDecision(
            True, False, needs_admin, domain,
            "Authorized by sovereign-local profile.",
            self._recovery(domain) if action in self._REVERSIBLE_DESTRUCTIVE else "none",
        )
