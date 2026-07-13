"""YAML-based Workflow Definition Engine for Lilith Orchestrator.

Extends the graph presets system with declarative, user-definable workflows
written in YAML. Inspired by kdeps' YAML pipeline approach and Aether's
5-phase pipeline pattern.

A workflow YAML defines a sequence of steps, each with:
    - name: Step identifier
    - agent: Which agent handles this step (optional — uses dispatch if omitted)
    - intent: Intent hint for agent selection (code, research, creative, debug, chat)
    - tools: Required tools for this step
    - gate: Quality gate definition (condition to pass before next step)
    - parallel: If true, this step runs concurrently with the next step
    - retry: Number of retries on failure (default 0)
    - timeout: Step timeout in seconds (default 60)

Example workflow YAML::

    name: code-review-pipeline
    description: Multi-step code review with quality gates
    version: "1.0"
    steps:
      - name: understand
        intent: research
        description: Analyze the codebase context
        gate:
          type: content_check
          min_length: 50

      - name: review
        intent: code
        description: Perform the code review
        tools: [terminal, file_edit]
        gate:
          type: content_check
          min_length: 100

      - name: suggest
        intent: creative
        description: Generate improvement suggestions

    on_failure: abort  # abort | skip | retry
    max_retries: 2
    timeout: 300

Usage::

    from lilith_orchestrator.workflow import WorkflowEngine

    engine = WorkflowEngine()
    workflow = engine.load_yaml("review.yaml")
    result = engine.run(workflow, context={"input": "Review this PR"})
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .policy import PolicyEngine

logger = logging.getLogger("lilith.orchestrator.workflow")


def _generate_session_id() -> str:
    """Return a stable-but-unique session id for hook context."""
    return f"wf-{uuid.uuid4().hex[:12]}"


# ── Enums ───────────────────────────────────────────────────────────────────


class GateType(Enum):
    """Types of quality gates between workflow steps."""

    NONE = "none"              # Always passes
    CONTENT_CHECK = "content_check"  # Check output meets criteria
    AGENT_REVIEW = "agent_review"    # Another agent reviews the output
    CUSTOM = "custom"          # Custom gate function
    MIN_LENGTH = "min_length"  # Enforce minimum content length (chars/tokens)
    KEYWORD_PRESENCE = "keyword_presence"  # Require required keywords be present
    REGEX_MATCH = "regex_match"  # Require output match a regex pattern
    JSON_PARSE = "json_parse"  # Output must parse as JSON


class OnFailure(Enum):
    """What to do when a step fails."""

    ABORT = "abort"     # Stop the entire workflow
    SKIP = "skip"       # Skip this step, continue to next
    RETRY = "retry"     # Retry the step up to max_retries


class StepStatus(Enum):
    """Status of a workflow step execution."""

    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    GATED = "gated"     # Blocked by quality gate


class WorkflowStatus(Enum):
    """Overall workflow execution status."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


# ── Data Models ─────────────────────────────────────────────────────────────


@dataclass
class QualityGate:
    """A quality gate that must pass before proceeding to the next step.

    Attributes:
        type: The gate type (none, content_check, agent_review, custom).
        min_length: Minimum content length for content_check gates.
        required_keywords: Keywords that must appear in the output.
        forbidden_keywords: Keywords that must NOT appear in the output.
        reviewer_agent: Agent name for agent_review gates.
        custom_check: Name of a registered custom gate function.
        description: Human-readable description of the gate.
    """

    type: GateType = GateType.NONE
    min_length: int = 0
    required_keywords: list[str] = field(default_factory=list)
    forbidden_keywords: list[str] = field(default_factory=list)
    reviewer_agent: str = ""
    custom_check: str = ""
    description: str = ""

    def evaluate(self, content: str) -> tuple[bool, str]:
        """Evaluate this gate against content.

        Args:
            content: The step output to evaluate.

        Returns:
            Tuple of (passed, reason). If passed is True, reason is empty.
        """
        if self.type == GateType.NONE:
            return True, ""

        if self.type == GateType.MIN_LENGTH:
            if len(content) < self.min_length:
                return False, f"Content too short: {len(content)} < {self.min_length}"
            return True, ""

        if self.type == GateType.KEYWORD_PRESENCE:
            for kw in self.required_keywords:
                if kw.lower() not in content.lower():
                    return False, f"Missing required keyword: {kw}"
            for kw in self.forbidden_keywords:
                if kw.lower() in content.lower():
                    return False, f"Forbidden keyword found: {kw}"
            return True, ""

        if self.type == GateType.REGEX_MATCH:
            import re
            pattern = self.custom_check
            if not pattern:
                return False, "regex_match gate missing pattern (set custom_check)"
            try:
                if not re.search(pattern, content):
                    return False, f"Output did not match regex: {pattern}"
            except re.error as exc:
                return False, f"Invalid regex: {exc}"
            return True, ""

        if self.type == GateType.JSON_PARSE:
            import json as _json
            try:
                _json.loads(content)
            except (ValueError, TypeError) as exc:
                return False, f"Output is not valid JSON: {exc}"
            return True, ""

        if self.type == GateType.CONTENT_CHECK:
            if self.min_length and len(content) < self.min_length:
                return False, f"Content too short: {len(content)} < {self.min_length}"
            for kw in self.required_keywords:
                if kw.lower() not in content.lower():
                    return False, f"Missing required keyword: {kw}"
            for kw in self.forbidden_keywords:
                if kw.lower() in content.lower():
                    return False, f"Forbidden keyword found: {kw}"
            return True, ""

        if self.type == GateType.AGENT_REVIEW:
            # Agent review gates are evaluated externally
            # Return True here; the engine handles the actual review
            return True, ""

        if self.type == GateType.CUSTOM:
            # Custom gates are evaluated externally
            return True, ""

        return True, ""

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> QualityGate:
        """Create a QualityGate from a YAML-parsed dict."""
        if not data:
            return cls()
        return cls(
            type=GateType(data.get("type", "none")),
            min_length=data.get("min_length", 0),
            required_keywords=data.get("required_keywords", []),
            forbidden_keywords=data.get("forbidden_keywords", []),
            reviewer_agent=data.get("reviewer_agent", ""),
            custom_check=data.get("custom_check", ""),
            description=data.get("description", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dict suitable for YAML dumping (inverse of from_dict)."""
        out: dict[str, Any] = {"type": self.type.value}
        # min_length is meaningful for both CONTENT_CHECK and MIN_LENGTH.
        if self.min_length and self.type in (GateType.CONTENT_CHECK, GateType.MIN_LENGTH):
            out["min_length"] = self.min_length
        if self.required_keywords:
            out["required_keywords"] = list(self.required_keywords)
        if self.forbidden_keywords:
            out["forbidden_keywords"] = list(self.forbidden_keywords)
        if self.reviewer_agent:
            out["reviewer_agent"] = self.reviewer_agent
        if self.custom_check:
            out["custom_check"] = self.custom_check
        if self.description:
            out["description"] = self.description
        return out


@dataclass
class WorkflowStep:
    """A single step in a workflow.

    Attributes:
        name: Unique step identifier.
        agent: Specific agent to use (optional — uses dispatch if omitted).
        intent: Intent hint for agent selection.
        description: Human-readable step description.
        tools: Required tools for this step.
        gate: Quality gate to evaluate after step completes.
        parallel: Whether this step can run in parallel with the next.
        retry: Number of retries on failure.
        timeout: Step timeout in seconds.
        input_key: Key in context to use as step input (default: previous output).
        output_key: Key in context to store step output.
        subagent_type: Optional key into the SubAgentRegistry. When set,
            the engine routes step execution through ``SubAgentRunner``
            (if attached) instead of the default executor. The runner
            receives ``step.description`` as the task prompt and returns
            the sub-agent's ``output`` as the step output. Tools are
            filtered by the matched :class:`SubAgentDefinition`.
        subagent_depth: Parent depth passed to ``SubAgentRunner.make_spawn_fn``.
            Defaults to ``0`` (top-level workflow = no nesting). Increase
            when launching a workflow from inside a sub-agent.
    """

    name: str
    agent: str = ""
    intent: str = "chat"
    description: str = ""
    tools: list[str] = field(default_factory=list)
    gate: QualityGate = field(default_factory=QualityGate)
    parallel: bool = False
    retry: int = 0
    timeout: int = 60
    input_key: str = ""
    output_key: str = ""
    subagent_type: str = ""
    subagent_depth: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowStep:
        """Create a WorkflowStep from a YAML-parsed dict."""
        return cls(
            name=data["name"],
            agent=data.get("agent", ""),
            intent=data.get("intent", "chat"),
            description=data.get("description", ""),
            tools=data.get("tools", []),
            gate=QualityGate.from_dict(data.get("gate")),
            parallel=data.get("parallel", False),
            retry=data.get("retry", 0),
            timeout=data.get("timeout", 60),
            input_key=data.get("input_key", ""),
            output_key=data.get("output_key", ""),
            subagent_type=data.get("subagent_type", ""),
            subagent_depth=int(data.get("subagent_depth", 0) or 0),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dict suitable for YAML dumping (inverse of from_dict)."""
        out: dict[str, Any] = {
            "name": self.name,
            "agent": self.agent,
            "intent": self.intent,
            "description": self.description,
            "tools": list(self.tools),
            "parallel": self.parallel,
            "retry": self.retry,
            "timeout": self.timeout,
        }
        if self.input_key:
            out["input_key"] = self.input_key
        if self.output_key:
            out["output_key"] = self.output_key
        if self.subagent_type:
            out["subagent_type"] = self.subagent_type
        if self.subagent_depth:
            out["subagent_depth"] = self.subagent_depth
        if self.gate and self.gate.type != GateType.NONE:
            out["gate"] = self.gate.to_dict()
        return out


@dataclass
class WorkflowDefinition:
    """A complete workflow definition parsed from YAML.

    Attributes:
        name: Workflow identifier.
        description: Human-readable description.
        version: Workflow version string.
        steps: Ordered list of workflow steps.
        on_failure: What to do when a step fails.
        max_retries: Global max retries per step.
        timeout: Global workflow timeout in seconds.
        variables: Workflow-level variables available to all steps.
        metadata: Extra metadata.
    """

    name: str
    description: str = ""
    version: str = "1.0"
    steps: list[WorkflowStep] = field(default_factory=list)
    on_failure: OnFailure = OnFailure.ABORT
    max_retries: int = 2
    timeout: int = 300
    variables: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowDefinition:
        """Create a WorkflowDefinition from a YAML-parsed dict."""
        steps = [WorkflowStep.from_dict(s) for s in data.get("steps", [])]
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            version=data.get("version", "1.0"),
            steps=steps,
            on_failure=OnFailure(data.get("on_failure", "abort")),
            max_retries=data.get("max_retries", 2),
            timeout=data.get("timeout", 300),
            variables=data.get("variables", {}),
            metadata=data.get("metadata", {}),
        )

    def validate(self) -> list[str]:
        """Validate the workflow definition.

        Returns:
            List of validation errors (empty if valid).
        """
        errors: list[str] = []

        if not self.name:
            errors.append("Workflow name is required")
        if not self.steps:
            errors.append("Workflow must have at least one step")

        step_names = set()
        for i, step in enumerate(self.steps):
            if not step.name:
                errors.append(f"Step {i} is missing a name")
            if step.name in step_names:
                errors.append(f"Duplicate step name: {step.name}")
            step_names.add(step.name)
            if step.retry < 0:
                errors.append(f"Step '{step.name}' has negative retry count")
            if step.timeout <= 0:
                errors.append(f"Step '{step.name}' has invalid timeout")
            if step.gate.type == GateType.AGENT_REVIEW and not step.gate.reviewer_agent:
                errors.append(f"Step '{step.name}' has agent_review gate but no reviewer_agent")

        return errors

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dict suitable for YAML dumping.

        Inverse of :meth:`from_dict`. Used by the workflow-package loader
        to persist a workflow graph back to disk.
        """
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "steps": [s.to_dict() for s in self.steps],
            "on_failure": self.on_failure.value,
            "max_retries": self.max_retries,
            "timeout": self.timeout,
            "variables": dict(self.variables),
            "metadata": dict(self.metadata),
        }


# ── Step Execution Result ───────────────────────────────────────────────────


@dataclass
class StepResult:
    """Result of executing a single workflow step.

    Attributes:
        step_name: The step that was executed.
        status: Execution status.
        output: The step's output content.
        agent_used: Which agent handled the step.
        gate_passed: Whether the quality gate passed.
        gate_reason: Why the gate failed (empty if passed).
        duration_ms: Execution time in milliseconds.
        retries: Number of retries used.
        error: Error message if failed.
        metadata: Extra result metadata.
    """

    step_name: str
    status: StepStatus = StepStatus.PENDING
    output: str = ""
    agent_used: str = ""
    gate_passed: bool = True
    gate_reason: str = ""
    duration_ms: float = 0.0
    retries: int = 0
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {
            "step_name": self.step_name,
            "status": self.status.value,
            "output": self.output[:500],  # Truncate for serialization
            "agent_used": self.agent_used,
            "gate_passed": self.gate_passed,
            "gate_reason": self.gate_reason,
            "duration_ms": round(self.duration_ms, 2),
            "retries": self.retries,
            "error": self.error,
        }


@dataclass
class WorkflowResult:
    """Result of executing a complete workflow.

    Attributes:
        workflow_name: The workflow that was executed.
        status: Overall execution status.
        steps: Results for each step.
        total_duration_ms: Total execution time.
        context: Final context state after execution.
    """

    workflow_name: str
    status: WorkflowStatus = WorkflowStatus.PENDING
    steps: list[StepResult] = field(default_factory=list)
    total_duration_ms: float = 0.0
    context: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        """Whether the workflow completed successfully."""
        return self.status == WorkflowStatus.COMPLETED

    @property
    def final_output(self) -> str:
        """The output of the last completed step."""
        for step in reversed(self.steps):
            if step.output:
                return step.output
        return ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {
            "workflow_name": self.workflow_name,
            "status": self.status.value,
            "steps": [s.to_dict() for s in self.steps],
            "total_duration_ms": round(self.total_duration_ms, 2),
            "success": self.success,
            "final_output": self.final_output[:500],
        }


# ── Workflow Engine ─────────────────────────────────────────────────────────


# Type alias for step executor functions
StepExecutor = Any  # Callable[[WorkflowStep, dict[str, Any]], tuple[str, str]]


class WorkflowEngine:
    """Executes workflow definitions step by step.

    The engine:
        1. Validates the workflow definition.
        2. Executes each step sequentially (or in parallel if marked).
        3. Evaluates quality gates after each step.
        4. Handles failures according to on_failure policy.
        5. Returns a WorkflowResult with all step results.

    Custom step executors can be registered for specific intents or agents.
    The default executor is a placeholder that echoes the step description.

    Usage::

        engine = WorkflowEngine()
        workflow = engine.parse_yaml(yaml_string)
        result = engine.run(workflow, context={"input": "Analyze this code"})
    """

    def __init__(
        self,
        policy_engine: "PolicyEngine | None" = None,
        hook_registry: Any = None,
        session_id: str | None = None,
        agent_name: str = "workflow_engine",
        subagent_runner: Any = None,
        full_tool_pool: list[str] | None = None,
    ) -> None:
        """Initialize the workflow engine.

        Args:
            policy_engine: Optional PolicyEngine for tool-call policy enforcement.
                If provided, every step's agent+intent is checked before execution.
            hook_registry: Optional :class:`lilith_core.hooks.HookRegistry` to
                fire lifecycle hooks during workflow execution. If ``None``,
                a fresh private registry is created (so the engine never
                raises on missing hook plumbing). Pass a shared registry
                to wire workflow hooks into the wider agent ecosystem.
            session_id: Stable session id used in ``HookContext.session_id``.
                Defaults to a random uuid4 string.
            agent_name: Agent name stamped on fired ``HookContext`` objects
                (default ``"workflow_engine"``).
            subagent_runner: Optional :class:`SubAgentRunner` used to
                execute steps that declare ``subagent_type``. When ``None``
                and a step requests a sub-agent, the engine falls back to
                the default executor and the sub-agent filter is logged.
            full_tool_pool: Names of every tool the runner can hand to a
                sub-agent (defaults to a sensible built-in list of the
                common Yggdrasil tool names). The runner intersects this
                pool with each sub-agent's ``allowed_tools``.
        """
        self._custom_executors: dict[str, StepExecutor] = {}
        self._custom_gates: dict[str, Any] = {}
        self.policy_engine: "PolicyEngine | None" = policy_engine
        # Hook integration. We always have a registry so firing is safe
        # even if no external plugin ever subscribes.
        if hook_registry is None:
            try:
                from lilith_core.hooks import HookRegistry as _DefaultHookRegistry

                self.hook_registry: Any = _DefaultHookRegistry()
            except Exception:  # pragma: no cover — defensive
                self.hook_registry = None
        else:
            self.hook_registry = hook_registry
        self.session_id: str = session_id or _generate_session_id()
        self.agent_name: str = agent_name
        self.subagent_runner = subagent_runner
        # Default tool pool: covers the common Yggdrasil / Hermes tool
        # names. Products can override by passing full_tool_pool=... at
        # construction time (useful when you have a custom tool registry).
        self.full_tool_pool: list[str] = list(
            full_tool_pool
            if full_tool_pool is not None
            else [
                "read_file",
                "search_files",
                "write_file",
                "patch",
                "terminal",
                "web_search",
                "web_fetch",
                "browser",
                "delegate_task",
            ]
        )

    def attach_policy(self, policy_engine: "PolicyEngine") -> None:
        """Attach or replace the policy engine after construction."""
        self.policy_engine = policy_engine

    # ── Hook integration ────────────────────────────────────────────────────

    def _fire_hook(self, hook_type: Any, data: dict[str, Any] | None = None) -> Any:
        """Fire a hook on the configured registry. Never raises.

        If no registry is attached (or ``lilith_core`` is unavailable),
        the call is a no-op. This keeps the engine's hot path safe even
        for users who never opt into the hook system.
        """
        if self.hook_registry is None:
            return None
        try:
            from lilith_core.hooks import HookContext as _Ctx

            ctx = _Ctx(
                hook_type=hook_type,
                agent_name=self.agent_name,
                session_id=self.session_id,
                data=dict(data or {}),
            )
            return self.hook_registry.fire(ctx)
        except Exception as exc:  # pragma: no cover — defensive
            logger.debug("Hook fire failed (non-fatal): %s", exc)
            return None

    def register_executor(self, name: str, executor: StepExecutor) -> None:
        """Register a custom step executor for a named intent or agent.

        Args:
            name: Intent name or agent name.
            executor: Callable that takes (step, context) and returns (output, agent_name).
        """
        self._custom_executors[name] = executor

    def register_gate(self, name: str, gate_fn: Any) -> None:
        """Register a custom gate function.

        Args:
            name: Gate function name (referenced in workflow YAML as custom_check).
            gate_fn: Callable that takes (content, gate_config) and returns (bool, str).
        """
        self._custom_gates[name] = gate_fn

    # ── YAML Parsing ────────────────────────────────────────────────────────

    def parse_yaml(self, yaml_string: str) -> WorkflowDefinition:
        """Parse a YAML string into a WorkflowDefinition.

        Args:
            yaml_string: YAML content of the workflow definition.

        Returns:
            A parsed and validated WorkflowDefinition.

        Raises:
            ValueError: If the YAML is invalid or validation fails.
        """
        try:
            import yaml
        except ImportError:
            # Minimal YAML parser for simple workflow definitions
            data = self._parse_yaml_minimal(yaml_string)
        else:
            data = yaml.safe_load(yaml_string)

        if not isinstance(data, dict):
            raise ValueError("Workflow YAML must be a mapping")

        workflow = WorkflowDefinition.from_dict(data)
        errors = workflow.validate()
        if errors:
            raise ValueError(f"Workflow validation failed: {'; '.join(errors)}")

        return workflow

    def _parse_yaml_minimal(self, text: str) -> dict[str, Any]:
        """Minimal YAML parser for workflow definitions (no PyYAML dependency).

        Handles simple key-value pairs, lists of mappings, and nested dicts.
        Not a full YAML implementation — sufficient for workflow definitions.
        """
        import json

        # Try JSON first (YAML is a superset of JSON)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Fallback: parse simple YAML-like structure
        result: dict[str, Any] = {}
        current_list: list[dict[str, Any]] | None = None
        current_list_key: str | None = None
        current_dict: dict[str, Any] | None = None

        for line in text.strip().split("\n"):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            # Count indentation
            indent = len(line) - len(line.lstrip())

            if stripped.startswith("- ") and indent > 0:
                # List item
                item_text = stripped[2:].strip()
                if ":" in item_text:
                    if current_list is None:
                        current_list = []
                    current_dict = {}
                    key, _, val = item_text.partition(":")
                    current_dict[key.strip()] = self._yaml_value(val.strip())
                    current_list.append(current_dict)
                continue

            if current_dict is not None and indent > 2 and ":" in stripped:
                # Nested dict item
                key, _, val = stripped.partition(":")
                current_dict[key.strip()] = self._yaml_value(val.strip())
                continue

            if current_list is not None and current_list_key:
                result[current_list_key] = current_list
                current_list = None
                current_list_key = None
                current_dict = None

            if ":" in stripped:
                key, _, val = stripped.partition(":")
                key = key.strip()
                val = val.strip()
                if not val:
                    # Next lines will be a list or dict
                    current_list_key = key
                    current_list = None
                    current_dict = None
                else:
                    result[key] = self._yaml_value(val)

        if current_list is not None and current_list_key:
            result[current_list_key] = current_list

        return result

    @staticmethod
    def _yaml_value(val: str) -> Any:
        """Convert a YAML value string to a Python value."""
        if not val:
            return ""
        if val.startswith("[") and val.endswith("]"):
            # Inline list
            import json
            try:
                return json.loads(val)
            except json.JSONDecodeError:
                return [v.strip().strip("'\"") for v in val[1:-1].split(",")]
        if val.lower() in ("true", "yes"):
            return True
        if val.lower() in ("false", "no"):
            return False
        if val.isdigit():
            return int(val)
        try:
            return float(val)
        except ValueError:
            return val.strip("'\"")

    def load_yaml(self, path: str) -> WorkflowDefinition:
        """Load and parse a workflow from a YAML file.

        Args:
            path: Path to the YAML file.

        Returns:
            A parsed and validated WorkflowDefinition.
        """
        from pathlib import Path

        content = Path(path).read_text(encoding="utf-8")
        return self.parse_yaml(content)

    # ── Execution ───────────────────────────────────────────────────────────

    def run(
        self,
        workflow: WorkflowDefinition,
        context: dict[str, Any] | None = None,
    ) -> WorkflowResult:
        """Execute a workflow definition.

        Args:
            workflow: The parsed workflow definition.
            context: Initial context (available to all steps).

        Returns:
            A WorkflowResult with all step results and final context.
        """
        from lilith_core.hooks import HookType as _HT

        ctx = dict(context or {})
        result = WorkflowResult(workflow_name=workflow.name)
        result.status = WorkflowStatus.RUNNING
        start = time.time()

        # Fire workflow-level start hook
        self._fire_hook(
            _HT.ON_SESSION_START,
            {
                "event": "workflow_start",
                "workflow": workflow.name,
                "step_count": len(workflow.steps),
            },
        )

        for step in workflow.steps:
            step_result = self._execute_step(step, ctx, workflow)
            result.steps.append(step_result)

            # Update context with step output
            output_key = step.output_key or step.name
            ctx[f"step_{output_key}_output"] = step_result.output
            ctx["last_output"] = step_result.output
            ctx["last_step"] = step.name

            # Handle failures
            if step_result.status == StepStatus.FAILED:
                self._fire_hook(
                    _HT.ON_ERROR,
                    {
                        "event": "step_failed",
                        "step": step.name,
                        "error": step_result.error,
                    },
                )
                if workflow.on_failure == OnFailure.ABORT:
                    result.status = WorkflowStatus.ABORTED
                    result.total_duration_ms = (time.time() - start) * 1000
                    result.context = ctx
                    self._fire_hook(
                        _HT.ON_SESSION_END,
                        {
                            "event": "workflow_aborted",
                            "workflow": workflow.name,
                            "status": "aborted",
                        },
                    )
                    return result
                elif workflow.on_failure == OnFailure.SKIP:
                    continue

            # Handle gate failures
            if step_result.status == StepStatus.GATED:
                self._fire_hook(
                    _HT.ON_ERROR,
                    {
                        "event": "step_gated",
                        "step": step.name,
                        "reason": step_result.gate_reason,
                    },
                )
                if workflow.on_failure == OnFailure.ABORT:
                    result.status = WorkflowStatus.ABORTED
                    result.total_duration_ms = (time.time() - start) * 1000
                    result.context = ctx
                    self._fire_hook(
                        _HT.ON_SESSION_END,
                        {
                            "event": "workflow_aborted",
                            "workflow": workflow.name,
                            "status": "gated",
                        },
                    )
                    return result

        result.status = WorkflowStatus.COMPLETED
        result.total_duration_ms = (time.time() - start) * 1000
        result.context = ctx
        # Fire workflow-level end hook
        self._fire_hook(
            _HT.ON_SESSION_END,
            {
                "event": "workflow_completed",
                "workflow": workflow.name,
                "status": "completed",
                "step_count": len(result.steps),
            },
        )
        return result

    def _execute_step(
        self,
        step: WorkflowStep,
        context: dict[str, Any],
        workflow: WorkflowDefinition,
    ) -> StepResult:
        """Execute a single workflow step with retry logic.

        Args:
            step: The step to execute.
            context: Current workflow context.
            workflow: Parent workflow (for global settings).

        Returns:
            A StepResult with execution details.
        """
        start = time.time()
        max_attempts = step.retry + 1

        for attempt in range(max_attempts):
            try:
                # Get step input
                input_content = context.get("last_output", "")
                if step.input_key:
                    input_content = context.get(step.input_key, input_content)

                # ── Policy check ──────────────────────────────────────────
                # Check with policy engine BEFORE invoking executor.
                # The agent_name is step.agent or auto(intent). The tool_name
                # is the intent (research/code/debug/creative). Paths come
                # from context if the step references any.
                if self.policy_engine is not None:
                    agent_name = step.agent or f"auto({step.intent})"
                    tool_name = step.intent or step.name
                    step_path = (
                        context.get("path")
                        or context.get("file_path")
                        or context.get("step_path")
                    )
                    decision, violation, detail = self.policy_engine.check_tool(
                        agent_name, tool_name, path=step_path
                    )
                    if decision.value == "deny":
                        logger.warning(
                            f"Policy denied step '{step.name}' "
                            f"(agent={agent_name}, tool={tool_name}): {detail}"
                        )
                        return StepResult(
                            step_name=step.name,
                            status=StepStatus.FAILED,
                            error=f"POLICY DENY: {detail}",
                            agent_used=agent_name,
                            duration_ms=(time.time() - start) * 1000,
                            retries=attempt,
                        )

                # Build step prompt
                prompt = self._build_step_prompt(step, input_content, context)

                # ── pre_tool_call hook ─────────────────────────────────
                # Plugins can rewrite the prompt, abort, or attach metadata.
                # If a hook returns a context whose data["prompt"] is set,
                # we use that as the effective prompt for the executor.
                from lilith_core.hooks import HookType as _HT_S

                pre = self._fire_hook(
                    _HT_S.PRE_TOOL_CALL,
                    {
                        "event": "step_start",
                        "step": step.name,
                        "agent": step.agent or f"auto({step.intent})",
                        "intent": step.intent,
                        "prompt": prompt,
                        "attempt": attempt,
                    },
                )
                if pre is None and self.hook_registry is not None:
                    # Hook returned None → abort signal
                    return StepResult(
                        step_name=step.name,
                        status=StepStatus.FAILED,
                        error="aborted by pre_tool_call hook",
                        duration_ms=(time.time() - start) * 1000,
                        retries=attempt,
                    )
                if pre is not None and isinstance(pre.data, dict):
                    rewritten = pre.data.get("prompt")
                    if isinstance(rewritten, str) and rewritten:
                        prompt = rewritten
                # Stash the effective prompt on the workflow context so
                # custom executors (which only receive step+context) can
                # observe a hook rewrite via context["effective_prompt"].
                context["effective_prompt"] = prompt

                # Execute via registered executor or default
                output, agent_used = self._dispatch_step(step, prompt, context)

                # ── post_tool_call hook ────────────────────────────────
                self._fire_hook(
                    _HT_S.POST_TOOL_CALL,
                    {
                        "event": "step_done",
                        "step": step.name,
                        "agent": agent_used,
                        "intent": step.intent,
                        "output": output,
                        "attempt": attempt,
                    },
                )

                # Evaluate quality gate
                gate_passed, gate_reason = step.gate.evaluate(output)

                if not gate_passed:
                    if attempt < max_attempts - 1:
                        continue  # Retry on gate failure
                    return StepResult(
                        step_name=step.name,
                        status=StepStatus.GATED,
                        output=output,
                        agent_used=agent_used,
                        gate_passed=False,
                        gate_reason=gate_reason,
                        duration_ms=(time.time() - start) * 1000,
                        retries=attempt,
                    )

                return StepResult(
                    step_name=step.name,
                    status=StepStatus.PASSED,
                    output=output,
                    agent_used=agent_used,
                    gate_passed=True,
                    duration_ms=(time.time() - start) * 1000,
                    retries=attempt,
                )

            except Exception as e:
                if attempt < max_attempts - 1:
                    logger.warning(f"Step '{step.name}' failed (attempt {attempt + 1}): {e}")
                    continue
                return StepResult(
                    step_name=step.name,
                    status=StepStatus.FAILED,
                    error=str(e),
                    duration_ms=(time.time() - start) * 1000,
                    retries=attempt,
                )

        # Should not reach here, but safety fallback
        return StepResult(
            step_name=step.name,
            status=StepStatus.FAILED,
            error="Max retries exceeded",
            duration_ms=(time.time() - start) * 1000,
        )

    def _build_step_prompt(
        self,
        step: WorkflowStep,
        input_content: str,
        context: dict[str, Any],
    ) -> str:
        """Build the prompt for a step execution.

        Args:
            step: The step definition.
            input_content: The input content for this step.
            context: Full workflow context.

        Returns:
            The prompt string.
        """
        parts = []
        if step.description:
            parts.append(f"Task: {step.description}")
        if input_content:
            parts.append(f"Input: {input_content}")

        # Add workflow variables
        variables = {k: v for k, v in context.items() if not k.startswith("step_")}
        if variables:
            var_str = ", ".join(f"{k}={v}" for k, v in variables.items() if k != "last_output" and k != "last_step")
            if var_str:
                parts.append(f"Context: {var_str}")

        return "\n\n".join(parts) if parts else step.name

    def _dispatch_step(
        self,
        step: WorkflowStep,
        prompt: str,
        context: dict[str, Any],
    ) -> tuple[str, str]:
        """Dispatch a step to the appropriate executor.

        Args:
            step: The step definition.
            prompt: The built prompt.
            context: Current workflow context.

        Returns:
            Tuple of (output, agent_name).

        Routing priority:

        1. If ``step.subagent_type`` is set and a runner is attached →
           run via :class:`SubAgentRunner` (synchronous wrapper around
           the runner's async spawn). Tools are filtered by the matched
           :class:`SubAgentDefinition`.
        2. If a custom executor is registered for ``step.agent`` or
           ``step.intent`` → use it.
        3. Default: emit a placeholder string. Products wire a real
           executor into the engine at construction time.
        """
        # ── Sub-agent routing ────────────────────────────────────────
        # When the step declares a subagent_type, we route through the
        # SubAgentRunner. The runner is async; we run it inline via
        # asyncio.run so the synchronous WorkflowEngine.run() contract
        # is preserved. If no runner is attached, fall through to the
        # default executor (with a debug log).
        if step.subagent_type:
            if self.subagent_runner is None:
                logger.debug(
                    "step '%s' requested subagent_type='%s' but no "
                    "SubAgentRunner attached; falling back to default "
                    "executor.",
                    step.name,
                    step.subagent_type,
                )
            else:
                return self._dispatch_subagent_step(step, prompt, context)

        # Check for custom executor by agent name
        if step.agent and step.agent in self._custom_executors:
            return self._custom_executors[step.agent](step, context)

        # Check for custom executor by intent
        if step.intent in self._custom_executors:
            return self._custom_executors[step.intent](step, context)

        # Default executor: echo placeholder
        # In production, this would dispatch to the actual orchestrator
        output = f"[Workflow '{step.name}'] Intent: {step.intent}"
        if step.description:
            output += f" — {step.description}"
        agent = step.agent or f"auto({step.intent})"
        return output, agent

    def _dispatch_subagent_step(
        self,
        step: WorkflowStep,
        prompt: str,
        context: dict[str, Any],
    ) -> tuple[str, str]:
        """Execute a sub-agent step via the attached SubAgentRunner.

        The runner returns a :class:`SubAgentResult`; we forward
        ``result.output`` as the step's output and
        ``f"subagent:{step.subagent_type}"`` as the agent name so the
        workflow trace + span correctly attributes the work.

        The spawn is synchronous (we wrap the async ``spawn_fn`` in
        ``asyncio.run``). This is fine for the common case; products
        that need pure-async dispatch should call
        :meth:`SubAgentRunner.make_spawn_fn` directly from their own
        async driver.
        """
        import asyncio

        from lilith_orchestrator.subagents import (
            SubAgentDefinition,
            get_agent as get_subagent_def,
        )

        # Validate the subagent type is registered, so we fail fast with
        # a useful message instead of letting the runner time out.
        defn: SubAgentDefinition | None = get_subagent_def(step.subagent_type)
        if defn is None:
            raise ValueError(
                f"step '{step.name}' references unknown "
                f"subagent_type={step.subagent_type!r}. Register it via "
                f"lilith_orchestrator.subagents.register(...) first."
            )

        spawn_fn = self.subagent_runner.make_spawn_fn(
            parent_depth=step.subagent_depth,
        )
        # The sub-agent receives the step's raw description as its task.
        # We intentionally do NOT use the engine's formatted ``prompt``
        # because that already prefixes "Task: ..." for default-model
        # executors. Sub-agents get the clean description they expect.
        task_prompt = step.description or step.name
        result = asyncio.run(spawn_fn(step.subagent_type, task_prompt))

        # Stash the full result on the context for downstream inspection
        context["last_subagent_result"] = result

        if not result.success:
            # Surface the failure but don't crash the workflow — the
            # gate + retry machinery will decide what to do next.
            return (
                f"[subagent:{step.subagent_type} FAILED] {result.error}",
                f"subagent:{step.subagent_type}",
            )
        return (
            result.output,
            f"subagent:{step.subagent_type}",
        )


# ── Convenience ─────────────────────────────────────────────────────────────


def load_workflow(yaml_string: str) -> WorkflowDefinition:
    """Parse a workflow YAML string. Convenience wrapper around WorkflowEngine.parse_yaml."""
    engine = WorkflowEngine()
    return engine.parse_yaml(yaml_string)


def load_workflow_file(path: str) -> WorkflowDefinition:
    """Load a workflow from a YAML file path."""
    engine = WorkflowEngine()
    return engine.load_yaml(path)
