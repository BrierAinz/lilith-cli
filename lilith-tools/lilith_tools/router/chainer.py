"""Tool chaining for multi-step operations."""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel


if TYPE_CHECKING:
    from lilith_tools.base import BaseTool, ToolResult
    from lilith_tools.registry import ToolRegistry


class ChainStep(BaseModel):
    """A single step in a tool chain."""

    tool_name: str
    params: dict[str, Any] = {}
    condition: str | None = None


class ToolChain(BaseModel):
    """A named sequence of tool execution steps."""

    name: str
    description: str
    steps: list[ChainStep]
    required_tools: list[str] = []


class ChainResult(BaseModel):
    """Result of executing a tool chain."""

    success: bool
    results: list[dict[str, Any]] = []
    errors: list[str] = []
    execution_time: float = 0.0


class ChainExecutor:
    """Execute tool chains sequentially, passing context between steps."""

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def execute(
        self,
        chain: ToolChain,
        context: dict[str, Any] | None = None,
    ) -> ChainResult:
        """Execute a *chain* of tool steps sequentially.

        Each step's result is fed into the context for subsequent steps.
        Steps with unmet conditions are skipped.
        """
        start = time.time()
        results: list[dict[str, Any]] = []
        errors: list[str] = []
        ctx = dict(context) if context else {}

        for step in chain.steps:
            # Evaluate condition (skip step if condition is false)
            if step.condition and not self._evaluate_condition(step.condition, ctx):
                results.append({"skipped": True, "reason": f"Condition not met: {step.condition}"})
                continue

            # Resolve parameter placeholders from context
            resolved_params = self._inject_context(step.params, ctx)

            # Look up the tool
            tool_cls = self.registry.get(step.tool_name)
            if tool_cls is None:
                err = f"Tool not found: {step.tool_name}"
                errors.append(err)
                results.append({"error": err})
                continue

            # Execute
            tool: BaseTool = tool_cls()
            try:
                result: ToolResult = tool.execute(**resolved_params)
                step_result = {
                    "tool": step.tool_name,
                    "success": result.success,
                    "data": result.data,
                    "error": result.error,
                }
                # Feed result into context for next step
                ctx[step.tool_name] = result.data
                ctx["result"] = {
                    "success": result.success,
                    "data": result.data,
                    "error": result.error,
                }
            except Exception as exc:
                err = f"{step.tool_name}: {exc}"
                errors.append(err)
                step_result = {"tool": step.tool_name, "error": err}
                ctx["result"] = {"success": False, "error": err}

            results.append(step_result)

        elapsed = time.time() - start
        has_errors = len(errors) > 0
        return ChainResult(
            success=not has_errors,
            results=results,
            errors=errors,
            execution_time=round(elapsed, 4),
        )

    # ------------------------------------------------------------------
    # Condition evaluation
    # ------------------------------------------------------------------

    @staticmethod
    def _evaluate_condition(condition: str, context: dict[str, Any]) -> bool:
        """Evaluate a simple condition string against *context*.

        Supported patterns:
        - ``result.success == True``
        - ``result.success == False``
        - ``<key>.<attr> == <value>``
        """
        # Parse "key.attr == value" patterns
        match = re.match(
            r"([\w.]+)\s*==\s*(True|False|\d+|'[^']*'|\"[^\"]*\")",
            condition.strip(),
        )
        if match:
            lhs_key = match.group(1)
            rhs_raw = match.group(2)

            # Traverse context for nested keys (e.g. "result.success")
            value = _deep_get(context, lhs_key)

            # Parse right-hand side
            if rhs_raw == "True":
                rhs = True
            elif rhs_raw == "False":
                rhs = False
            elif rhs_raw.isdigit():
                rhs = int(rhs_raw)
            else:
                rhs = rhs_raw.strip("'\"")
            return value == rhs

        # Fallback: treat non-empty truthy context key as True
        return bool(context)

    # ------------------------------------------------------------------
    # Context injection
    # ------------------------------------------------------------------

    @staticmethod
    def _inject_context(params: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        """Replace ``{{key}}`` placeholders in param values with context values."""
        resolved: dict[str, Any] = {}
        for key, value in params.items():
            if isinstance(value, str):
                resolved[key] = _resolve_placeholders(value, context)
            elif isinstance(value, dict):
                resolved[key] = {
                    k: _resolve_placeholders(v, context) if isinstance(v, str) else v
                    for k, v in value.items()
                }
            else:
                resolved[key] = value
        return resolved


# ------------------------------------------------------------------
# Builtin chain definitions
# ------------------------------------------------------------------

BUILTIN_CHAINS: dict[str, ToolChain] = {
    "search_and_summarize": ToolChain(
        name="search_and_summarize",
        description="Search the web and summarize the results",
        steps=[
            ChainStep(tool_name="web_search", params={"query": "{{query}}"}),
            ChainStep(
                tool_name="coding",
                params={"code": "print('''{{result}}''')"},
                condition="result.success == True",
            ),
        ],
        required_tools=["web_search", "coding"],
    ),
    "debug_cycle": ToolChain(
        name="debug_cycle",
        description="Read file, analyze, and attempt fix",
        steps=[
            ChainStep(tool_name="file_read", params={"path": "{{path}}"}),
            ChainStep(
                tool_name="coding",
                params={"code": "# Analyze: {{result}}"},
                condition="result.success == True",
            ),
            ChainStep(tool_name="coding", params={"code": "# Fix attempt"}),
        ],
        required_tools=["file_read", "coding"],
    ),
    "deploy_pipeline": ToolChain(
        name="deploy_pipeline",
        description="Execute code, run command, verify",
        steps=[
            ChainStep(tool_name="coding", params={"code": "{{code}}"}),
            ChainStep(
                tool_name="system_info",
                params={},
                condition="result.success == True",
            ),
        ],
        required_tools=["coding", "system_info"],
    ),
}


def _deep_get(data: dict[str, Any], dotted_key: str) -> Any:
    """Traverse a nested dict using a dotted key path like 'result.success'."""
    keys = dotted_key.split(".")
    value: Any = data
    for key in keys:
        if isinstance(value, dict):
            value = value.get(key)
        else:
            return None
    return value


def _resolve_placeholders(text: str, context: dict[str, Any]) -> str:
    """Replace all ``{{key}}`` and ``{{key.sub}}`` placeholders in *text*."""

    def _replace(m: re.Match[str]) -> str:
        key = m.group(1)
        value = _deep_get(context, key)
        return str(value) if value is not None else m.group(0)

    return re.sub(r"\{\{([\w.]+)\}\}", _replace, text)


def get_builtin_chain(name: str) -> ToolChain | None:
    """Return a builtin chain by name, or *None*."""
    return BUILTIN_CHAINS.get(name)
