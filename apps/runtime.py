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
                 approvals=None, run_records=None, worker_id="worker-1",
                 max_replans=2):
        self.event_bus = event_bus or EventBus()
        self.policy = policy or PolicyEngine(PolicyRules())
        self.evaluator = evaluator or Evaluator()
        self.tools = tools or ToolRegistry()
        self.router = router
        self.approvals = approvals
        self.run_records = run_records
        self.retriever = retriever
        self.executor = executor
        self.queue = queue
        self.worker_id = worker_id

        # composition (the only place these are wired together)
        self.orchestrator = Orchestrator(
            retriever=retriever, executor=executor, policy=self.policy,
            tools=self.tools, evaluator=self.evaluator, bus=self.event_bus,
            approvals=self.approvals, run_records=self.run_records,
            max_replans=max_replans)
        self.worker = Worker(worker_id=worker_id, queue=queue,
                             orchestrator=self.orchestrator, run_records=self.run_records)

    # -- application surface (identical for fake and real components) -------
    def ask(self, title: str, user: str = "", agent: str = "",
            parent_run_id: str = "") -> str:
        """Submit a task; returns its id. Asynchronous by default — the caller
        polls `task`/`events` rather than blocking on the whole run. `user`,
        `agent`, and `parent_run_id` record identity + delegation (AD-034/035)."""
        task = Task(task_id=new_id("task"), title=title,
                    user=user, agent=agent, parent_run_id=parent_run_id)
        self.queue.enqueue(task)
        return task.task_id

    def run_one(self):
        """Drain one queued task through the worker. Returns its Outcome, or None
        if the queue is empty."""
        return self.worker.run_one()

    def task(self, task_id: str):
        return self.queue.get(task_id)

    def events(self, task_id: str | None = None, run_id: str | None = None):
        """The durable event log for a task and/or run (a projection the surface
        can render)."""
        loader = getattr(self.event_bus, "load_events", None)
        if loader is None:
            return [e for e in self.event_bus.history
                    if (task_id is None or e.task_id == task_id)
                    and (run_id is None or e.run_id == run_id)]
        return loader(task_id=task_id, run_id=run_id)

    def approve(self, approval_id: str):
        """Approve a pending approval and requeue its task — a command, never an
        execution. Returns the approval, or None if approvals aren't wired."""
        if self.approvals is None:
            return None
        approval = self.approvals.approve(approval_id)
        if approval is not None:
            self.queue.requeue(approval.task_id)
        return approval

    def deny(self, approval_id: str):
        """Deny a pending approval and fail its task — a command, never an execution."""
        if self.approvals is None:
            return None
        approval = self.approvals.deny(approval_id)
        if approval is not None:
            self.queue.fail_waiting(approval.task_id, "approval denied")
        return approval
