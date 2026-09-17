from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import ClassVar


@dataclass(frozen=True)
class ComputeResource:
    resource_id: str
    label: str
    transport: str
    capabilities: frozenset[str]
    cost_class: str
    priority: int
    adapter: str
    enabled: bool = True
    metadata: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["capabilities"] = sorted(self.capabilities)
        return data


@dataclass(frozen=True)
class ComputeRoute:
    resource: ComputeResource
    reason: str
    fallbacks: tuple[str, ...]


class ComputeBroker:
    """Route compute independently from court identity or mission role."""

    COST_RANK: ClassVar[dict[str, int]] = {
        "free": 0,
        "subscription": 1,
        "metered": 2,
        "unknown": 3,
    }

    BASELINE: ClassVar[tuple[ComputeResource, ...]] = (
        ComputeResource(
            "experimental_labs",
            "Experimental Labs Free",
            "fabric",
            frozenset({"chat", "analysis", "research", "code_read", "reasoning"}),
            "free",
            10,
            "fabric",
            metadata={"model": "gpt-5.6-luna", "model_policy": "experiential"},
        ),
        ComputeResource(
            "opencode_go",
            "OpenCode Go",
            "fabric",
            frozenset({"chat", "analysis", "code_read", "code_write", "reasoning"}),
            "subscription",
            20,
            "fabric",
            metadata={"plan": "go", "model": "glm-5.2"},
        ),
        ComputeResource(
            "minimax_plus",
            "MiniMax Token Plan Plus",
            "fabric",
            frozenset({"chat", "analysis", "code_read", "code_write", "batch", "reasoning"}),
            "subscription",
            30,
            "fabric",
            metadata={"plan": "plus", "model": "MiniMax-M3"},
        ),
        ComputeResource(
            "claude_pro",
            "Claude Pro / Claude Code",
            "cli",
            frozenset({"chat", "analysis", "code_read", "code_write", "review", "admin_reasoning"}),
            "subscription",
            40,
            "claude_code",
            metadata={"billing_surface": "subscription_cli"},
        ),
        ComputeResource(
            "gpt_plus",
            "GPT Plus / Codex",
            "cli",
            frozenset({"chat", "analysis", "code_read", "code_write", "review", "reasoning"}),
            "subscription",
            50,
            "codex_cli",
            metadata={"billing_surface": "subscription_cli"},
        ),
    )

    def __init__(self, resources: tuple[ComputeResource, ...] | None = None) -> None:
        self.resources = resources or self.BASELINE

    def enabled(self) -> list[ComputeResource]:
        return [resource for resource in self.resources if resource.enabled]

    def route(
        self,
        required: set[str] | frozenset[str],
        *,
        healthy: set[str] | None = None,
        avoid: set[str] | None = None,
        preferred_transport: str | None = None,
    ) -> ComputeRoute:
        wanted = {str(item).strip().lower() for item in required if str(item).strip()}
        healthy_ids = set(healthy) if healthy is not None else {
            resource.resource_id for resource in self.enabled()
        }
        avoided = set(avoid or ())
        candidates: list[tuple[int, int, int, ComputeResource]] = []
        for resource in self.enabled():
            if resource.resource_id not in healthy_ids or resource.resource_id in avoided:
                continue
            overlap = len(wanted & resource.capabilities)
            if wanted and overlap != len(wanted):
                continue
            transport_penalty = int(
                preferred_transport is not None and resource.transport != preferred_transport
            )
            candidates.append((
                transport_penalty,
                self.COST_RANK.get(resource.cost_class, 99),
                resource.priority,
                resource,
            ))
        if not candidates:
            raise LookupError(f"no healthy compute route for capabilities: {sorted(wanted)}")
        candidates.sort(key=lambda item: item[:3])
        chosen = candidates[0][3]
        fallbacks = tuple(item[3].resource_id for item in candidates[1:])
        reason = (
            f"matched {sorted(wanted) or ['general']} via {chosen.transport}; "
            f"cost={chosen.cost_class}; priority={chosen.priority}"
        )
        return ComputeRoute(chosen, reason, fallbacks)

    def public_inventory(self) -> list[dict[str, object]]:
        return [resource.as_dict() for resource in self.enabled()]
