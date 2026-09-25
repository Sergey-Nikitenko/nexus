"""Worker — claims tasks and runs them through the orchestrator.

The worker owns the task-level sequence (claim -> run -> ack). It never knows
how retrieval works, how tools execute, or how models run — it only composes the
injected queue and orchestrator. It has no provider knowledge (enforced by
tests/conformance/test_worker_purity.py).

Delivery is AT-LEAST-ONCE: if the worker dies between `claim` and `complete`,
the task stays CLAIMED (durable) and is recoverable; a tool may execute twice.
That is explicit, never hidden.

**Ownership (AD-028).** A terminal transition (`complete`/`fail`) must present the
exact `Claim` (worker_id + generation) the worker acquired. A stale worker whose
lease expired and whose task was re-claimed simply gets `False` back — its
completion/failure is a no-op, so it can never overwrite the new owner.
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
        claim = self.queue.claim(self.worker_id)
        if claim is None:
            return None
        task = claim.task
        try:
            outcome = self.orchestrator.run(task)
        except Exception as exc:
            self.queue.fail(claim, f"worker error: {exc}")
            raise
        if outcome.waiting:
            # durable pause: the task waits for human approval, not lost/failed
            self.queue.wait(task.task_id)
        elif outcome.live_state.task_status == TaskStatus.DONE:
            self.queue.complete(claim, outcome.answer)
        else:
            self.queue.fail(claim, "run failed")
        return outcome
