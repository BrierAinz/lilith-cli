from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from lilith_tools.base import ToolResult
from lilith_tools.cli_delegate import MuninnDelegateTool, VorDelegateTool
from lilith_tools.delegate import DelegateSubagentTool
from lilith_tools.orchestration_state import OrchestrationStateStore

from .authority import AuthorityPolicy
from .budget_guard import AggregateBudgetGuard
from .compute import ComputeBroker, ComputeRoute
from .compute_health import ComputeHealthResolver
from .court import CourtAgent, CourtRegistry
from .kernel import MissionKernel


class CourtDispatcher:
    """Dispatch a court role through an interchangeable compute adapter."""

    def __init__(
        self,
        *,
        compute: ComputeBroker | None = None,
        state_path: str | Path | None = None,
        health: ComputeHealthResolver | None = None,
    ) -> None:
        self.compute = compute or ComputeBroker()
        self.state_path = Path(state_path).expanduser() if state_path else None
        self.health = health or ComputeHealthResolver(
            broker=self.compute,
            state_path=self.state_path,
        )
        from ..config import CampaignBudgetConfig, load_config
        try:
            policy = load_config().campaign_budgets
        except (OSError, TypeError, ValueError):
            policy = CampaignBudgetConfig()
        self.budget_guard = AggregateBudgetGuard(
            policy=policy, state_path=str(self.state_path) if self.state_path else None
        )

    def _store(self) -> OrchestrationStateStore:
        return OrchestrationStateStore(self.state_path)

    @staticmethod
    def _system(agent: CourtAgent) -> str:
        return (
            f"You are {agent.display_name}, {agent.role}, a specialist serving Lilith, "
            "Queen / Prime Orchestrator under Ainz / Overlord. "
            f"Your mission role: {agent.mission} "
            "Stay inside the delegated scope. Report observed evidence separately from "
            "inference. Your result is advisory until Lilith verifies it."
        )

    @staticmethod
    def _dispatch_key(
        agent_id: str,
        prompt: str,
        workdir: str,
        mission_id: str,
        provenance: dict[str, str] | None = None,
    ) -> str:
        raw = json.dumps(
            {
                "agent": agent_id,
                "prompt": prompt,
                "workdir": workdir,
                "mission_id": mission_id,
                "provenance": dict(sorted((provenance or {}).items())),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def dispatch(
        self,
        agent_name: str,
        prompt: str,
        *,
        mission_id: str = "",
        workdir: str = "",
        required_compute: set[str] | frozenset[str] | None = None,
        healthy_compute: set[str] | None = None,
        agentic: bool | None = None,
        structured: bool = False,
        max_turns: int = 10,
        max_tokens: int | None = None,
        timeout: int = 1800,
        provenance: dict[str, str] | None = None,
    ) -> ToolResult:
        agent = CourtRegistry.get(agent_name)
        if agent is None or agent.agent_id == "lilith":
            return ToolResult(False, None, f"court agent desconocido: {agent_name}")
        prompt = str(prompt).strip()
        if not prompt:
            return ToolResult(False, None, "prompt vacío")
        target = str(Path(workdir).expanduser().resolve()) if workdir else ""
        if target and agent.can_execute:
            authority = AuthorityPolicy().evaluate("file_edit", path=target)
            if not authority.allowed:
                return ToolResult(False, authority.as_dict(), authority.reason)

        needs = set(required_compute or MissionKernel.compute_needs(agent))
        preferred = "cli" if agent.can_admin else None
        from .learning import LearningEngine

        learned_avoid = LearningEngine(state_path=self.state_path).avoided_resources(
            agent.agent_id
        )
        if healthy_compute is None:
            healthy_compute = self.health.healthy_ids(court_agent=agent.agent_id)
        try:
            route = self.compute.route(
                needs,
                healthy=healthy_compute,
                avoid=learned_avoid,
                preferred_transport=preferred,
            )
        except LookupError as exc:
            return ToolResult(False, None, str(exc))
        campaign_id = os.environ.get("YGGDRASIL_CAMPAIGN_ID") or mission_id or "default"
        budget_decision = self.budget_guard.check(
            campaign_id=campaign_id,
            provider=route.resource.resource_id,
            prospective_cost_class=route.resource.cost_class,
        )
        if not budget_decision.allowed:
            return ToolResult(
                False,
                {
                    "court_agent": agent.agent_id,
                    "compute_resource": route.resource.resource_id,
                    "budget": budget_decision.as_dict(),
                },
                budget_decision.reason,
            )
        effective_agentic = agent.can_execute if agentic is None else bool(agentic)
        provenance = {
            str(key): str(value) for key, value in (provenance or {}).items()
            if str(key).strip() and str(value).strip()
        }
        key = self._dispatch_key(
            agent.agent_id, prompt, target, mission_id, provenance=provenance
        )
        store = self._store()
        state = store.get()
        existing = next(
            (task for task in state.get("tasks", []) if task.get("idempotency_key") == key),
            None,
        )
        if existing is not None:
            if existing.get("status") == "completada":
                return ToolResult(
                    True,
                    {"deduplicated": True, "court_agent": agent.agent_id, "task": existing},
                    "",
                )
            return ToolResult(
                False,
                {
                    "deduplicated": True,
                    "court_agent": agent.agent_id,
                    "task": existing,
                    "effects_unknown": existing.get("status") == "delegada",
                },
                "existing dispatch must be reconciled before retry",
            )

        task = store.add_task(
            f"{agent.display_name}: {' '.join(prompt.split())[:64]}",
            " ".join(prompt.split())[:500],
            status="delegada",
            preset=agent.agent_id,
            routing={
                "court_agent": agent.as_dict(),
                "compute_resource": route.resource.as_dict(),
                "adapter": route.resource.adapter,
                "provenance": provenance,
            },
            correlation_id=mission_id or None,
            idempotency_key=key,
        )
        started = time.perf_counter()
        result = self._execute_route(
            agent,
            route,
            prompt,
            workdir=target,
            agentic=effective_agentic,
            structured=structured,
            max_turns=max_turns,
            max_tokens=max_tokens,
            timeout=timeout,
            request_id=key[:32],
        )
        latency_ms = max(0, int((time.perf_counter() - started) * 1000))
        return self._finalize(
            store,
            task,
            agent,
            route,
            result,
            latency_ms=latency_ms,
            mission_id=mission_id,
        )

    def _execute_route(
        self,
        agent: CourtAgent,
        route: ComputeRoute,
        prompt: str,
        *,
        workdir: str,
        agentic: bool,
        structured: bool,
        max_turns: int,
        max_tokens: int | None,
        timeout: int,
        request_id: str,
    ) -> ToolResult:
        full_prompt = f"{self._system(agent)}\n\nDelegated task:\n{prompt}"
        adapter = route.resource.adapter
        if adapter == "claude_code":
            return MuninnDelegateTool().execute(
                task=full_prompt,
                cd=workdir or None,
                timeout=timeout,
                request_id=request_id,
            )
        if adapter == "codex_cli":
            return VorDelegateTool().execute(
                task=full_prompt,
                cd=workdir or None,
                timeout=timeout,
                request_id=request_id,
            )
        if adapter == "fabric":
            return self._fabric_delegate(
                agent,
                route,
                prompt,
                workdir=workdir,
                agentic=agentic,
                structured=structured,
                max_turns=max_turns,
                max_tokens=max_tokens,
            )
        return ToolResult(False, None, f"adapter no soportado: {adapter}")

    def _fabric_delegate(
        self,
        agent: CourtAgent,
        route: ComputeRoute,
        prompt: str,
        *,
        workdir: str,
        agentic: bool,
        structured: bool,
        max_turns: int,
        max_tokens: int | None,
    ) -> ToolResult:
        from lilith_cli.config import load_config
        from lilith_cli.providers import LLMProviderWrapper

        cfg = load_config().model_copy(deep=True)
        cfg.provider = "fabric"
        cfg.model = route.resource.metadata.get("model") or "router"
        cfg.temperature = 0.1 if agent.can_review else 0.25
        if max_tokens is not None:
            cfg.max_tokens = int(max_tokens)
        delegate = DelegateSubagentTool()
        base_system = self._system(agent)
        if agentic:
            return delegate._execute_agentic(
                preset_name=agent.agent_id,
                provider_name="fabric",
                cfg=cfg,
                prompt=prompt,
                base_system=base_system,
                workdir_arg=workdir,
                max_turns=max(1, min(30, int(max_turns))),
                structured=structured,
                LLMProviderWrapper=LLMProviderWrapper,
            )

        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": base_system + (
                    " You have no tools in this delegation. Return the work product "
                    "directly and say when evidence is missing."
                ),
            },
            {"role": "user", "content": prompt},
        ]

        async def run() -> dict[str, Any]:
            provider = LLMProviderWrapper(cfg)
            try:
                return await provider.complete(messages, model=cfg.model)
            finally:
                await provider.close()

        try:
            response = asyncio.run(run())
        except Exception as exc:  # noqa: BLE001 - provider boundary
            return ToolResult(
                False,
                {
                    "provider": "fabric",
                    "model": cfg.model,
                    "usage": {},
                },
                f"{agent.display_name} falló: {type(exc).__name__}: {exc}",
            )
        content = str(response.get("content") or "")
        usage = response.get("usage") or {}
        if structured:
            validated, raw_content, errors = asyncio.run(
                delegate._enforce_structured(
                    content=content,
                    preset_name=agent.agent_id,
                    provider_name="fabric",
                    cfg=cfg,
                    base_system=base_system,
                    response_format=delegate._provider_response_format(),
                )
            )
            return ToolResult(
                validated is not None,
                {
                    "provider": "fabric",
                    "model": cfg.model,
                    "content": validated.get("summary", "") if validated else content,
                    "usage": usage,
                    "structured": validated,
                    "validation_errors": errors,
                    "raw_content": raw_content if validated is None else None,
                },
                "" if validated is not None else f"structured validation failed: {errors}",
            )
        return ToolResult(
            True,
            {
                "provider": "fabric",
                "model": cfg.model,
                "content": content,
                "usage": usage,
            },
            "",
        )

    @staticmethod
    def _decorate(
        result: ToolResult,
        agent: CourtAgent,
        route: ComputeRoute,
    ) -> dict[str, Any]:
        data = dict(result.data) if isinstance(result.data, dict) else {"raw": result.data}
        legacy = data.pop("agent", None)
        if legacy:
            data["backend_legacy_name"] = legacy
        data.update({
            "court_agent": agent.agent_id,
            "court_name": agent.display_name,
            "court_role": agent.role,
            "compute_resource": route.resource.resource_id,
            "transport": route.resource.transport,
            "adapter": route.resource.adapter,
        })
        return data

    def _finalize(
        self,
        store: OrchestrationStateStore,
        task: dict[str, Any],
        agent: CourtAgent,
        route: ComputeRoute,
        result: ToolResult,
        *,
        latency_ms: int,
        mission_id: str,
    ) -> ToolResult:
        data = self._decorate(result, agent, route)
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        content = str(
            data.get("content") or data.get("raw_content") or result.error or ""
        )
        turns = int(data.get("turns_used") or 0)
        provenance = dict((task.get("routing") or {}).get("provenance") or {})
        post_mortem = {
            "task_id": task["id"],
            "correlation_id": mission_id or task.get("correlation_id"),
            "preset": agent.agent_id,
            "provider": route.resource.resource_id,
            "adapter": route.resource.adapter,
            "success": bool(result.success),
            "cause": "" if result.success else str(result.error or content)[:1000],
            "quality": 1.0 if result.success else 0.0,
            "latency_ms": latency_ms,
            "turns": turns,
            "usage": usage,
            **provenance,
        }
        try:
            if usage:
                store.record_cost(
                    agent.agent_id,
                    route.resource.resource_id,
                    usage,
                    session_id=os.environ.get("YGGDRASIL_CAMPAIGN_ID") or mission_id or "default",
                )
            store.append_post_mortem(post_mortem)
            updated = store.update_task(
                task["id"],
                status="completada" if result.success else "fallida",
                result=content[:1000],
                usage=usage,
                provider=route.resource.resource_id,
                turns=turns,
                post_mortem=post_mortem,
            )
        except Exception as exc:  # noqa: BLE001 - durable-state boundary
            data["effects_unknown"] = bool(result.success)
            data["state_task_id"] = task["id"]
            return ToolResult(
                False,
                data,
                f"dispatch finished but durable state failed: {type(exc).__name__}: {exc}",
            )
        data["state_task_id"] = updated["id"]
        data["dispatch_key"] = task.get("idempotency_key")
        data["post_mortem"] = post_mortem
        return ToolResult(result.success, data, result.error)
