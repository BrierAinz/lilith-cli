"""Sub-agents: definition primitive + registry + spawning runner.

Inspired by Neurosurfer's ``subagents`` module. Yggdrasil's agent system
(Odin, Mimir, Adan, Eva, Shalltear, Heimdall…) is a *registry* of personas,
but the engine has no first-class primitive for *spawning a child agent*
mid-workflow with:

  - forked context (child gets its own message history, not a clone of parent)
  - filtered tool pool (allow / deny lists cut the parent's tools)
  - parallel dispatch (asyncio.gather for fan-out)
  - depth cap (recursive spawning has a hard ceiling)
  - concurrency cap (max in-flight sub-agents per runner)
  - per-definition model preference ("fast" tier / "inherit" / default)

This module is the engine primitive. Products (e.g. lilith-agent,
lilith-cli) register their personas via :func:`register` at import time.

A :class:`SubAgentDefinition` declares a role::

    from lilith_orchestrator.subagents import (
        SubAgentDefinition, register, SubAgentRunner,
    )

    register(SubAgentDefinition(
        agent_type="researcher",
        when_to_use="read-only web/file research, no edits",
        system_prompt="You are Mimir, the wise researcher. ...",
        allowed_tools=["read_file", "search_files", "web_search"],
        disallowed_tools=["write_file", "terminal"],
        model_preference="fast",
    ))

    runner = SubAgentRunner(
        full_tool_pool=ALL_TOOLS,
        provider=my_provider,
        guardrails=my_guardrails,
    )
    spawn_fn = runner.make_spawn_fn(parent_depth=0)
    result = await spawn_fn("researcher", "Summarize project X")

Why it matters:

  * Multi-agent orchestration needs *real* sub-agents, not threads that
    pretend. Forked context + tool filtering is the lingua franca of
    Claude Code sub-agents and OpenAI Codex sub-agents.
  * Neurosurfer proved this design works in production. Yggdrasil already
    has Bifrost IPC and AgentRegistry; this completes the missing layer.
  * Asyncio.gather enables fan-out (parallel research, parallel build/test)
    without blocking the orchestrator.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# lilith-core exposes the plugin hook system. The runner fires
# PRE_SUBAGENT_SPAWN (with rewrite/abort semantics) and
# POST_SUBAGENT_RESULT (observational + output redaction) so that
# policy / audit / telemetry plugins observe every sub-agent spawn.
try:
    from lilith_core.hooks import (
        HookContext,
        HookRegistry,
        HookType,
        get_hook_registry,
    )

    _HOOKS_AVAILABLE = True
except ImportError:  # pragma: no cover - lilith-core is a hard dep
    _HOOKS_AVAILABLE = False
    HookContext = None  # type: ignore[assignment,misc]
    HookRegistry = None  # type: ignore[assignment,misc]
    HookType = None  # type: ignore[assignment,misc]

    def get_hook_registry():  # type: ignore[no-redef]
        raise RuntimeError(
            "lilith_core.hooks unavailable; install lilith-core to enable hooks"
        )


logger = logging.getLogger("lilith.orchestrator.subagents")

# Hard ceiling on parent→child nesting. Resets per top-level spawn.
MAX_DEPTH: int = 3


# ──────────────────────────────────────────────────────────────────────────────
# Definition
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class SubAgentDefinition:
    """Declarative persona that can be spawned mid-workflow.

    Attributes:
        agent_type: Stable string key (e.g. ``"researcher"``).
        when_to_use: Human-readable hint for routing decisions.
        system_prompt: Static string OR zero-arg callable for lazy build.
        allowed_tools: ``["*"]`` inherits all parent tools. Otherwise an
            explicit allow-list; parent tools outside this set are hidden.
        disallowed_tools: Tools removed even if allowed. Applied last.
        model_preference: ``"fast"`` → cheap/fast tier, ``"inherit"`` → same
            as parent, ``None`` → runner default.
        max_concurrency: Per-type concurrency cap (overrides runner default).
        tags: Free-form labels for routing / metrics.
    """

    agent_type: str
    when_to_use: str = ""
    system_prompt: str | Callable[[], str] = ""
    allowed_tools: list[str] = field(default_factory=lambda: ["*"])
    disallowed_tools: list[str] = field(default_factory=list)
    model_preference: str | None = None
    max_concurrency: int | None = None
    tags: list[str] = field(default_factory=list)

    def get_system_prompt(self) -> str:
        """Resolve the system prompt (call lazy callable if needed)."""
        if callable(self.system_prompt):
            return self.system_prompt()
        return self.system_prompt

    def resolve_tools(self, pool_names: list[str]) -> list[str]:
        """Return the tool names this agent may use given the parent's pool.

        Resolution order:
          1. If ``allowed_tools`` is ``["*"]``, start from the full pool.
          2. Otherwise intersect ``allowed_tools`` with the pool.
          3. Remove anything in ``disallowed_tools``.
        """
        if self.allowed_tools == ["*"]:
            allowed = set(pool_names)
        else:
            allowed = set(self.allowed_tools) & set(pool_names)
        denied = set(self.disallowed_tools)
        return [n for n in pool_names if n in allowed and n not in denied]


# ──────────────────────────────────────────────────────────────────────────────
# Registry
# ──────────────────────────────────────────────────────────────────────────────


_REGISTRY: dict[str, SubAgentDefinition] = {}


def register(defn: SubAgentDefinition) -> None:
    """Register (or overwrite) a sub-agent definition.

    Args:
        defn: The definition to publish. ``defn.agent_type`` becomes the key.
    """
    _REGISTRY[defn.agent_type] = defn
    logger.debug("subagent.register type=%s", defn.agent_type)


def get_agent(agent_type: str) -> SubAgentDefinition | None:
    """Return the definition for ``agent_type`` or ``None`` if unknown."""
    return _REGISTRY.get(agent_type)


def all_agents() -> list[SubAgentDefinition]:
    """Return a snapshot list of every registered definition."""
    return list(_REGISTRY.values())


def unregister(agent_type: str) -> bool:
    """Remove a definition. Returns ``True`` if it existed."""
    existed = _REGISTRY.pop(agent_type, None) is not None
    if existed:
        logger.debug("subagent.unregister type=%s", agent_type)
    return existed


def clear_registry() -> None:
    """Wipe the registry. Test helper — do not call in production."""
    _REGISTRY.clear()


def agent_types() -> list[str]:
    """Return sorted registered agent type keys."""
    return sorted(_REGISTRY.keys())


# ──────────────────────────────────────────────────────────────────────────────
# Guardrails
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class SubAgentGuardrails:
    """Per-runner concurrency and depth limits.

    Attributes:
        max_concurrent_subagents: Hard cap on in-flight spawns per runner.
        max_depth: Maximum parent→child nesting (absolute ceiling).
        timeout_seconds: Per-spawn wall-clock cap (0 = no timeout).
    """

    max_concurrent_subagents: int = 4
    max_depth: int = MAX_DEPTH
    timeout_seconds: float = 0.0

    def __post_init__(self) -> None:
        if self.max_concurrent_subagents < 1:
            raise ValueError("max_concurrent_subagents must be >= 1")
        if self.max_depth < 0:
            raise ValueError("max_depth must be >= 0")


# ──────────────────────────────────────────────────────────────────────────────
# Spawn function protocol
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class SubAgentResult:
    """Outcome of a single sub-agent spawn.

    Attributes:
        agent_type: The type that was spawned.
        spawn_id: Unique correlation id.
        output: Final string report from the sub-agent.
        depth: Nesting depth (0 = direct child of root).
        duration_ms: Wall-clock time the spawn took.
        success: ``False`` if the spawn raised.
        error: Exception string, present only when ``success`` is False.
        tools_used: Subset of tools actually exercised (populated by runner).
    """

    agent_type: str
    spawn_id: str
    output: str
    depth: int
    duration_ms: float
    success: bool = True
    error: str | None = None
    tools_used: list[str] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────────────
# Runner
# ──────────────────────────────────────────────────────────────────────────────


class SubAgentRunner:
    """Spawns child agents with forked context and filtered tools.

    The runner is *not* an LLM driver itself — it delegates the actual
    generation to a callable ``executor`` supplied by the host application
    (lilith-agent, lilith-cli, etc.). This keeps the primitive pure and
    testable: tests pass a fake executor that returns deterministic strings.

    Args:
        full_tool_pool: Names of every tool available to the parent agent.
        executor: Async callable ``(system_prompt, user_input, tool_names,
            model_preference) -> str`` that performs the actual work.
        guardrails: Concurrency / depth / timeout limits. Defaults applied
            when omitted.
        default_model_preference: Used when a definition omits
            ``model_preference``.
        hook_registry: Optional ``lilith_core.hooks.HookRegistry`` that the
            runner fires ``PRE_SUBAGENT_SPAWN`` and ``POST_SUBAGENT_RESULT``
            events through. ``None`` (default) means hook firing is
            disabled — the runner behaves exactly as before, which keeps
            existing call sites and tests untouched. Pass an explicit
            registry to enable rewrite / abort / audit / telemetry
            integrations. The default runner does *not* reach into the
            global registry to avoid surprising side-effects in tests
            and embedded use.
    """

    def __init__(
        self,
        full_tool_pool: list[str],
        executor: Callable[..., Any] | None = None,
        guardrails: SubAgentGuardrails | None = None,
        default_model_preference: str | None = None,
        hook_registry: Any | None = None,
    ) -> None:
        self.full_tool_pool = list(full_tool_pool)
        self.executor = executor
        self.guardrails = guardrails or SubAgentGuardrails()
        self.default_model_preference = default_model_preference
        self._semaphore = asyncio.Semaphore(self.guardrails.max_concurrent_subagents)
        self._in_flight: set[str] = set()
        self._completed: int = 0
        self._failed: int = 0
        self._type_sems: dict[str, asyncio.Semaphore] = {}
        # Optional plugin hook registry. When set, the runner fires
        # PRE_SUBAGENT_SPAWN (with rewrite / abort) and
        # POST_SUBAGENT_RESULT (observational) on every spawn. See
        # ``lilith_core.hooks.HookType`` for the contract.
        self.hook_registry: Any = hook_registry
        # Counters for hook-driven events; useful for tests / metrics.
        self._hooks_fired: int = 0
        self._hooks_aborted: int = 0
        self._hooks_rewritten: int = 0

    # ── Stats ─────────────────────────────────────────────────────────────

    @property
    def stats(self) -> dict[str, int]:
        """Return a snapshot of runner statistics."""
        return {
            "completed": self._completed,
            "failed": self._failed,
            "in_flight": len(self._in_flight),
            "max_concurrent": self.guardrails.max_concurrent_subagents,
            "hooks_fired": self._hooks_fired,
            "hooks_aborted": self._hooks_aborted,
            "hooks_rewritten": self._hooks_rewritten,
        }

    def reset_stats(self) -> None:
        """Zero all counters. Does not affect the semaphore."""
        self._completed = 0
        self._failed = 0
        self._in_flight.clear()
        self._hooks_fired = 0
        self._hooks_aborted = 0
        self._hooks_rewritten = 0

    # ── Spawning ──────────────────────────────────────────────────────────

    def make_spawn_fn(self, parent_depth: int = 0) -> Callable[..., Any]:
        """Return a bound spawn callable for use as a parent's tool.

        Args:
            parent_depth: The depth of the *caller* (0 for the root engine).

        Returns:
            Async ``spawn(agent_type, user_input)`` that returns
            :class:`SubAgentResult`. ``spawn_many([...])`` is also attached.
        """

        async def spawn(agent_type: str, user_input: str) -> SubAgentResult:
            return await self._spawn_one(agent_type, user_input, parent_depth)

        async def spawn_many(
            calls: list[tuple[str, str]],
        ) -> list[SubAgentResult]:
            """Fan-out spawn. Returns results in the same order as ``calls``."""
            tasks = [
                self._spawn_one(t, msg, parent_depth) for t, msg in calls
            ]
            return await asyncio.gather(*tasks, return_exceptions=False)

        spawn.spawn_many = spawn_many  # type: ignore[attr-defined]
        return spawn

    async def _spawn_one(
        self,
        agent_type: str,
        user_input: str,
        parent_depth: int,
    ) -> SubAgentResult:
        """Internal single-spawn implementation with concurrency + depth caps."""
        spawn_id = uuid.uuid4().hex[:12]
        depth = parent_depth + 1
        start = time.monotonic()

        # Depth cap (absolute ceiling, not relative)
        if depth > self.guardrails.max_depth:
            self._failed += 1
            return SubAgentResult(
                agent_type=agent_type,
                spawn_id=spawn_id,
                output="",
                depth=depth,
                duration_ms=(time.monotonic() - start) * 1000,
                success=False,
                error=f"max_depth exceeded (depth={depth}, max={self.guardrails.max_depth})",
            )

        defn = get_agent(agent_type)
        if defn is None:
            self._failed += 1
            return SubAgentResult(
                agent_type=agent_type,
                spawn_id=spawn_id,
                output="",
                depth=depth,
                duration_ms=(time.monotonic() - start) * 1000,
                success=False,
                error=f"unknown agent_type: {agent_type}",
            )

        tool_names = defn.resolve_tools(self.full_tool_pool)
        model_pref = defn.model_preference or self.default_model_preference

        # ── PRE_SUBAGENT_SPAWN hook (rewrite / abort / audit) ──────────────
        # Fires once we know the definition + filtered tool set. Hooks can
        # rewrite ``user_input`` (e.g. policy adds context, PII redaction)
        # or return ``None`` to abort the spawn entirely. The runner treats
        # None as a policy veto and returns success=False without invoking
        # the executor.
        effective_user_input = user_input
        effective_tool_names = list(tool_names)
        effective_model_pref = model_pref
        if self.hook_registry is not None and _HOOKS_AVAILABLE:
            pre_ctx = HookContext(
                hook_type=HookType.PRE_SUBAGENT_SPAWN,
                agent_name="subagent_runner",
                session_id=spawn_id,
                data={
                    "agent_type": agent_type,
                    "spawn_id": spawn_id,
                    "depth": depth,
                    "user_input": user_input,
                    "allowed_tools": list(tool_names),
                    "model_preference": model_pref,
                },
            )
            try:
                pre_result = self.hook_registry.fire(pre_ctx)
            except Exception as exc:  # noqa: BLE001 — never break a spawn
                logger.warning(
                    "subagent.hook_pre_failed type=%s id=%s err=%s",
                    agent_type,
                    spawn_id,
                    exc,
                )
                pre_result = pre_ctx
            self._hooks_fired += 1
            if pre_result is None:
                # Hook vetoed the spawn.
                self._hooks_aborted += 1
                self._failed += 1
                return SubAgentResult(
                    agent_type=agent_type,
                    spawn_id=spawn_id,
                    output="",
                    depth=depth,
                    duration_ms=(time.monotonic() - start) * 1000,
                    success=False,
                    error="aborted by hook",
                )
            # Apply any rewrites from the hook.
            data = pre_result.data
            if "user_input" in data and data["user_input"] != user_input:
                effective_user_input = data["user_input"]
                self._hooks_rewritten += 1
            if "allowed_tools" in data and data["allowed_tools"] != tool_names:
                # Allow the hook to tighten the tool pool further.
                effective_tool_names = list(data["allowed_tools"])
                self._hooks_rewritten += 1
            if "model_preference" in data and data["model_preference"] != model_pref:
                effective_model_pref = data["model_preference"]
                self._hooks_rewritten += 1

        # Per-type concurrency cap (if defined)
        per_type_cap = defn.max_concurrency

        async def _run() -> SubAgentResult:
            self._in_flight.add(spawn_id)
            try:
                if self.executor is None:
                    output = (
                        f"[stub:{agent_type}] {effective_user_input} "
                        f"(tools={effective_tool_names})"
                    )
                    tools_used: list[str] = []
                else:
                    coro = self.executor(
                        defn.get_system_prompt(),
                        effective_user_input,
                        effective_tool_names,
                        effective_model_pref,
                    )
                    if self.guardrails.timeout_seconds > 0:
                        output = await asyncio.wait_for(
                            coro, timeout=self.guardrails.timeout_seconds
                        )
                    else:
                        output = await coro
                    tools_used = list(effective_tool_names)

                self._completed += 1
                return SubAgentResult(
                    agent_type=agent_type,
                    spawn_id=spawn_id,
                    output=output if isinstance(output, str) else str(output),
                    depth=depth,
                    duration_ms=(time.monotonic() - start) * 1000,
                    success=True,
                    tools_used=tools_used,
                )
            except Exception as exc:  # noqa: BLE001 — runner boundary
                self._failed += 1
                logger.warning(
                    "subagent.spawn_failed type=%s id=%s err=%s",
                    agent_type,
                    spawn_id,
                    exc,
                )
                return SubAgentResult(
                    agent_type=agent_type,
                    spawn_id=spawn_id,
                    output="",
                    depth=depth,
                    duration_ms=(time.monotonic() - start) * 1000,
                    success=False,
                    error=f"{type(exc).__name__}: {exc}",
                )
            finally:
                self._in_flight.discard(spawn_id)

        # Per-type cap (local semaphore, lazy)
        if per_type_cap is not None and per_type_cap >= 1:
            type_sem = self._get_type_semaphore(agent_type, per_type_cap)
            async with type_sem:
                async with self._semaphore:
                    result = await _run()
        else:
            async with self._semaphore:
                result = await _run()

        # ── POST_SUBAGENT_RESULT hook (observational + output rewrite) ────
        if self.hook_registry is not None and _HOOKS_AVAILABLE:
            post_ctx = HookContext(
                hook_type=HookType.POST_SUBAGENT_RESULT,
                agent_name="subagent_runner",
                session_id=spawn_id,
                data={
                    "agent_type": agent_type,
                    "spawn_id": spawn_id,
                    "depth": depth,
                    "output": result.output,
                    "success": result.success,
                    "error": result.error,
                    "tools_used": list(result.tools_used),
                    "duration_ms": result.duration_ms,
                },
            )
            try:
                post_result = self.hook_registry.fire(post_ctx)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "subagent.hook_post_failed type=%s id=%s err=%s",
                    agent_type,
                    spawn_id,
                    exc,
                )
                post_result = post_ctx
            self._hooks_fired += 1
            if post_result is not None:
                rewritten = post_result.data.get("output")
                if isinstance(rewritten, str) and rewritten != result.output:
                    result = SubAgentResult(
                        agent_type=result.agent_type,
                        spawn_id=result.spawn_id,
                        output=rewritten,
                        depth=result.depth,
                        duration_ms=result.duration_ms,
                        success=result.success,
                        error=result.error,
                        tools_used=list(result.tools_used),
                    )
                    self._hooks_rewritten += 1
        return result

    # ── Per-type semaphore cache ──────────────────────────────────────────

    def _get_type_semaphore(
        self, agent_type: str, limit: int
    ) -> asyncio.Semaphore:
        """Return a cached semaphore for ``agent_type`` at ``limit``."""
        sem = self._type_sems.get(agent_type)
        if sem is None or sem._value != limit:  # type: ignore[attr-defined]
            sem = asyncio.Semaphore(limit)
            self._type_sems[agent_type] = sem
        return sem


# ──────────────────────────────────────────────────────────────────────────────
# Public factory + convenience
# ──────────────────────────────────────────────────────────────────────────────


def make_default_definitions() -> list[SubAgentDefinition]:
    """Return a starter set of personas for default registration.

    These mirror the Bifrost personas from Vanaheim. Calling this is
    optional — products are free to register only what they need.

    Personas shipped (8):
      - researcher (Mimir):   read-only research, no edits
      - editor (Adan):        targeted code edits via patch
      - auditor (Heimdall):   pre-commit audit, read-only
      - coder:                full read+write+patch+terminal, no network
      - tester:               read+terminal (for running tests), no network/write
      - security:             read-only + web_search (CVE lookup)
      - reviewer:             strict read-only, no edits, no execution
      - planner:              zero tools (pure reasoning, no I/O)
    """
    return [
        SubAgentDefinition(
            agent_type="researcher",
            when_to_use="Read-only research; no edits",
            system_prompt="You are Mimir, the wise researcher.",
            allowed_tools=["read_file", "search_files", "web_search"],
            disallowed_tools=["write_file", "terminal", "patch"],
            model_preference="fast",
            tags=["read-only", "research"],
        ),
        SubAgentDefinition(
            agent_type="editor",
            when_to_use="Targeted code edits via apply_patch",
            system_prompt="You are Adan, the precise editor.",
            allowed_tools=["read_file", "patch"],
            disallowed_tools=["terminal", "shell_exec"],
            model_preference="inherit",
            tags=["write", "code"],
        ),
        SubAgentDefinition(
            agent_type="auditor",
            when_to_use="Heimdall-style audit before commit",
            system_prompt="You are Heimdall, the watchman of the bifrost.",
            allowed_tools=["read_file", "search_files"],
            disallowed_tools=["write_file", "terminal", "patch"],
            model_preference="inherit",
            tags=["read-only", "audit"],
        ),
        SubAgentDefinition(
            agent_type="coder",
            when_to_use="Full-stack implementation: read, write, patch, terminal",
            system_prompt=(
                "You are a senior engineer. Read context, "
                "implement the requested change with write_file or "
                "patch, run quick verification via terminal, and report back."
            ),
            allowed_tools=[
                "read_file",
                "search_files",
                "write_file",
                "patch",
                "terminal",
            ],
            disallowed_tools=["web_search", "browser"],
            model_preference="inherit",
            tags=["write", "code", "implementation"],
        ),
        SubAgentDefinition(
            agent_type="tester",
            when_to_use="Run the test suite, capture failures, report back",
            system_prompt=(
                "You are a QA engineer. Run the relevant tests via "
                "terminal, parse failures, and report what's broken. "
                "Do not modify source files."
            ),
            allowed_tools=["read_file", "search_files", "terminal"],
            disallowed_tools=["write_file", "patch", "web_search"],
            model_preference="fast",
            tags=["read-mostly", "test"],
        ),
        SubAgentDefinition(
            agent_type="security",
            when_to_use="Read source, look up CVEs and best practices",
            system_prompt=(
                "You are a security engineer. Audit code paths for "
                "injection, secrets, and unsafe deserialization. Use "
                "web_search for known CVE lookups."
            ),
            allowed_tools=["read_file", "search_files", "web_search"],
            disallowed_tools=["write_file", "terminal", "patch"],
            model_preference="inherit",
            tags=["read-only", "security", "audit"],
        ),
        SubAgentDefinition(
            agent_type="reviewer",
            when_to_use="Strict read-only review; no edits, no execution",
            system_prompt=(
                "You are a code reviewer. Read the diff, evaluate "
                "correctness and style, leave detailed comments. "
                "Never write or run anything."
            ),
            allowed_tools=["read_file", "search_files"],
            disallowed_tools=[
                "write_file",
                "patch",
                "terminal",
                "web_search",
            ],
            model_preference="inherit",
            tags=["read-only", "review"],
        ),
        SubAgentDefinition(
            agent_type="planner",
            when_to_use="Pure reasoning; no I/O. Decompose tasks, propose plans.",
            system_prompt=(
                "You are a strategist. Given a goal, decompose it into "
                "ordered, testable steps. Do not perform any I/O — "
                "your output is the plan itself."
            ),
            allowed_tools=[],
            disallowed_tools=[
                "read_file",
                "search_files",
                "write_file",
                "patch",
                "terminal",
                "web_search",
            ],
            model_preference="inherit",
            tags=["no-io", "planning", "plan", "strategy", "design"],
        ),
    ]


def register_defaults() -> int:
    """Register the default personas if not already present.

    Returns:
        Number of *new* definitions registered.
    """
    added = 0
    for defn in make_default_definitions():
        if defn.agent_type not in _REGISTRY:
            register(defn)
            added += 1
    return added


def runner_with_global_hooks(
    full_tool_pool: list[str],
    executor: Callable[..., Any] | None = None,
    guardrails: SubAgentGuardrails | None = None,
    default_model_preference: str | None = None,
) -> SubAgentRunner:
    """Build a :class:`SubAgentRunner` wired to the global hook registry.

    Convenience constructor for products that want every spawn to
    flow through ``lilith_core.hooks.get_hook_registry()``. The global
    registry is shared across the host process, so a single audit /
    policy plugin registered there observes every SubAgentRunner in
    the process.

    If ``lilith_core.hooks`` is not importable (should not happen in
    production — lilith-core is a hard dep of the orchestrator) this
    function falls back to a runner with no hook wiring and emits a
    warning via the module logger.
    """
    if not _HOOKS_AVAILABLE:
        logger.warning(
            "lilith_core.hooks unavailable — runner built without global hooks"
        )
        return SubAgentRunner(
            full_tool_pool=full_tool_pool,
            executor=executor,
            guardrails=guardrails,
            default_model_preference=default_model_preference,
        )
    return SubAgentRunner(
        full_tool_pool=full_tool_pool,
        executor=executor,
        guardrails=guardrails,
        default_model_preference=default_model_preference,
        hook_registry=get_hook_registry(),
    )


__all__ = [
    "MAX_DEPTH",
    "SubAgentDefinition",
    "SubAgentGuardrails",
    "SubAgentResult",
    "SubAgentRunner",
    "agent_types",
    "all_agents",
    "clear_registry",
    "get_agent",
    "make_default_definitions",
    "register",
    "register_defaults",
    "unregister",
]