"""Plan & Execute workflow for the Lilith IDE agent."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PlanStep:
    """A single step in an agent-generated plan."""

    number: int
    description: str
    done: bool = False


@dataclass
class AgentPlan:
    """A multi-step plan produced by Lilith."""

    goal: str = ""
    steps: list[PlanStep] = field(default_factory=list)

    def is_complete(self) -> bool:
        return all(step.done for step in self.steps)

    def reset(self) -> None:
        for step in self.steps:
            step.done = False

    def mark_done(self, number: int) -> bool:
        for step in self.steps:
            if step.number == number:
                step.done = True
                return True
        return False

    def next_pending(self) -> PlanStep | None:
        for step in self.steps:
            if not step.done:
                return step
        return None


def parse_plan(text: str) -> AgentPlan:
    """Parse a numbered plan from agent output.

    Recognises lines like:

        1. Implement the auth middleware
        2. Add the login route
        3. Write tests

    Returns an ``AgentPlan`` with the extracted steps.
    """
    steps: list[PlanStep] = []
    pattern = re.compile(r"^\s*(\d+)\.\s*(.+)$", re.MULTILINE)
    for match in pattern.finditer(text):
        number = int(match.group(1))
        description = match.group(2).strip()
        steps.append(PlanStep(number=number, description=description))
    return AgentPlan(steps=steps)


def build_planning_prompt(goal: str) -> str:
    """Return a prompt that asks Lilith for a numbered plan."""
    return (
        f"Creá un plan paso a paso para: {goal}\n\n"
        "Respondé ÚNICAMENTE con una lista numerada de pasos concretos y ejecutables. "
        "No incluyas código ni explicaciones fuera de la lista. "
        "Formato:\n1. Primer paso\n2. Segundo paso\n..."
    )


def build_execution_prompt(step: PlanStep, *, previous_steps: list[PlanStep] | None = None) -> str:
    """Return a prompt that asks Lilith to execute a single plan step."""
    context = ""
    if previous_steps:
        done = "\n".join(f"- {s.number}. {s.description}" for s in previous_steps if s.done)
        if done:
            context = f"Pasos ya completados:\n{done}\n\n"
    return (
        f"{context}Ejecutá el siguiente paso del plan y aplicá los cambios necesarios "
        f"directamente en el código.\n\nPaso {step.number}: {step.description}\n\n"
        "Si generás código, usá bloques de código markdown para que se forjen Runestones."
    )


def plan_to_dict(plan: AgentPlan) -> dict[str, Any]:
    return {
        "goal": plan.goal,
        "steps": [
            {"number": s.number, "description": s.description, "done": s.done}
            for s in plan.steps
        ],
    }
