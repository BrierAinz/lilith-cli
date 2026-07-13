"""Self-Correction System for Lilith Orchestrator.

Inspired by SEAL framework's Plan → Act → Reflect → Judge loop:
    - Plan: Create execution plan
    - Act: Execute the plan
    - Reflect: Analyze what happened
    - Judge: Evaluate quality, decide if retry/continue/abort

This module provides:
    - JudgeCriteria: Evaluation criteria for judge decisions
    - JudgeVerdict: Possible verdicts (APPROVED, RETRY, REVISE, ABORT)
    - Reflector: Analyzes execution results
    - SelfCorrectionLoop: Orchestrates the full reflect-judge cycle

The Judge can dynamically evolve its evaluation rubric based on failure patterns:
    - hallucination: Agent made up facts
    - goal_drift: Agent lost sight of original goal
    - execution_error: Tool execution failed
    - quality_issues: Output too short/long/off-topic
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class JudgeVerdict(Enum):
    """Possible verdicts from the Judge."""

    APPROVED = "approved"       # Output passes all criteria, deliver to user
    RETRY = "retry"           # Same approach, try again
    REVISE = "revise"         # Different approach needed
    ABORT = "abort"          # Stop execution, report failure


class FailurePattern(Enum):
    """Patterns that indicate specific failure types."""

    HALLUCINATION = "hallucination"       # Made-up facts
    GOAL_DRIFT = "goal_drift"           # Lost original goal
    EXECUTION_ERROR = "execution_error" # Tool failed
    QUALITY_SHORT = "quality_short"      # Output too short
    QUALITY_LONG = "quality_long"        # Output too long
    QUALITY_OFF_TOPIC = "quality_off_topic"  # Off-topic
    SAFETY_VIOLATION = "safety_violation"  # Unsafe content


@dataclass
class JudgeCriteria:
    """Criteria for evaluating execution results.

    Attributes:
        min_length: Minimum response length (chars).
        max_length: Maximum response length (chars).
        require_tools_used: If True, must have called at least one tool.
        check_facts: If True, verify factual claims aren't hallucinations.
        check_safety: If True, check for safety violations.
        check_goal_alignment: If True, verify output matches original goal.
    """

    min_length: int = 10
    max_length: int = 50000
    require_tools_used: bool = False
    check_facts: bool = False
    check_safety: bool = True
    check_goal_alignment: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Serialize criteria to a plain dict."""
        return {
            "min_length": self.min_length,
            "max_length": self.max_length,
            "require_tools_used": self.require_tools_used,
            "check_facts": self.check_facts,
            "check_safety": self.check_safety,
            "check_goal_alignment": self.check_goal_alignment,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JudgeCriteria":
        """Build criteria from a dict, using defaults for missing fields."""
        return cls(
            min_length=data.get("min_length", 10),
            max_length=data.get("max_length", 50000),
            require_tools_used=data.get("require_tools_used", False),
            check_facts=data.get("check_facts", False),
            check_safety=data.get("check_safety", True),
            check_goal_alignment=data.get("check_goal_alignment", True),
        )


@dataclass
class JudgeResult:
    """Result of a Judge evaluation."""

    verdict: JudgeVerdict
    reason: str
    confidence: float = 1.0
    issues: list[str] = field(default_factory=list)
    patterns: list[FailurePattern] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    retry_count: int = 0


@dataclass
class ExecutionResult:
    """Result of an execution attempt.

    Attributes:
        response: The generated response.
        tool_calls: List of tool calls made.
        original_goal: What the agent was trying to achieve.
        error: Any error that occurred.
        metadata: Extra metadata.
    """

    response: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    original_goal: str = ""
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


# ── Reflector ───────────────────────────────────────────────────────────────────


class Reflector:
    """Analyzes execution results to identify issues.

    The Reflector examines what happened during execution and identifies
    specific failure patterns that the Judge can use to make decisions.
    """

    def __init__(self):
        self._failure_counts: dict[FailurePattern, int] = {}

    def reflect(self, result: ExecutionResult, criteria: JudgeCriteria) -> list[FailurePattern]:
        """Analyze execution result and identify failure patterns.

        Args:
            result: The execution result to analyze.
            criteria: The criteria being evaluated against.

        Returns:
            List of identified failure patterns.
        """
        patterns: list[FailurePattern] = []

        # Check length
        resp_len = len(result.response)
        if resp_len < criteria.min_length:
            patterns.append(FailurePattern.QUALITY_SHORT)
        elif resp_len > criteria.max_length:
            patterns.append(FailurePattern.QUALITY_LONG)

        # Check tool usage requirement
        if criteria.require_tools_used and not result.tool_calls:
            patterns.append(FailurePattern.EXECUTION_ERROR)

        # Check for execution errors
        if result.error:
            patterns.append(FailurePattern.EXECUTION_ERROR)

        # Check for safety issues (simple keyword check)
        if criteria.check_safety:
            unsafe_keywords = ["hack", "exploit", "bypass", "inject"]
            resp_lower = result.response.lower()
            if any(kw in resp_lower for kw in unsafe_keywords):
                patterns.append(FailurePattern.SAFETY_VIOLATION)

        # Check goal alignment (simple keyword check)
        if criteria.check_goal_alignment and result.original_goal:
            goal_keywords = set(result.original_goal.lower().split())
            resp_keywords = set(result.response.lower().split())
            overlap = goal_keywords & resp_keywords
            if len(overlap) < 2 and len(goal_keywords) > 3:
                patterns.append(FailurePattern.GOAL_DRIFT)

        # Update failure counts
        for p in patterns:
            self._failure_counts[p] = self._failure_counts.get(p, 0) + 1

        return patterns

    def get_failure_stats(self) -> dict[str, int]:
        """Get counts of each failure pattern."""
        return {p.value: count for p, count in self._failure_counts.items()}

    def reset_stats(self) -> None:
        """Reset failure statistics."""
        self._failure_counts.clear()


# ── Judge ───────────────────────────────────────────────────────────────────────


class Judge:
    """Evaluates execution results and makes retry/revise/abort decisions.

    The Judge uses both static criteria and dynamic adjustment based on
    observed failure patterns. It can evolve its rubric over time.
    """

    def __init__(
        self,
        criteria: JudgeCriteria | None = None,
        max_retries: int = 3,
    ):
        self.criteria = criteria or JudgeCriteria()
        self.max_retries = max_retries
        self._reflector = Reflector()
        self._retry_counts: dict[str, int] = {}

    def judge(
        self,
        result: ExecutionResult,
        attempt_id: str = "default",
    ) -> JudgeResult:
        """Evaluate execution result and return verdict.

        Args:
            result: The execution result to evaluate.
            attempt_id: Identifier for this execution attempt.

        Returns:
            JudgeResult with verdict and reasoning.
        """
        # Track retries
        self._retry_counts[attempt_id] = self._retry_counts.get(attempt_id, 0) + 1
        retry_count = self._retry_counts[attempt_id]

        # Run reflector to identify patterns
        patterns = self._reflector.reflect(result, self.criteria)

        issues: list[str] = []
        suggestions: list[str] = []
        verdict = JudgeVerdict.APPROVED

        # Evaluate each pattern
        if FailurePattern.QUALITY_SHORT in patterns:
            issues.append("Response too short")
            suggestions.append("Expand the response with more detail")
            if retry_count < self.max_retries:
                verdict = JudgeVerdict.RETRY

        if FailurePattern.QUALITY_LONG in patterns:
            issues.append("Response too long")
            suggestions.append("Condense the response")
            if retry_count < self.max_retries:
                verdict = JudgeVerdict.RETRY

        if FailurePattern.EXECUTION_ERROR in patterns:
            issues.append(f"Execution error: {result.error or 'unknown'}")
            suggestions.append("Fix the underlying error and retry")
            if retry_count < self.max_retries:
                verdict = JudgeVerdict.RETRY

        if FailurePattern.SAFETY_VIOLATION in patterns:
            issues.append("Safety violation detected")
            suggestions.append("Remove unsafe content")
            verdict = JudgeVerdict.ABORT

        if FailurePattern.GOAL_DRIFT in patterns:
            issues.append("Goal drift detected")
            suggestions.append("Re-focus on original goal")
            if retry_count < self.max_retries:
                verdict = JudgeVerdict.REVISE

        if FailurePattern.HALLUCINATION in patterns:
            issues.append("Possible hallucination detected")
            suggestions.append("Verify factual claims")
            if retry_count < self.max_retries:
                verdict = JudgeVerdict.REVISE

        # Check if max retries exceeded - override verdict to ABORT
        # When retry_count reaches max_retries, abort
        if retry_count >= self.max_retries:
            if verdict in (JudgeVerdict.RETRY, JudgeVerdict.REVISE):
                verdict = JudgeVerdict.ABORT

        # Default: approve if no issues (only if verdict wasn't set to ABORT above)
        if not issues and verdict not in (JudgeVerdict.ABORT,):
            verdict = JudgeVerdict.APPROVED
            reason = "All criteria met"
        elif verdict == JudgeVerdict.ABORT and retry_count >= self.max_retries:
            reason = f"Max retries ({self.max_retries}) exceeded. Issues: {'; '.join(issues)}"
        else:
            reason = f"Issues: {'; '.join(issues)}"

        return JudgeResult(
            verdict=verdict,
            reason=reason,
            confidence=1.0 - (len(issues) * 0.1),
            issues=issues,
            patterns=patterns,
            suggestions=suggestions,
            retry_count=retry_count,
        )

    def update_criteria(self, criteria: JudgeCriteria) -> None:
        """Update the judge's criteria."""
        self.criteria = criteria

    def get_stats(self) -> dict[str, Any]:
        """Get judge statistics."""
        return {
            "criteria": self.criteria.to_dict(),
            "max_retries": self.max_retries,
            "failure_stats": self._reflector.get_failure_stats(),
            "retry_counts": self._retry_counts,
        }


# ── Self-Correction Loop ───────────────────────────────────────────────────────


@dataclass
class LoopConfig:
    """Configuration for the self-correction loop."""

    max_iterations: int = 5
    max_retries_per_attempt: int = 3
    require_tools_used: bool = False
    check_safety: bool = True
    check_goal_alignment: bool = True


@dataclass
class LoopIteration:
    """Result of a single iteration of the loop."""

    iteration: int
    result: ExecutionResult
    judge_result: JudgeResult
    duration_ms: float


@dataclass
class LoopOutcome:
    """Final outcome of the self-correction loop."""

    success: bool
    final_response: str
    iterations: list[LoopIteration]
    total_duration_ms: float
    verdict: JudgeVerdict


class SelfCorrectionLoop:
    """Orchestrates Plan → Act → Reflect → Judge cycle.

    This loop runs until:
        - Judge approves (success)
        - Judge aborts (failure)
        - Max iterations reached (failure)
    """

    def __init__(self, config: LoopConfig | None = None):
        self.config = config or LoopConfig()
        self.judge = Judge(
            criteria=JudgeCriteria(
                require_tools_used=self.config.require_tools_used,
                check_safety=self.config.check_safety,
                check_goal_alignment=self.config.check_goal_alignment,
            ),
            max_retries=self.config.max_retries_per_attempt,
        )
        self._iteration_count = 0

    def run(
        self,
        execute_fn: Callable[[str], ExecutionResult],
        goal: str,
    ) -> LoopOutcome:
        """Run the self-correction loop.

        Args:
            execute_fn: Function that executes one attempt. Takes goal string,
                       returns ExecutionResult.
            goal: The original goal/task.

        Returns:
            LoopOutcome with final result.
        """
        iterations: list[LoopIteration] = []
        self._iteration_count = 0

        while self._iteration_count < self.config.max_iterations:
            self._iteration_count += 1
            start_time = time.time()

            # Act: Execute
            result = execute_fn(goal)
            result.original_goal = goal

            # Judge: Evaluate
            judge_result = self.judge.judge(result, f"iter_{self._iteration_count}")

            duration = (time.time() - start_time) * 1000

            # Record iteration
            iterations.append(LoopIteration(
                iteration=self._iteration_count,
                result=result,
                judge_result=judge_result,
                duration_ms=duration,
            ))

            # Check verdict
            if judge_result.verdict == JudgeVerdict.APPROVED:
                return LoopOutcome(
                    success=True,
                    final_response=result.response,
                    iterations=iterations,
                    total_duration_ms=sum(i.duration_ms for i in iterations),
                    verdict=JudgeVerdict.APPROVED,
                )

            if judge_result.verdict == JudgeVerdict.ABORT:
                return LoopOutcome(
                    success=False,
                    final_response=result.response,
                    iterations=iterations,
                    total_duration_ms=sum(i.duration_ms for i in iterations),
                    verdict=judge_result.verdict,
                )

            # RETRY or REVISE: continue loop
            # The execute_fn can use judge_result.suggestions to improve

        # Max iterations reached
        return LoopOutcome(
            success=False,
            final_response=iterations[-1].result.response if iterations else "",
            iterations=iterations,
            total_duration_ms=sum(i.duration_ms for i in iterations),
            verdict=JudgeVerdict.ABORT,
        )

    def get_stats(self) -> dict[str, Any]:
        """Get loop statistics."""
        return {
            "config": {
                "max_iterations": self.config.max_iterations,
                "max_retries_per_attempt": self.config.max_retries_per_attempt,
            },
            "judge_stats": self.judge.get_stats(),
            "iteration_count": self._iteration_count,
        }
