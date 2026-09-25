"""Event types and a minimal in-process event bus.

Phase 0: a synchronous bus so components emit/consume events without calling
each other directly. A real (async/durable) bus is a Phase 3 upgrade — the
consumers don't change, only the transport does.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Callable

from .contracts import Event


class EventType(str):
    TASK_QUEUED = "task.queued"
    TASK_CLAIMED = "task.claimed"
    TASK_WAITING = "task.waiting"
    TASK_REQUEUED = "task.requeued"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    RUN_MANIFEST = "run.manifest"
    RUN_STARTED = "run.started"
    STEP_STARTED = "step.started"
    STEP_COMPLETED = "step.completed"
    STEP_FAILED = "step.failed"
    # RESERVED (not yet emitted): model selection/fallback are Phase 6 — the
    # router is not wired into the orchestrator yet, but the event names are
    # reserved so the taxonomy doesn't churn later.
    MODEL_SELECTED = "model.selected"
    MODEL_FALLBACK = "model.fallback"
    MODEL_REQUESTED = "model.requested"
    MODEL_COMPLETED = "model.completed"
    RETRIEVAL_REQUESTED = "retrieval.requested"
    RETRIEVAL_COMPLETED = "retrieval.completed"
    TOOL_REQUESTED = "tool.requested"
    TOOL_COMPLETED = "tool.completed"
    POLICY_DECISION = "policy.decision"
    APPROVAL_REQUIRED = "approval.required"
    APPROVAL_GRANTED = "approval.granted"
    APPROVAL_DENIED = "approval.denied"
    APPROVAL_CONSUMED = "approval.consumed"
    EVALUATION_COMPLETED = "evaluation.completed"
    RUN_REPLANNED = "run.replanned"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"


Handler = Callable[[Event], None]


class EventBus:
    """Synchronous, in-process, ordered per-event-type. Boring on purpose."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self._all: list[Handler] = []
        self.history: list[Event] = []

    def subscribe(self, event_type: str, handler: Handler) -> None:
        self._handlers[event_type].append(handler)

    def subscribe_all(self, handler: Handler) -> None:
        self._all.append(handler)

    def unsubscribe(self, event_type: str, handler: Handler) -> None:
        if handler in self._handlers.get(event_type, []):
            self._handlers[event_type].remove(handler)

    def unsubscribe_all(self, handler: Handler) -> None:
        if handler in self._all:
            self._all.remove(handler)

    def publish(self, event: Event) -> None:
        self.history.append(event)
        for h in self._handlers.get(event.event_type, []):
            h(event)
        for h in self._all:
            h(event)
