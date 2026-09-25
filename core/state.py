"""Recoverable state.

The rule: state is a projection of events, not a thing components mutate in
place. Reconstructing a Run from its event history must reproduce the same
state — that is what makes a crashed worker recoverable.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .contracts import Event, Run, Step, StepStatus, Task, TaskStatus


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


@dataclass
class TaskState:
    """The queue/worker view of a task — a projection of task.* events.

    Ownership is a durable event (`task.claimed` with a worker_id), not an
    in-memory flag. A task whose last lifecycle event is `task.claimed` with no
    terminal event is RECOVERABLE: another worker can observe it and re-claim it.
    """
    task: Task
    status: TaskStatus = TaskStatus.QUEUED
    worker_id: str | None = None

    @classmethod
    def reconstruct(cls, task: Task, events: list[Event]) -> "TaskState":
        state = cls(task=task)
        for ev in events:
            if ev.event_type == "task.claimed":
                state.status = TaskStatus.CLAIMED
                state.worker_id = ev.payload.get("worker_id")
            elif ev.event_type == "task.waiting":
                state.status = TaskStatus.AWAITING_APPROVAL
            elif ev.event_type == "task.requeued":
                state.status = TaskStatus.QUEUED
                state.worker_id = None
            elif ev.event_type == "task.completed":
                state.status = TaskStatus.DONE
            elif ev.event_type == "task.failed":
                state.status = TaskStatus.FAILED
        return state


@dataclass
class ApprovalState:
    """An approval's lifecycle, projected from approval.* events.

    The approval store persists records AND emits events; this projection makes
    the events the authoritative lifecycle, exactly like TaskState/RunState."""
    approval_id: str
    status: str = "pending"

    @classmethod
    def reconstruct(cls, approval_id: str, events: list[Event]) -> "ApprovalState":
        state = cls(approval_id=approval_id)
        for ev in events:
            if ev.payload.get("approval_id") != approval_id:
                continue
            if ev.event_type == "approval.required":
                state.status = "pending"
            elif ev.event_type == "approval.granted":
                state.status = "approved"
            elif ev.event_type == "approval.denied":
                state.status = "denied"
            elif ev.event_type == "approval.consumed":
                state.status = "consumed"
        return state
