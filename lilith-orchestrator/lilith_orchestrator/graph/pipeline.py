"""5-Phase Pipeline for the Lilith orchestrator.

Inspired by Aether-Agents' sequential quality-gate pipeline:
    IDEA → RESEARCH → DESIGN → PLAN → CODE

Each phase has:
    - An entry condition (what must be true to start)
    - A node function (the work to do)
    - A quality gate (what must be true to proceed)

If a quality gate fails, the pipeline can:
    - Retry the phase (up to max_retries)
    - Loop back to a previous phase
    - Abort with an error

This module provides the pipeline definition, phase enum, quality gate
logic, and a PipelineRunner that executes phases sequentially with gates.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from lilith_core.hooks import HookContext, HookType, get_hook_registry

from lilith_orchestrator.graph.state import GraphState


# ── Phase Definition ─────────────────────────────────────────────────────────


class PipelinePhase(Enum):
    """The 5 phases of the Aether-inspired pipeline."""

    IDEA = "idea"
    RESEARCH = "research"
    DESIGN = "design"
    PLAN = "plan"
    CODE = "code"

    @property
    def order(self) -> int:
        """Phase order index (0-based)."""
        return list(PipelinePhase).index(self)

    def next(self) -> PipelinePhase | None:
        """Return the next phase, or None if this is the last."""
        phases = list(PipelinePhase)
        idx = phases.index(self)
        return phases[idx + 1] if idx + 1 < len(phases) else None

    @classmethod
    def from_string(cls, value: str) -> PipelinePhase:
        """Parse a phase from a string (case-insensitive)."""
        try:
            return cls(value.lower())
        except ValueError:
            raise ValueError(f"Unknown phase: {value!r}. Expected one of {[p.value for p in cls]}") from None


# ── Quality Gate ─────────────────────────────────────────────────────────────


@dataclass
class QualityGate:
    """A quality gate between pipeline phases.

    A gate has:
        - name: Human-readable name
        - check: Function that receives GraphState and returns (passed: bool, reason: str)
        - min_confidence: If check returns a confidence, must be >= this (default 0.0)
    """

    name: str
    check: Callable[[GraphState], tuple[bool, str]]
    min_confidence: float = 0.0

    def evaluate(self, state: GraphState) -> "GateResult":
        """Run the gate check and return a GateResult."""
        passed, reason = self.check(state)
        return GateResult(
            gate_name=self.name,
            passed=passed,
            reason=reason,
            timestamp=time.time(),
        )


@dataclass
class GateResult:
    """Result of a quality gate evaluation."""

    gate_name: str
    passed: bool
    reason: str = ""
    timestamp: float = field(default_factory=time.time)


# ── Pipeline Result ──────────────────────────────────────────────────────────


@dataclass
class PipelineResult:
    """Final result of a pipeline run.

    Attributes:
        success: Whether the pipeline completed all phases.
        completed_phases: List of phases that passed their gates.
        final_state: The GraphState at the end of the pipeline.
        gate_results: All gate evaluations during the run.
        errors: Any errors encountered.
        total_duration_ms: Total execution time.
    """

    success: bool
    completed_phases: list[PipelinePhase] = field(default_factory=list)
    final_state: GraphState | None = None
    gate_results: list[GateResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    total_duration_ms: float = 0.0
    aborted_at: PipelinePhase | None = None

    @property
    def phase_count(self) -> int:
        return len(self.completed_phases)


# ── Phase Node Functions ─────────────────────────────────────────────────────


def idea_node(state: GraphState) -> GraphState:
    """IDEA phase: capture and formalize the user's idea.

    Extracts the core concept from the user's message and stores it
    in the context as a structured idea.
    """
    messages = state.messages
    content = ""
    if messages:
        last = messages[-1]
        content = last.get("content", "") if isinstance(last, dict) else str(last)

    idea = {
        "raw_input": content,
        "summary": content[:200] if content else "",
        "timestamp": time.time(),
        "phase": PipelinePhase.IDEA.value,
    }

    new_context = {**state.context, "idea": idea, "pipeline_phase": PipelinePhase.IDEA.value}
    return state.copy_with(current_node="idea", context=new_context)


def research_node(state: GraphState) -> GraphState:
    """RESEARCH phase: gather information relevant to the idea.

    In production this would dispatch to Mimir or web search tools.
    Here it records the research context.
    """
    idea = state.context.get("idea", {})
    research = {
        "query": idea.get("summary", ""),
        "sources": [],
        "findings": [],
        "timestamp": time.time(),
        "phase": PipelinePhase.RESEARCH.value,
    }

    new_context = {**state.context, "research": research, "pipeline_phase": PipelinePhase.RESEARCH.value}
    return state.copy_with(current_node="research", context=new_context)


def design_node(state: GraphState) -> GraphState:
    """DESIGN phase: produce an architectural design from research.

    Creates a design document with components, data flow, and decisions.
    """
    design = {
        "components": [],
        "data_flow": [],
        "decisions": [],
        "constraints": [],
        "timestamp": time.time(),
        "phase": PipelinePhase.DESIGN.value,
    }

    new_context = {**state.context, "design": design, "pipeline_phase": PipelinePhase.DESIGN.value}
    return state.copy_with(current_node="design", context=new_context)


def plan_node(state: GraphState) -> GraphState:
    """PLAN phase: break the design into actionable tasks.

    Creates a task list with priorities and dependencies.
    """
    plan = {
        "tasks": [],
        "priorities": {},
        "dependencies": {},
        "estimated_effort": "",
        "timestamp": time.time(),
        "phase": PipelinePhase.PLAN.value,
    }

    new_context = {**state.context, "plan": plan, "pipeline_phase": PipelinePhase.PLAN.value}
    return state.copy_with(current_node="plan", context=new_context)


def code_node(state: GraphState) -> GraphState:
    """CODE phase: execute the plan and produce code.

    In production this would dispatch to Adan/Eva agents with tools.
    Here it records the code context.
    """
    code = {
        "files_created": [],
        "files_modified": [],
        "tests_run": False,
        "tests_passed": 0,
        "tests_failed": 0,
        "timestamp": time.time(),
        "phase": PipelinePhase.CODE.value,
    }

    new_context = {**state.context, "code": code, "pipeline_phase": PipelinePhase.CODE.value}
    return state.copy_with(current_node="code", context=new_context)


# ── Default Quality Gates ────────────────────────────────────────────────────


def _gate_idea(state: GraphState) -> tuple[bool, str]:
    """Gate: IDEA must have a non-empty summary."""
    idea = state.context.get("idea", {})
    summary = idea.get("summary", "")
    if not summary:
        return False, "Idea summary is empty"
    if len(summary) < 5:
        return False, "Idea summary too short (<5 chars)"
    return True, "Idea captured successfully"


def _gate_research(state: GraphState) -> tuple[bool, str]:
    """Gate: RESEARCH must have been performed."""
    research = state.context.get("research", {})
    if not research:
        return False, "No research data found"
    if "query" not in research:
        return False, "Research query missing"
    return True, "Research phase complete"


def _gate_design(state: GraphState) -> tuple[bool, str]:
    """Gate: DESIGN must have components defined."""
    design = state.context.get("design", {})
    if not design:
        return False, "No design data found"
    if "components" not in design:
        return False, "Design components missing"
    return True, "Design phase complete"


def _gate_plan(state: GraphState) -> tuple[bool, str]:
    """Gate: PLAN must have a task list."""
    plan = state.context.get("plan", {})
    if not plan:
        return False, "No plan data found"
    if "tasks" not in plan:
        return False, "Plan tasks missing"
    return True, "Plan phase complete"


def _gate_code(state: GraphState) -> tuple[bool, str]:
    """Gate: CODE must have at least one file created or modified."""
    code = state.context.get("code", {})
    if not code:
        return False, "No code data found"
    files_created = code.get("files_created", [])
    files_modified = code.get("files_modified", [])
    if not files_created and not files_modified:
        # Allow pass if tests were run (some cycles are test-only)
        if code.get("tests_run"):
            return True, "Code phase complete (test-only cycle)"
        return False, "No files created or modified"
    return True, "Code phase complete"


# ── Default Gate Map ─────────────────────────────────────────────────────────


DEFAULT_GATES: dict[PipelinePhase, QualityGate] = {
    PipelinePhase.IDEA: QualityGate(name="idea_gate", check=_gate_idea),
    PipelinePhase.RESEARCH: QualityGate(name="research_gate", check=_gate_research),
    PipelinePhase.DESIGN: QualityGate(name="design_gate", check=_gate_design),
    PipelinePhase.PLAN: QualityGate(name="plan_gate", check=_gate_plan),
    PipelinePhase.CODE: QualityGate(name="code_gate", check=_gate_code),
}


DEFAULT_NODES: dict[PipelinePhase, Callable[[GraphState], GraphState]] = {
    PipelinePhase.IDEA: idea_node,
    PipelinePhase.RESEARCH: research_node,
    PipelinePhase.DESIGN: design_node,
    PipelinePhase.PLAN: plan_node,
    PipelinePhase.CODE: code_node,
}


# ── Pipeline Runner ──────────────────────────────────────────────────────────


class PipelineRunner:
    """Executes the 5-phase pipeline with quality gates.

    Usage::

        runner = PipelineRunner()
        result = runner.run(initial_state)
        if result.success:
            print(f"Completed {result.phase_count} phases")
        else:
            print(f"Aborted at {result.aborted_at}: {result.errors}")

    Args:
        gates: Optional custom quality gates per phase.
        nodes: Optional custom node functions per phase.
        max_retries: Max retries per phase before aborting (default 2).
    """

    def __init__(
        self,
        gates: dict[PipelinePhase, QualityGate] | None = None,
        nodes: dict[PipelinePhase, Callable[[GraphState], GraphState]] | None = None,
        max_retries: int = 2,
        hooks: Any = None,
        agent_name: str = "lilith-pipeline",
    ) -> None:
        """Initialize the PipelineRunner.

        Args:
            gates: Optional custom quality gates per phase.
            nodes: Optional custom node functions per phase.
            max_retries: Max retries per phase before aborting (default 2).
            hooks: Optional HookRegistry. If None, uses the global registry.
            agent_name: Agent name used in hook contexts (default "lilith-pipeline").
        """
        self.gates = gates or dict(DEFAULT_GATES)
        self.nodes = nodes or dict(DEFAULT_NODES)
        self.max_retries = max_retries
        self._hooks = hooks if hooks is not None else get_hook_registry()
        self._agent_name = agent_name

    def _fire(
        self,
        hook_type: HookType,
        data: dict[str, Any],
        session_id: str = "",
    ) -> dict[str, Any] | None:
        """Fire a hook and return the (possibly modified) data dict.

        Returns None if any hook aborts the chain. Hooks may modify
        ``data`` by returning a modified HookContext.
        """
        ctx = HookContext(
            hook_type=hook_type,
            agent_name=self._agent_name,
            session_id=session_id or str(uuid.uuid4()),
            data=data,
        )
        result = self._hooks.fire(ctx)
        if result is None:
            return None
        return result.data

    def run(
        self,
        initial_state: GraphState,
        start_phase: PipelinePhase = PipelinePhase.IDEA,
        end_phase: PipelinePhase = PipelinePhase.CODE,
        session_id: str = "",
    ) -> PipelineResult:
        """Run the pipeline from start_phase to end_phase (inclusive).

        Fires the following hooks (inspired by Aether-Agents):
            - PRE_PIPELINE_START (before any phase)
            - PRE_PIPELINE_PHASE (before each phase node)
            - POST_PIPELINE_PHASE (after each phase gate)
            - POST_PIPELINE_END (after all phases succeed)
            - ON_ERROR (when a hook aborts or a gate fails to recover)

        Hooks can:
            - Modify phase data (e.g. set ``abort=True`` to skip phase)
            - Abort the chain by returning None
            - Observe progress for telemetry / auditing

        Args:
            initial_state: The GraphState to start with.
            start_phase: First phase to execute (default IDEA).
            end_phase: Last phase to execute (default CODE).
            session_id: Optional session ID for hook contexts.

        Returns:
            PipelineResult with success/failure info.
        """
        session_id = session_id or str(uuid.uuid4())
        start_time = time.time()
        state = initial_state
        completed: list[PipelinePhase] = []
        all_gate_results: list[GateResult] = []
        errors: list[str] = []

        phases = list(PipelinePhase)
        start_idx = phases.index(start_phase)
        end_idx = phases.index(end_phase)
        active_phases = phases[start_idx : end_idx + 1]

        # ── Fire PRE_PIPELINE_START hook ─────────────────────────────────
        start_data = {
            "start_phase": start_phase.value,
            "end_phase": end_phase.value,
            "active_phases": [p.value for p in active_phases],
            "total_phases": len(active_phases),
        }
        hook_result = self._fire(
            HookType.PRE_PIPELINE_START, start_data, session_id=session_id
        )
        if hook_result is None:
            return PipelineResult(
                success=False,
                completed_phases=completed,
                final_state=state,
                gate_results=all_gate_results,
                errors=["Pipeline aborted by PRE_PIPELINE_START hook"],
                total_duration_ms=(time.time() - start_time) * 1000,
            )

        for phase in active_phases:
            # Execute the phase node
            node_fn = self.nodes.get(phase)
            if node_fn is None:
                errors.append(f"No node function for phase {phase.value}")
                return PipelineResult(
                    success=False,
                    completed_phases=completed,
                    final_state=state,
                    gate_results=all_gate_results,
                    errors=errors,
                    total_duration_ms=(time.time() - start_time) * 1000,
                    aborted_at=phase,
                )

            # ── Fire PRE_PIPELINE_PHASE hook ─────────────────────────────
            pre_data = {
                "phase": phase.value,
                "attempt": 0,
                "completed_so_far": [p.value for p in completed],
            }
            pre_result = self._fire(
                HookType.PRE_PIPELINE_PHASE, pre_data, session_id=session_id
            )
            if pre_result is None:
                errors.append(f"Phase {phase.value} aborted by PRE_PIPELINE_PHASE hook")
                return PipelineResult(
                    success=False,
                    completed_phases=completed,
                    final_state=state,
                    gate_results=all_gate_results,
                    errors=errors,
                    total_duration_ms=(time.time() - start_time) * 1000,
                    aborted_at=phase,
                )
            # Hooks may set abort=True to skip this phase
            if pre_result.get("abort"):
                continue

            # Retry loop for this phase
            phase_passed = False
            for attempt in range(self.max_retries + 1):
                state = node_fn(state)

                # Evaluate quality gate
                gate = self.gates.get(phase)
                if gate is None:
                    # No gate for this phase — auto-pass
                    phase_passed = True
                    break

                gate_result = gate.evaluate(state)
                all_gate_results.append(gate_result)

                # ── Fire POST_PIPELINE_PHASE hook ────────────────────────
                post_data = {
                    "phase": phase.value,
                    "attempt": attempt,
                    "gate_name": gate.name,
                    "gate_passed": gate_result.passed,
                    "gate_reason": gate_result.reason,
                }
                post_result = self._fire(
                    HookType.POST_PIPELINE_PHASE, post_data, session_id=session_id
                )
                if post_result is not None:
                    # Hook can override gate_passed
                    if post_result.get("gate_passed") is False:
                        gate_result = GateResult(
                            gate_name=gate.name,
                            passed=False,
                            reason=post_result.get("gate_reason", gate_result.reason),
                        )

                if gate_result.passed:
                    phase_passed = True
                    completed.append(phase)
                    break
                else:
                    if attempt < self.max_retries:
                        # Retry — node will be called again
                        continue
                    else:
                        # Max retries exceeded — abort
                        errors.append(
                            f"Phase {phase.value} failed gate '{gate.name}': {gate_result.reason}"
                        )
                        return PipelineResult(
                            success=False,
                            completed_phases=completed,
                            final_state=state,
                            gate_results=all_gate_results,
                            errors=errors,
                            total_duration_ms=(time.time() - start_time) * 1000,
                            aborted_at=phase,
                        )

            if not phase_passed:
                errors.append(f"Phase {phase.value} did not pass (unexpected)")
                return PipelineResult(
                    success=False,
                    completed_phases=completed,
                    final_state=state,
                    gate_results=all_gate_results,
                    errors=errors,
                    total_duration_ms=(time.time() - start_time) * 1000,
                    aborted_at=phase,
                )

        # ── Fire POST_PIPELINE_END hook ────────────────────────────────────
        end_data = {
            "completed_phases": [p.value for p in completed],
            "total_phases": len(active_phases),
            "duration_ms": (time.time() - start_time) * 1000,
        }
        self._fire(HookType.POST_PIPELINE_END, end_data, session_id=session_id)

        return PipelineResult(
            success=True,
            completed_phases=completed,
            final_state=state,
            gate_results=all_gate_results,
            errors=errors,
            total_duration_ms=(time.time() - start_time) * 1000,
        )

    def run_phase(self, phase: PipelinePhase, state: GraphState) -> tuple[GraphState, GateResult]:
        """Execute a single phase and return the state + gate result.

        Useful for incremental pipeline execution or testing.
        """
        node_fn = self.nodes.get(phase)
        if node_fn:
            state = node_fn(state)

        gate = self.gates.get(phase)
        if gate:
            result = gate.evaluate(state)
        else:
            result = GateResult(gate_name="none", passed=True, reason="no gate")

        return state, result
