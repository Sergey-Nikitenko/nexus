"""Phase 5.1 golden task — atomic approval consumption.

Two workers race to consume the SAME approved proposal. The database (a single
conditional UPDATE), not Python timing, decides the winner: exactly one worker
transitions APPROVED -> CONSUMED and executes the tool; the other is rejected.

This exercises the persistence boundary concurrently (two separate connections),
not a mocked race.

Run:  py tests/golden/test_phase5_concurrency.py
"""
import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import ApprovalRequest, Risk, new_id  # noqa: E402
from execution.approvals import ApprovalStore  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def main():
    print("Phase 5.1 golden task: atomic approval consumption")
    path = os.path.join(tempfile.mkdtemp(), "approvals.db")

    # set up an APPROVED approval
    store = ApprovalStore(path)
    approval = ApprovalRequest(approval_id=new_id("appr"), task_id="t1", run_id="r1",
                               tool_name="github.create_pr", risk=Risk.WRITE)
    store.create(approval)
    store.approve(approval.approval_id)
    store.close()

    # two workers, each with its OWN connection, race to consume the same approval
    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    executions: list[int] = []
    lock = threading.Lock()

    def worker():
        s = ApprovalStore(path)  # separate connection (one per worker)
        found = s.find_approved("t1", "github.create_pr", Risk.WRITE)
        barrier.wait()  # both have FOUND (both saw APPROVED) before either consumes
        if found is None:
            outcome = "rejected"  # the approval disappeared before we looked
        else:
            consumed = s.consume_approved(found.approval_id)
            outcome = "executed" if consumed is not None else "rejected"
            if consumed is not None:
                executions.append(1)  # only the winner executes the tool
        with lock:
            outcomes.append(outcome)
        s.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    check(sorted(outcomes) == ["executed", "rejected"],
          "exactly one worker consumes + executes, the other is rejected")
    check(len(executions) == 1, "the tool executes exactly once (no double execution)")

    final = ApprovalStore(path)
    check(final.get(approval.approval_id).status == "consumed",
          "the approval reaches CONSUMED")
    final.close()

    print("\nPASS: Phase 5.1 atomic approval consumption holds.")


if __name__ == "__main__":
    main()
