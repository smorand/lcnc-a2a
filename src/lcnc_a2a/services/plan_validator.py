"""Plan & Execute planner output validator (FR-016, FR-020)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


class PlanValidationError(Exception):
    """Raised when a planner JSON output is malformed or violates a rule.

    The string representation is intended to be inlined into the planner's
    retry prompt so the model can recover. Tests assert on substrings of the
    message, so wording is part of the contract:
    - ``max_steps={N}`` whenever the step count exceeds the budget.
    - ``unknown tool: {name}`` for tools not present in the catalog.
    - ``forward dependency`` whenever a step's ``depends_on`` points to a
      step in the same or a later stage.
    """


@dataclass(frozen=True, slots=True)
class PlanStep:
    """A validated step in the plan."""

    id: int
    stage: int
    description: str
    tool: str
    args: dict[str, Any]
    success_criterion: str
    depends_on: list[int]

    def to_payload(self) -> dict[str, Any]:
        """Round-trip back to a JSON-friendly dict."""
        return {
            "id": self.id,
            "stage": self.stage,
            "description": self.description,
            "tool": self.tool,
            "args": dict(self.args),
            "success_criterion": self.success_criterion,
            "depends_on": list(self.depends_on),
        }


@dataclass(frozen=True, slots=True)
class Plan:
    """A validated plan."""

    goal: str
    steps: list[PlanStep]

    def to_payload(self) -> dict[str, Any]:
        """Round-trip back to a JSON-friendly dict."""
        return {"goal": self.goal, "steps": [s.to_payload() for s in self.steps]}


def parse_and_validate_plan(
    raw: str,
    *,
    max_steps: int,
    available_tools: set[str],
) -> tuple[Plan, dict[str, Any]]:
    """Parse and validate a planner JSON string.

    Returns ``(plan, raw_payload)``. The raw payload is the deep-copied
    dict from JSON parsing so callers can persist it deep-equal to the
    planner's literal output.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PlanValidationError(f"invalid JSON: {exc.msg}") from exc

    if not isinstance(data, dict):
        raise PlanValidationError("plan must be a JSON object")

    goal = data.get("goal")
    if not isinstance(goal, str):
        raise PlanValidationError("plan.goal must be a string")

    raw_steps = data.get("steps")
    if not isinstance(raw_steps, list):
        raise PlanValidationError("plan.steps must be an array")

    if len(raw_steps) < 1:
        raise PlanValidationError(f"plan.steps is empty; expected 1..max_steps={max_steps}")
    if len(raw_steps) > max_steps:
        raise PlanValidationError(f"plan.steps has {len(raw_steps)} entries, exceeds max_steps={max_steps}")

    steps: list[PlanStep] = []
    seen_ids: set[int] = set()

    for raw_step in raw_steps:
        if not isinstance(raw_step, dict):
            raise PlanValidationError("each step must be a JSON object")
        sid = raw_step.get("id")
        if not isinstance(sid, int) or isinstance(sid, bool) or sid <= 0:
            raise PlanValidationError(f"step.id must be a positive integer; got {sid!r}")
        if sid in seen_ids:
            raise PlanValidationError(f"duplicate step id: {sid}")
        seen_ids.add(sid)
        stage = raw_step.get("stage")
        if not isinstance(stage, int) or isinstance(stage, bool) or stage <= 0:
            raise PlanValidationError(f"step {sid}: stage must be a positive integer; got {stage!r}")
        tool = raw_step.get("tool")
        if not isinstance(tool, str) or not tool:
            raise PlanValidationError(f"step {sid}: tool must be a non-empty string")
        if tool != "synthesize" and tool not in available_tools:
            raise PlanValidationError(f"step {sid}: unknown tool: {tool}")
        args_raw: Any = raw_step.get("args", {})
        if args_raw is None:
            args_raw = {}
        if not isinstance(args_raw, dict):
            raise PlanValidationError(f"step {sid}: args must be a JSON object")
        description = raw_step.get("description", "")
        if not isinstance(description, str):
            raise PlanValidationError(f"step {sid}: description must be a string")
        criterion = raw_step.get("success_criterion", "")
        if not isinstance(criterion, str):
            raise PlanValidationError(f"step {sid}: success_criterion must be a string")
        deps_raw: Any = raw_step.get("depends_on", [])
        if deps_raw is None:
            deps_raw = []
        if not isinstance(deps_raw, list) or not all(isinstance(d, int) and not isinstance(d, bool) for d in deps_raw):
            raise PlanValidationError(f"step {sid}: depends_on must be a list of integers")
        steps.append(
            PlanStep(
                id=sid,
                stage=stage,
                description=description,
                tool=tool,
                args=dict(args_raw),
                success_criterion=criterion,
                depends_on=list(deps_raw),
            )
        )

    by_id = {s.id: s for s in steps}
    for step in steps:
        for dep_id in step.depends_on:
            dep = by_id.get(dep_id)
            if dep is None:
                raise PlanValidationError(f"step {step.id}: depends_on references unknown step {dep_id}")
            if dep.stage >= step.stage:
                raise PlanValidationError(
                    f"step {step.id} (stage {step.stage}) has a forward dependency on step "
                    f"{dep_id} (stage {dep.stage}); depends_on must reference a strictly lower stage"
                )

    return Plan(goal=goal, steps=steps), data


__all__ = [
    "Plan",
    "PlanStep",
    "PlanValidationError",
    "parse_and_validate_plan",
]
