"""Worker — claims tasks and runs them through the orchestrator.

The worker owns the task-level sequence (claim -> run -> ack). It never knows
how retrieval works, how tools execute, or how models run — it only composes the
injected queue and orchestrator. It has no provider knowledge (enforced by
tests/conformance/test_worker_purity.py).

Delivery is AT-LEAST-ONCE: if the worker dies between `claim` and `complete`,
the task stays CLAIMED (durable) and is recoverable; a tool may execute twice.
That is explicit, never hidden.

**Ownership (AD-028, AD-032).** A terminal transition (`complete`/`fail`) must
present the exact `Claim` (worker_id + generation) the worker acquired. A stale
worker whose lease expired and whose task was re-claimed gets `False` back, and —
crucially — does NOT publish a terminal run record: its run's fingerprint stays
NULL, so recovery never manufactures a second authoritative run.
"""
from __future__ import annotations

from core.contracts import TaskStatus
from core.fingerprint import fingerprint


class Worker:
    def __init__(self, *, worker_id: str, queue, orchestrator, run_records=None) -> None:
        self.worker_id = worker_id
        self.queue = queue
        self.orchestrator = orchestrator
        self.run_records = run_records

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
            # durable pause: not terminal -> no fingerprint (AD-032)
            self.queue.wait(task.task_id)
            return outcome
        # Terminal transition, guarded by the claim generation. Only the CURRENT
        # generation may publish the run's terminal fingerprint (AD-032): a stale
        # worker gets False and leaves the record's fingerprint NULL.
        if outcome.live_state.task_status == TaskStatus.DONE:
            recorded = self.queue.complete(claim, outcome.answer)
            status = "completed"
        else:
            recorded = self.queue.fail(claim, "run failed")
            status = "failed"
        if recorded and self.run_records is not None:
            self.run_records.record_terminal(
                outcome.run.run_id,
                fingerprint([e for e in outcome.events if e.run_id == outcome.run.run_id]),
                status)
        return outcome
