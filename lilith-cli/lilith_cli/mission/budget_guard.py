from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from lilith_tools.orchestration_state import OrchestrationStateStore

from ..config import CampaignBudgetConfig


@dataclass(frozen=True)
class BudgetUsage:
    calls: int = 0
    tokens: int = 0
    cost_usd: float = 0.0

    def as_dict(self) -> dict[str, int | float]:
        return asdict(self)

    def plus(self, other: BudgetUsage) -> BudgetUsage:
        return BudgetUsage(
            calls=self.calls + other.calls,
            tokens=self.tokens + other.tokens,
            cost_usd=round(self.cost_usd + other.cost_usd, 8),
        )


@dataclass(frozen=True)
class BudgetDecision:
    allowed: bool
    reason: str
    daily: BudgetUsage
    campaign: BudgetUsage
    provider_daily: BudgetUsage

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "daily": self.daily.as_dict(),
            "campaign": self.campaign.as_dict(),
            "provider_daily": self.provider_daily.as_dict(),
        }


def _usage_from_payload(payload: dict[str, Any]) -> BudgetUsage:
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    prompt = int(usage.get("prompt_tokens", 0) or 0)
    completion = int(usage.get("completion_tokens", 0) or 0)
    total = int(usage.get("total_tokens", prompt + completion) or 0)
    cost = float(usage.get("cost_usd", usage.get("cost", 0.0)) or 0.0)
    return BudgetUsage(calls=1, tokens=max(0, total), cost_usd=max(0.0, cost))


class AggregateBudgetGuard:
    """Enforce aggregate Campaign Runtime budgets from durable cost events."""

    def __init__(
        self,
        *,
        policy: CampaignBudgetConfig,
        state_path: str | None = None,
    ) -> None:
        self.policy = policy
        self.store = OrchestrationStateStore(state_path)

    def _today_events(self) -> list[dict[str, Any]]:
        day = datetime.now(UTC).date()
        rows = self.store.events(limit=50_000)
        result: list[dict[str, Any]] = []
        for row in rows:
            if row.get("event_type") != "cost.recorded":
                continue
            try:
                created = datetime.fromisoformat(str(row.get("created_at") or ""))
            except ValueError:
                continue
            if created.astimezone(UTC).date() == day:
                result.append(row)
        return result

    @staticmethod
    def _sum(rows: list[dict[str, Any]]) -> BudgetUsage:
        total = BudgetUsage()
        for row in rows:
            payload = row.get("payload")
            if isinstance(payload, dict):
                total = total.plus(_usage_from_payload(payload))
        return total

    def snapshot(self, *, campaign_id: str = "", provider: str = "") -> dict[str, Any]:
        rows = self._today_events()
        daily = self._sum(rows)
        campaign_rows = [
            row for row in rows
            if str((row.get("payload") or {}).get("session_id") or "") == campaign_id
        ] if campaign_id else []
        provider_rows = [
            row for row in rows
            if str((row.get("payload") or {}).get("provider") or "") == provider
        ] if provider else []
        return {
            "day": datetime.now(UTC).date().isoformat(),
            "daily": daily.as_dict(),
            "campaign": self._sum(campaign_rows).as_dict(),
            "provider_daily": self._sum(provider_rows).as_dict(),
            "campaign_id": campaign_id or None,
            "provider": provider or None,
        }

    @staticmethod
    def _reached(value: int, limit: int | None) -> bool:
        return limit is not None and value >= limit

    @staticmethod
    def _spent(value: float, limit: float | None) -> bool:
        if limit is None:
            return False
        return value > limit if limit == 0 else value >= limit

    def check(
        self,
        *,
        campaign_id: str = "",
        provider: str = "",
        prospective_cost_class: str = "",
    ) -> BudgetDecision:
        if not self.policy.enabled:
            zero = BudgetUsage()
            return BudgetDecision(True, "aggregate budgets disabled", zero, zero, zero)
        cost_class = prospective_cost_class.strip().lower()
        if cost_class == "metered" and self.policy.daily_max_usd == 0:
            zero = BudgetUsage()
            return BudgetDecision(False, "metered compute denied by zero daily USD budget", zero, zero, zero)
        if (
            cost_class == "metered"
            and provider
            and self.policy.provider_daily_max_usd.get(provider) == 0
        ):
            zero = BudgetUsage()
            return BudgetDecision(False, f"{provider} metered compute denied by zero USD budget", zero, zero, zero)
        snap = self.snapshot(campaign_id=campaign_id, provider=provider)
        daily = BudgetUsage(**snap["daily"])
        campaign = BudgetUsage(**snap["campaign"])
        provider_daily = BudgetUsage(**snap["provider_daily"])
        checks = [
            (self._reached(daily.calls, self.policy.daily_max_calls), "daily call budget exhausted"),
            (self._reached(daily.tokens, self.policy.daily_max_tokens), "daily token budget exhausted"),
            (self._spent(daily.cost_usd, self.policy.daily_max_usd), "daily USD budget exhausted"),
            (self._reached(campaign.calls, self.policy.campaign_max_calls), "campaign call budget exhausted"),
            (self._reached(campaign.tokens, self.policy.campaign_max_tokens), "campaign token budget exhausted"),
            (self._spent(campaign.cost_usd, self.policy.campaign_max_usd), "campaign USD budget exhausted"),
        ]
        if provider:
            checks.extend([
                (self._reached(provider_daily.calls, self.policy.provider_daily_max_calls.get(provider)), f"{provider} daily call budget exhausted"),
                (self._reached(provider_daily.tokens, self.policy.provider_daily_max_tokens.get(provider)), f"{provider} daily token budget exhausted"),
                (self._spent(provider_daily.cost_usd, self.policy.provider_daily_max_usd.get(provider)), f"{provider} daily USD budget exhausted"),
            ])
        reason = next((message for hit, message in checks if hit), "within aggregate budget")
        return BudgetDecision(reason == "within aggregate budget", reason, daily, campaign, provider_daily)


def campaign_id_for_task(
    store: OrchestrationStateStore,
    task: dict[str, Any],
) -> str:
    """Return the root mission correlation id for a campaign descendant."""
    tasks = {str(row.get("id")): row for row in store.get().get("tasks", [])}
    current = task
    visited: set[str] = set()
    while True:
        task_id = str(current.get("id") or "")
        if not task_id or task_id in visited:
            break
        visited.add(task_id)
        spec = ((current.get("routing") or {}).get("mission_spec") or {})
        metadata = spec.get("metadata") if isinstance(spec.get("metadata"), dict) else {}
        parent_id = str(metadata.get("parent_task_id") or "")
        if not parent_id or parent_id not in tasks:
            break
        current = tasks[parent_id]
    return str(current.get("correlation_id") or current.get("id") or "default")


__all__ = [
    "AggregateBudgetGuard",
    "BudgetDecision",
    "BudgetUsage",
    "campaign_id_for_task",
]
