"""NexusRuntime — the composition root.

Applications compose Nexus; Nexus components do not discover each other through
global state. NexusRuntime wires injected components into one working system and
exposes a minimal, uniform application surface (`ask`, `run_one`, `task`,
`events`). The API, CLI, tests, and dashboard all share this runtime — only the
injected components differ (fakes in tests, MCP/Chroma/durable in production).
"""
from __future__ import annotations

from core.contracts import Task, new_id
from core.events import EventBus
from control.evaluator import Evaluator
from control.policy import PolicyEngine, PolicyRules
from control.tools import ToolRegistry
from execution.orchestrator import Orchestrator
from execution.worker import Worker


class NexusRuntime:
    """The one place that wires retriever + executor + policy + evaluator + tools
    + queue + event bus into an orchestrator and a worker."""

    def __init__(self, *, retriever, executor, queue, event_bus=None,
                 policy=None, evaluator=None, tools=None, router=None,
                 worker_id="worker-1", max_replans=2):
        self.event_bus = event_bus or EventBus()
        self.policy = policy or PolicyEngine(PolicyRules())
        self.evaluator = evaluator or Evaluator()
        self.tools = tools or ToolRegistry()
        self.router = router
        self.retriever = retriever
        self.executor = executor
        self.queue = queue
        self.worker_id = worker_id

        # composition (the only place these are wired together)
        self.orchestrator = Orchestrator(
            retriever=retriever, executor=executor, policy=self.policy,
            tools=self.tools, evaluator=self.evaluator, bus=self.event_bus,
            max_replans=max_replans)
        self.worker = Worker(worker_id=worker_id, queue=queue,
                             orchestrator=self.orchestrator)

    # -- application surface (identical for fake and real components) -------
    def ask(self, title: str) -> str:
        """Submit a task; returns its id. Asynchronous by default — the caller
        polls `task`/`events` rather than blocking on the whole run."""
        task = Task(task_id=new_id("task"), title=title)
        self.queue.enqueue(task)
        return task.task_id

    def run_one(self):
        """Drain one queued task through the worker. Returns its Outcome, or None
        if the queue is empty."""
        return self.worker.run_one()

    def task(self, task_id: str):
        return self.queue.get(task_id)

    def events(self, task_id: str):
        """The durable event log for a task (a projection the surface can render)."""
        loader = getattr(self.event_bus, "load_events", None)
        if loader is None:
            return [e for e in self.event_bus.history if e.task_id == task_id]
        return loader(task_id=task_id)
