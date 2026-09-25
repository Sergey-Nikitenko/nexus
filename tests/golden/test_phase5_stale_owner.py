"""Phase 5.9 golden task — a stale owner is harmless (A2-1, closed).

The adversarial scenario audit #2 flagged:

    t0 w1 claims (generation 1)
    t1 w1 starts work
    t2 recovery reclaims the task
    t3 w2 claims (generation 2)
    t4 w2 completes
    t5 stale w1 finishes
    t6 w1 marks the task DONE  <- the bug: stale owner wins

The fix makes ownership generation-specific (AD-028): a terminal transition
(`complete`/`fail`) is a conditional UPDATE guarded by (worker_id, generation),
so a stale owner's completion/failure is rejected — a no-op that emits nothing
and overwrites nothing. The strongest assertion: a worker may only complete/fail
the claim generation it currently owns.

Before this change: stale workers had to be assumed dead before reclamation.
After: stale workers are harmless even if they remain alive.

Run:  py tests/golden/test_phase5_stale_owner.py
"""
import os
import sys
import tempfile
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import Task, TaskStatus, new_id, utcnow  # noqa: E402
from core.events import EventType  # noqa: E402
from execution.durable import DurableEventBus  # noqa: E402
from execution.queue import TaskQueue  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def _stale_sequence(q, task):
    """The A2-1 sequence: claim gen1 -> recovery -> claim gen2. Returns the two
    claims (stale, current)."""
    stale = q.claim("w1")
    q.recover_abandoned(lease_seconds=1, now=utcnow() + timedelta(seconds=10))
    current = q.claim("w2")
    return stale, current


def main():
    print("Phase 5.9 golden task: a stale owner is harmless (A2-1)")

    # --- 1. stale COMPLETION is rejected ------------------------------------
    tmp = tempfile.mkdtemp()
    bus = DurableEventBus(os.path.join(tmp, "events.db"))
    q = TaskQueue(os.path.join(tmp, "queue.db"), bus=bus)
    task = Task(task_id=new_id("task"), title="x")
    q.enqueue(task)

    stale, current = _stale_sequence(q, task)
    check(stale.generation == 1 and current.generation == 2,
          "recovery advanced the claim generation (1 -> 2)")
    check(q.complete(current, "w2-answer") is True,
          "w2 (current owner) completes successfully")
    check(q.complete(stale, "w1-stale-answer") is False,
          "w1 (stale owner) completion is REJECTED (explicit False, not an overwrite)")

    check(q.get(task.task_id).status == TaskStatus.DONE, "the task is DONE")
    check(q.owner(task.task_id) == "w2", "the owner is w2 (the current generation)")
    check(q.generation(task.task_id) == 2, "the claim generation is 2 (not rolled back)")
    completed = [e for e in bus.load_events(task_id=task.task_id)
                 if e.event_type == EventType.TASK_COMPLETED]
    check(len(completed) == 1 and completed[0].payload.get("answer") == "w2-answer",
          "exactly one task.completed (w2's); the stale completion emitted nothing")
    bus.close()
    q.close()

    # --- 2. stale FAILURE is rejected ---------------------------------------
    tmp2 = tempfile.mkdtemp()
    bus2 = DurableEventBus(os.path.join(tmp2, "events.db"))
    q2 = TaskQueue(os.path.join(tmp2, "queue.db"), bus=bus2)
    task2 = Task(task_id=new_id("task"), title="y")
    q2.enqueue(task2)

    stale2, current2 = _stale_sequence(q2, task2)
    check(current2 is not None and q2.fail(current2, "w2-error") is True,
          "w2 (current owner) fails successfully")
    check(q2.fail(stale2, "w1-stale-error") is False,
          "w1 (stale owner) failure is REJECTED (explicit False)")

    check(q2.get(task2.task_id).status == TaskStatus.FAILED, "the task is FAILED")
    check(q2.owner(task2.task_id) == "w2", "the owner is still w2")
    failed = [e for e in bus2.load_events(task_id=task2.task_id)
              if e.event_type == EventType.TASK_FAILED]
    check(len(failed) == 1 and failed[0].payload.get("error") == "w2-error",
          "exactly one task.failed (w2's); the stale failure emitted nothing")
    bus2.close()
    q2.close()

    print("\nPASS: Phase 5.9 stale-owner isolation holds (A2-1 resolved).")


if __name__ == "__main__":
    main()
