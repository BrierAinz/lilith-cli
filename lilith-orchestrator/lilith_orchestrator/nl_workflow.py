"""Plain-English Workflow Builder for Lilith Orchestrator.

Converts natural language workflow descriptions into structured
WorkflowDefinition objects (and YAML) that the existing WorkflowEngine can run.

Inspired by Neurosurfer's plain-English pipeline builder and Aether-Agents'
5-phase pipeline pattern. Users describe what they want in English; the
builder infers steps, intents, tools, gates, and flow.

Usage::

    from lilith_orchestrator.nl_workflow import NLWorkflowBuilder

    builder = NLWorkflowBuilder()
    workflow = builder.build("First research the topic, then design the architecture, "
                             "then write the code, and finally run tests")
    # Returns a WorkflowDefinition with 4 steps

    yaml_text = builder.to_yaml("Research X, design Y, code Z")
    # Returns valid YAML string

Patterns detected:
    - Step sequences: "first ... then ... finally ..."
    - Intent keywords: research, design, code, test, review, audit, deploy, etc.
    - Tool mentions: "search the web", "read files", "run tests", "create a PR"
    - Gate hints: "make sure", "verify that", "ensure", "validate"
    - Parallel hints: "at the same time", "simultaneously", "in parallel"
    - Agent hints: "use odin for", "let mimir handle", "delegate to adan"
"""

from __future__ import annotations

import re
import yaml
import logging
from dataclasses import dataclass, field
from typing import Any

from lilith_orchestrator.workflow import (
    GateType,
    OnFailure,
    QualityGate,
    WorkflowDefinition,
    WorkflowStep,
)

logger = logging.getLogger("lilith.orchestrator.nl_workflow")


# ── Intent Detection ─────────────────────────────────────────────────────────

# Maps keywords to intent strings (matching WorkflowStep.intent)
_INTENT_KEYWORDS: dict[str, list[str]] = {
    "research": [
        "research", "investigate", "search", "find", "look up", "discover",
        "analyze", "study", "explore", "gather", "collect", "review literature",
    ],
    "design": [
        "design", "architect", "plan", "sketch", "outline", "draft",
        "blueprint", "schema", "structure", "model",
    ],
    "code": [
        "code", "implement", "build", "create", "write", "develop",
        "program", "scaffold", "generate", "construct", "make",
    ],
    "test": [
        "test", "verify", "validate", "check", "assert", "confirm",
        "run tests", "unit test", "integration test", "e2e",
    ],
    "debug": [
        "debug", "fix", "troubleshoot", "diagnose", "repair", "resolve",
        "investigate bug", "error", "crash", "issue",
    ],
    "review": [
        "review", "audit", "inspect", "evaluate", "assess", "critique",
        "code review", "peer review", "feedback",
    ],
    "deploy": [
        "deploy", "release", "publish", "ship", "push", "merge",
        "CI/CD", "pipeline", "production",
    ],
    "creative": [
        "write", "compose", "draft", "author", "story", "poem",
        "creative", "brainstorm", "ideate", "imagine",
    ],
    "chat": [
        "chat", "talk", "discuss", "conversation", "ask", "question",
    ],
}

# Maps tool keywords to tool names (checked with word boundaries)
_TOOL_KEYWORDS: dict[str, list[str]] = {
    "terminal": [
        "run command", "execute", "shell", "terminal", "bash", "CLI",
        "run tests", "npm", "pip", "git", "docker", "pytest", "test suite",
        "run", "command",
    ],
    "read_file": [
        "read file", "read files", "open file", "load file", "cat",
        "view file", "inspect file", "read the", "read a",
    ],
    "write_file": [
        "write file", "create file", "save file", "write to", "output file",
        "create a new file", "new file",
    ],
    "search_files": [
        "search", "find files", "grep", "search for", "locate",
    ],
    "web_search": [
        "web search", "search online", "google", "look up online",
        "search the web", "internet",
    ],
    "patch": [
        "edit", "modify", "update file", "patch", "change",
    ],
    "vision_analyze": [
        "screenshot", "image", "vision", "analyze image", "OCR",
    ],
}

# Gate trigger phrases
_GATE_PHRASES: list[tuple[str, GateType]] = [
    (r"make sure|verify that|ensure|validate|check that", GateType.CONTENT_CHECK),
    (r"at least \d+ (chars|characters|words|lines)", GateType.CONTENT_CHECK),
    (r"must (include|contain|have|pass)", GateType.CONTENT_CHECK),
    (r"should (include|contain|have)", GateType.CONTENT_CHECK),
    (r"review (by|with)|peer review|have .* review", GateType.AGENT_REVIEW),
]

# Agent name patterns — checked with word boundaries to avoid false positives
# (e.g., "coding" must not match "odin")
_AGENT_PATTERNS: list[tuple[str, str]] = [
    # Longer/more-specific patterns first to avoid being shadowed
    ("heimdall", "heimdall"),
    ("strategist", "odin"),
    ("researcher", "mimir"),
    ("executor", "adan"),
    ("coder", "adan"),
    ("creative", "eva"),
    ("auditor", "heimdall"),
    ("odin", "odin"),
    ("mimir", "mimir"),
    ("lilith", "lilith"),
    ("eva", "eva"),
    ("adan", "adan"),
]

# Parallel trigger phrases
_PARALLEL_PHRASES = [
    r"at the same time",
    r"simultaneously",
    r"in parallel",
    r"concurrently",
    r"while also",
    r"alongside",
]


# ── Step Extraction ──────────────────────────────────────────────────────────


@dataclass
class _ExtractedStep:
    """Intermediate representation of a parsed step before conversion."""

    raw_text: str
    name: str = ""
    intent: str = "chat"
    tools: list[str] = field(default_factory=list)
    agent: str = ""
    gate: QualityGate | None = None
    parallel: bool = False
    description: str = ""


def _split_into_steps(text: str) -> list[str]:
    """Split a natural language description into step segments.

    Handles patterns like:
        - "First X, then Y, finally Z"
        - "1. X  2. Y  3. Z"
        - "X. Then Y. After that, Z."
        - "X -> Y -> Z"
        - "X and then Y"
    """
    # Try numbered list first
    numbered = re.split(r"\d+\.\s+", text)
    numbered = [s.strip() for s in numbered if s.strip()]
    if len(numbered) >= 2:
        return numbered

    # Try arrow-separated
    arrow_split = re.split(r"\s*->\s*", text)
    if len(arrow_split) >= 2:
        return [s.strip() for s in arrow_split if s.strip()]

    # Try sequential markers
    sequential_pattern = (
        r"(?:^|[,;.\n])\s*"
        r"(?:first(?:ly)?|then|next|after that|finally|lastly|"
        r"step \d+|phase \d+|afterwards|subsequently|once .+ is done|"
        r"after .+ is complete|when .+ (?:is|are) (?:done|complete|ready))"
        r"\s*"
    )
    parts = re.split(sequential_pattern, text, flags=re.IGNORECASE)
    parts = [p.strip().strip(",.;.") for p in parts if p.strip()]

    if len(parts) >= 2:
        return parts

    # Try comma-separated with action verbs
    # Look for patterns like "research X, design Y, code Z"
    action_verbs = (
        r"(?:research|investigate|design|architect|code|implement|build|"
        r"test|verify|review|audit|deploy|write|create|fix|debug|"
        r"analyze|plan|scaffold|generate|run|check|validate)"
    )
    verb_splits = re.split(
        rf"(?<=\w)[,;]\s+(?={action_verbs})",
        text,
        flags=re.IGNORECASE,
    )
    if len(verb_splits) >= 2:
        return [s.strip() for s in verb_splits if s.strip()]

    # Fallback: treat the whole text as one step
    return [text]


def _detect_intent(text: str) -> str:
    """Detect the workflow intent from step text."""
    text_lower = text.lower()

    # Score each intent
    scores: dict[str, int] = {}
    for intent, keywords in _INTENT_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                scores[intent] = scores.get(intent, 0) + 1

    if not scores:
        return "chat"

    # Return highest-scoring intent
    return max(scores, key=scores.get)  # type: ignore[arg-type]


def _detect_tools(text: str) -> list[str]:
    """Detect tool requirements from step text."""
    text_lower = text.lower()
    tools: list[str] = []

    for tool_name, keywords in _TOOL_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                if tool_name not in tools:
                    tools.append(tool_name)
                break

    return tools


def _detect_agent(text: str) -> str:
    """Detect agent assignment from step text."""
    text_lower = text.lower()

    for pattern, agent_name in _AGENT_PATTERNS:
        # Use word boundary matching to avoid false positives
        # e.g., "coding" should not match "odin"
        if re.search(rf"\b{re.escape(pattern)}\b", text_lower):
            return agent_name

    return ""


def _detect_gate(text: str) -> QualityGate | None:
    """Detect quality gate requirements from step text."""
    text_lower = text.lower()

    for pattern, gate_type in _GATE_PHRASES:
        if re.search(pattern, text_lower):
            gate = QualityGate(type=gate_type)

            # Extract min_length from "at least N chars/words/lines"
            length_match = re.search(
                r"at least (\d+) (chars?|characters?|words?|lines?)",
                text_lower,
            )
            if length_match:
                gate.min_length = int(length_match.group(1))

            # Extract required keywords from "must include/contain X"
            kw_match = re.search(
                r"must (?:include|contain|have) (\w[\w\s,]+)",
                text_lower,
            )
            if kw_match:
                keywords = [k.strip() for k in kw_match.group(1).split(",")]
                gate.required_keywords = [k for k in keywords if k]

            gate.description = f"Auto-detected gate: {gate_type.value}"
            return gate

    return None


def _detect_parallel(text: str) -> bool:
    """Detect if a step should run in parallel."""
    text_lower = text.lower()
    return any(re.search(p, text_lower) for p in _PARALLEL_PHRASES)


def _generate_step_name(text: str, index: int) -> str:
    """Generate a short step name from the text."""
    # Try to extract a verb + object
    text_lower = text.lower().strip()

    # Common action verbs
    verb_match = re.match(
        r"^(research|investigate|design|architect|code|implement|build|"
        r"test|verify|review|audit|deploy|write|create|fix|debug|"
        r"analyze|plan|scaffold|generate|run|check|validate|explore|"
        r"discuss|brainstorm|summarize|document|refactor|optimize)\s+",
        text_lower,
    )
    if verb_match:
        verb = verb_match.group(1)
        # Take first 2-3 words after the verb
        rest = text[len(verb):].strip().split()[:2]
        name_parts = [verb] + rest
        name = "_".join(name_parts)
        # Clean up
        name = re.sub(r"[^a-z0-9_]", "", name)
        return name[:40] if name else f"step_{index + 1}"

    # Fallback: first 3 words
    words = text_lower.split()[:3]
    name = "_".join(words)
    name = re.sub(r"[^a-z0-9_]", "", name)
    return name[:40] if name else f"step_{index + 1}"


# ── Builder ──────────────────────────────────────────────────────────────────


class NLWorkflowBuilder:
    """Converts natural language descriptions into workflow definitions.

    Usage::

        builder = NLWorkflowBuilder()

        # Build a WorkflowDefinition
        workflow = builder.build("Research the topic, then design it, then code it")

        # Get YAML string
        yaml_text = builder.to_yaml("Research X, code Y, test Z")

        # Build with custom name
        workflow = builder.build(
            "First research, then code",
            name="my-pipeline",
            description="Custom research → code pipeline",
        )
    """

    def build(
        self,
        description: str,
        name: str = "",
        workflow_description: str = "",
        on_failure: str = "abort",
        max_retries: int = 1,
        timeout: int = 300,
    ) -> WorkflowDefinition:
        """Build a WorkflowDefinition from a natural language description.

        Args:
            description: Plain-English workflow description.
            name: Workflow name (auto-generated if empty).
            workflow_description: Workflow description (auto-generated if empty).
            on_failure: Failure strategy (abort, skip, retry).
            max_retries: Global max retries per step.
            timeout: Global workflow timeout in seconds.

        Returns:
            A WorkflowDefinition ready for WorkflowEngine.
        """
        if not description or not description.strip():
            raise ValueError("Workflow description cannot be empty")

        # Split into steps
        raw_steps = _split_into_steps(description)

        # Parse each step
        steps: list[WorkflowStep] = []
        for i, raw_step in enumerate(raw_steps):
            extracted = self._parse_step(raw_step, i)
            ws = WorkflowStep(
                name=extracted.name,
                agent=extracted.agent,
                intent=extracted.intent,
                description=extracted.description or extracted.raw_text,
                tools=extracted.tools,
                gate=extracted.gate or QualityGate(),
                parallel=extracted.parallel,
                retry=max_retries if extracted.intent in ("code", "test", "deploy") else 0,
                timeout=timeout // max(len(raw_steps), 1),
            )
            steps.append(ws)

        # Auto-generate name if not provided
        if not name:
            intents = [s.intent for s in steps]
            if len(set(intents)) == 1:
                name = f"{intents[0]}-pipeline"
            else:
                name = "-".join(dict.fromkeys(intents)) + "-pipeline"
            name = re.sub(r"[^a-z0-9-]", "-", name.lower()).strip("-")
            name = name[:50] or "nl-pipeline"

        # Auto-generate description
        if not workflow_description:
            workflow_description = f"Auto-generated from: {description[:100]}"

        # Parse on_failure
        try:
            failure_strategy = OnFailure(on_failure.lower())
        except ValueError:
            failure_strategy = OnFailure.ABORT

        return WorkflowDefinition(
            name=name,
            description=workflow_description,
            version="1.0",
            steps=steps,
            on_failure=failure_strategy,
            max_retries=max_retries,
            timeout=timeout,
            variables={},
            metadata={"source": "nl_workflow_builder", "raw_input": description},
        )

    def to_yaml(
        self,
        description: str | WorkflowDefinition = "",
        name: str = "",
        workflow_description: str = "",
        on_failure: str = "abort",
        max_retries: int = 1,
        timeout: int = 300,
    ) -> str:
        """Build a workflow and return it as a YAML string.

        Args:
            description: Plain-English workflow description, or an existing
                WorkflowDefinition to serialize.
            name: Workflow name.
            workflow_description: Workflow description.
            on_failure: Failure strategy.
            max_retries: Global max retries.
            timeout: Global timeout.

        Returns:
            Valid YAML string that WorkflowEngine.load_yaml() can parse.
        """
        if isinstance(description, WorkflowDefinition):
            return self._workflow_to_yaml(description)
        workflow = self.build(
            description,
            name=name,
            workflow_description=workflow_description,
            on_failure=on_failure,
            max_retries=max_retries,
            timeout=timeout,
        )
        return self._workflow_to_yaml(workflow)

    def _parse_step(self, text: str, index: int) -> _ExtractedStep:
        """Parse a single step text into an ExtractedStep."""
        return _ExtractedStep(
            raw_text=text,
            name=_generate_step_name(text, index),
            intent=_detect_intent(text),
            tools=_detect_tools(text),
            agent=_detect_agent(text),
            gate=_detect_gate(text),
            parallel=_detect_parallel(text),
            description=text.strip(),
        )

    def _workflow_to_yaml(self, workflow: WorkflowDefinition) -> str:
        """Convert a WorkflowDefinition to YAML string."""
        data: dict[str, Any] = {
            "name": workflow.name,
            "description": workflow.description,
            "version": workflow.version,
            "on_failure": workflow.on_failure.value,
            "max_retries": workflow.max_retries,
            "timeout": workflow.timeout,
            "steps": [],
        }

        for step in workflow.steps:
            step_dict: dict[str, Any] = {
                "name": step.name,
                "intent": step.intent,
                "description": step.description,
            }
            if step.agent:
                step_dict["agent"] = step.agent
            if step.tools:
                step_dict["tools"] = step.tools
            if step.parallel:
                step_dict["parallel"] = True
            if step.retry:
                step_dict["retry"] = step.retry
            if step.timeout != 60:
                step_dict["timeout"] = step.timeout
            if step.gate and step.gate.type != GateType.NONE:
                gate_dict: dict[str, Any] = {"type": step.gate.type.value}
                if step.gate.min_length:
                    gate_dict["min_length"] = step.gate.min_length
                if step.gate.required_keywords:
                    gate_dict["required_keywords"] = step.gate.required_keywords
                if step.gate.forbidden_keywords:
                    gate_dict["forbidden_keywords"] = step.gate.forbidden_keywords
                if step.gate.description:
                    gate_dict["description"] = step.gate.description
                step_dict["gate"] = gate_dict

            data["steps"].append(step_dict)

        return yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)
