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
    TASK_CREATED = "task.created"
    RUN_STARTED = "run.started"
    STEP_STARTED = "step.started"
    STEP_COMPLETED = "step.completed"
    STEP_FAILED = "step.failed"
    MODEL_SELECTED = "model.selected"
    MODEL_REQUESTED = "model.requested"
    MODEL_COMPLETED = "model.completed"
    RETRIEVAL_COMPLETED = "retrieval.completed"
    TOOL_REQUESTED = "tool.requested"
    TOOL_COMPLETED = "tool.completed"
    APPROVAL_REQUIRED = "approval.required"
    APPROVAL_GRANTED = "approval.granted"
    MODEL_FALLBACK = "model.fallback"
    EVALUATION_COMPLETED = "evaluation.completed"
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

    def publish(self, event: Event) -> None:
        self.history.append(event)
        for h in self._handlers.get(event.event_type, []):
            h(event)
        for h in self._all:
            h(event)
