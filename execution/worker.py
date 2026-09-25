"""Worker — claims tasks and runs them through the orchestrator.

The worker owns the task-level sequence (claim -> run -> ack). It never knows
how retrieval works, how tools execute, or how models run — it only composes the
injected queue and orchestrator. It has no provider knowledge (enforced by
tests/conformance/test_worker_purity.py).

Delivery is AT-LEAST-ONCE: if the worker dies between `claim` and `complete`,
the task stays CLAIMED (durable) and is recoverable; a tool may execute twice.
That is explicit, never hidden.
"""
from __future__ import annotations

from core.contracts import TaskStatus


class Worker:
    def __init__(self, *, worker_id: str, queue, orchestrator) -> None:
        self.worker_id = worker_id
        self.queue = queue
        self.orchestrator = orchestrator

    def run_one(self):
        """Claim one task and run it. Returns the outcome, or None if the queue
        is empty. A failure is recorded durably before the exception propagates,
        so a task is never silently lost."""
        task = self.queue.claim(self.worker_id)
        if task is None:
            return None
        try:
            outcome = self.orchestrator.run(task)
        except Exception as exc:
            self.queue.fail(task.task_id, f"worker error: {exc}")
            raise
        if outcome.live_state.task_status == TaskStatus.DONE:
            self.queue.complete(task.task_id, outcome.answer)
        else:
            self.queue.fail(task.task_id, "run failed")
        return outcome
