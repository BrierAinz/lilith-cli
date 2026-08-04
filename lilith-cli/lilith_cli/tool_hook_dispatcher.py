"""Tool hook dispatch isolated from the conversation runtime.

``AgentSession`` owns the tool execution loop, but it should not need to know
how ``lilith_core`` hook contexts are constructed or how hook failures are
handled. This module contains that policy boundary while keeping hooks an
optional, fail-open integration.
"""

from __future__ import annotations

import logging
from typing import Any


logger = logging.getLogger(__name__)


class ToolHookDispatcher:
    """Fire pre/post tool hooks through an optional registry."""

    def __init__(
        self,
        registry: Any = None,
        *,
        session_id: str = "",
        failures: int = 0,
    ) -> None:
        self.registry = registry
        self.session_id = session_id
        self.failures = failures

    def attach(self, registry: Any, *, session_id: str = "") -> None:
        """Attach or detach a registry for one session."""
        self.registry = registry
        self.session_id = session_id

    def fire_pre(
        self,
        tool_name: str,
        params: dict[str, Any],
        *,
        agent_name: str,
    ) -> tuple[bool, dict[str, Any]]:
        """Return whether execution is allowed and the effective parameters."""
        if self.registry is None:
            return True, params

        try:
            from lilith_core.hooks import HookContext, HookType

            context = HookContext(
                hook_type=HookType.PRE_TOOL_CALL,
                agent_name=agent_name,
                session_id=self.session_id,
                data={"tool_name": tool_name, "params": dict(params)},
            )
            result = self.registry.fire(context)
        except Exception as exc:  # pragma: no cover - defensive
            self.failures += 1
            logger.warning("pre_tool_call hook failed (non-fatal): %s", exc)
            return True, params

        if result is None:
            return False, params

        effective = result.data.get("params", params)
        if not isinstance(effective, dict):
            effective = params
        return True, effective

    def fire_post(
        self,
        tool_name: str,
        params: dict[str, Any],
        result: Any,
        *,
        agent_name: str,
    ) -> Any:
        """Return the hook-rewritten result or structured suppression error."""
        if self.registry is None:
            return result

        try:
            from lilith_core.hooks import HookContext, HookType

            context = HookContext(
                hook_type=HookType.POST_TOOL_CALL,
                agent_name=agent_name,
                session_id=self.session_id,
                data={
                    "tool_name": tool_name,
                    "params": dict(params),
                    "result": result,
                },
            )
            hook_result = self.registry.fire(context)
        except Exception as exc:  # pragma: no cover - defensive
            self.failures += 1
            logger.warning("post_tool_call hook failed (non-fatal): %s", exc)
            return result

        if hook_result is None:
            try:
                from .providers import ToolResult

                return ToolResult(
                    tool_call_id="",
                    name=tool_name,
                    content=(
                        f"Error: result for '{tool_name}' suppressed by "
                        "post_tool_call hook"
                    ),
                )
            except Exception:  # pragma: no cover - defensive
                return result
        return hook_result.data.get("result", result)


__all__ = ["ToolHookDispatcher"]
