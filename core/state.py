"""Recoverable state.

The rule: state is a projection of events, not a thing components mutate in
place. Reconstructing a Run from its event history must reproduce the same
state — that is what makes a crashed worker recoverable.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .contracts import Event, Run, Step, StepStatus, TaskStatus


@dataclass
class RunState:
    run: Run
    step_status: dict[str, StepStatus] = field(default_factory=dict)
    task_status: TaskStatus = TaskStatus.RUNNING
    replan_count: int = 0
    evaluations: list[dict] = field(default_factory=list)

    @classmethod
    def reconstruct(cls, run: Run, events: list[Event]) -> "RunState":
        """Rebuild state purely from the event log — including the replan/
        evaluation history, so a crashed worker recovers the same attempt trail."""
        state = cls(run=run)
        for ev in events:
            if ev.event_type in ("step.started",):
                state.step_status[ev.payload.get("step_id", "")] = StepStatus.RUNNING
            elif ev.event_type == "step.completed":
                state.step_status[ev.payload.get("step_id", "")] = StepStatus.PASS
            elif ev.event_type == "step.failed":
                state.step_status[ev.payload.get("step_id", "")] = StepStatus.FAIL
            elif ev.event_type == "run.completed":
                state.task_status = TaskStatus.DONE
            elif ev.event_type == "run.failed":
                state.task_status = TaskStatus.FAILED
            elif ev.event_type == "run.replanned":
                state.replan_count += 1
            elif ev.event_type == "evaluation.completed":
                state.evaluations.append({
                    "passed": ev.payload.get("passed"),
                    "reason": ev.payload.get("reason"),
                    "replan_required": ev.payload.get("replan_required"),
                })
        return state

    @property
    def completed_steps(self) -> list[str]:
        return [sid for sid, s in self.step_status.items() if s == StepStatus.PASS]
