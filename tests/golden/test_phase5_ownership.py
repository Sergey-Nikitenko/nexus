"""Phase 5.3 golden task — task ownership is atomic (two distinct races).

5.1 proved approval CONSUMPTION is atomic. That does not prove task OWNERSHIP is
atomic, so 5.3 proves the two ownership transitions separately:

- **claim race**: two workers race `claim` on ONE task -> exactly one CLAIMED;
  the loser gets an explicit None, never an exception.
- **recovery/claim race**: a recoverer and a worker race over an abandoned
  (CLAIMED, lease-expired) task -> exactly one owner, never two, never a silent
  overwrite.

Both races run on a SINGLE shared store, so they also exercise the per-thread
connection policy from 5.2.

Run:  py tests/golden/test_phase5_ownership.py
"""
import os
import sys
import tempfile
import threading
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import Task, TaskStatus, new_id, utcnow  # noqa: E402
from execution.queue import TaskQueue  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def main():
    print("Phase 5.3 golden task: atomic task ownership")

    # --- 1. claim race: two workers, one task --------------------------------
    path = os.path.join(tempfile.mkdtemp(), "claim.db")
    q = TaskQueue(path)
    task = Task(task_id=new_id("task"), title="one task only")
    q.enqueue(task)

    barrier = threading.Barrier(2)
    claims: list = [None, None]
    errors: list[Exception] = []

    def claim(idx):
        try:
            barrier.wait()
            claims[idx] = q.claim(f"w{idx}")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    ts = [threading.Thread(target=claim, args=(i,)) for i in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    got = [c for c in claims if c is not None]
    check(not errors, "no exception during the claim race")
    check(len(got) == 1, "exactly one worker CLAIMED the task")
    check(claims.count(None) == 1, "the loser got an explicit None (not an exception)")
    check(got[0].task.task_id == task.task_id and got[0].task.status == TaskStatus.CLAIMED,
          "the winner owns exactly the task it claimed")
    check(q.owner(task.task_id) in ("w0", "w1"),
          "the task has exactly one owner (a single worker_id)")
    q.close()

    # --- 2. recovery/claim race: exactly one owner ---------------------------
    path2 = os.path.join(tempfile.mkdtemp(), "recover.db")
    q2 = TaskQueue(path2)
    task2 = Task(task_id=new_id("task"), title="abandoned")
    q2.enqueue(task2)
    q2.claim("w1")  # CLAIMED by w1; the lease will be treated as expired

    barrier2 = threading.Barrier(2)
    recovered: list = [None]
    claimed: list = [None]
    errors2: list[Exception] = []

    def recoverer():
        try:
            barrier2.wait()
            recovered[0] = q2.recover_abandoned(
                lease_seconds=1, now=utcnow() + timedelta(seconds=10))
        except Exception as exc:  # noqa: BLE001
            errors2.append(exc)

    def claimer():
        try:
            barrier2.wait()
            claimed[0] = q2.claim("w2")
        except Exception as exc:  # noqa: BLE001
            errors2.append(exc)

    t1 = threading.Thread(target=recoverer)
    t2 = threading.Thread(target=claimer)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    check(not errors2, "no exception in either racer (the loser gets an explicit result)")
    check(recovered[0] == [task2.task_id], "the recoverer requeued the abandoned task")

    owner = q2.owner(task2.task_id)
    status = q2.get(task2.task_id).status
    if claimed[0] is not None:
        check(claimed[0].task.task_id == task2.task_id and owner == "w2"
              and status == TaskStatus.CLAIMED,
              "claimer won -> exactly one owner (w2)")
    else:
        check(owner is None and status == TaskStatus.QUEUED,
              "claimer lost (explicit None) -> task requeued with no owner")

    # no state in between: the task is never owned by two workers at once
    check(q2.owner(task2.task_id) in (None, "w2"),
          "recovery/claim race yields exactly one owner (none or w2), never two")
    q2.close()

    print("\nPASS: Phase 5.3 atomic task ownership holds (claim + recovery/claim).")


if __name__ == "__main__":
    main()
