"""Phase 5.2 golden task — the concurrency contract (per-worker connections).

Finding #2 was: with `check_same_thread=False`, a single SQLite connection could
be written from two threads at once — unsafe. 5.2 makes the promise explicit and
mechanically true:

1. each worker thread gets its OWN SQLite connection (never a shared one);
2. the connection lifecycle belongs to the store component, not the worker thread;
3. SQLite is configured deliberately (busy_timeout, WAL, foreign_keys);
4. concurrent INDEPENDENT work succeeds — one worker's transition does not
   serialize (or deadlock) the whole system.

Exclusive-transition atomicity (exactly one winner; the loser gets an explicit
None) is proven separately in 5.1 (`test_phase5_concurrency.py`) and 5.3
(`test_phase5_ownership.py`).

Run:  py tests/golden/test_phase5_connections.py
"""
import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import Task, TaskStatus, new_id  # noqa: E402
from execution.queue import TaskQueue  # noqa: E402
from execution.sqlite import sqlite_connect  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def main():
    print("Phase 5.2 golden task: the concurrency contract")

    # --- 1. SQLite behavior is deliberate -----------------------------------
    conn = sqlite_connect(os.path.join(tempfile.mkdtemp(), "probe.db"))
    check(conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000,
          "busy_timeout is configured (a writer waits, it does not fail)")
    check(conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal",
          "journal_mode=WAL (one writer + many readers)")
    check(conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1,
          "foreign_keys=ON (integrity enforced, not assumed)")
    check(conn.execute("PRAGMA synchronous").fetchone()[0] == 1,
          "synchronous=NORMAL (WAL-mode durability sweet spot)")
    conn.close()

    # --- 2. one connection per thread, owned by the store --------------------
    path = os.path.join(tempfile.mkdtemp(), "queue.db")
    q = TaskQueue(path)
    check(q.connection_count() == 1,
          "one connection opened (the schema), owned by the store")

    barrier = threading.Barrier(2)

    def enqueue(idx):
        barrier.wait()
        q.enqueue(Task(task_id=new_id("task"), title=f"t{idx}"))

    ts = [threading.Thread(target=enqueue, args=(i,)) for i in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    check(q.connection_count() == 3,
          "each worker thread opened its OWN connection (never a shared one)")
    q.close()

    # --- 3. concurrent INDEPENDENT work --------------------------------------
    path2 = os.path.join(tempfile.mkdtemp(), "queue2.db")
    q2 = TaskQueue(path2)
    t1 = Task(task_id=new_id("task"), title="one")
    t2 = Task(task_id=new_id("task"), title="two")
    q2.enqueue(t1)
    q2.enqueue(t2)

    claimed: dict[str, str] = {}
    lock = threading.Lock()
    errors: list[Exception] = []

    def worker(name):
        try:
            claim = q2.claim(name)
            if claim is not None:
                q2.complete(claim, f"done-by-{name}")
                with lock:
                    claimed[name] = claim.task.task_id
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)

    ws = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(2)]
    for w in ws:
        w.start()
    for w in ws:
        w.join()

    check(not errors, "no 'database is locked' or other error under concurrent independent work")
    check(set(claimed.values()) == {t1.task_id, t2.task_id},
          "both workers claimed and completed DIFFERENT tasks concurrently")
    check(q2.get(t1.task_id).status == TaskStatus.DONE
          and q2.get(t2.task_id).status == TaskStatus.DONE,
          "both tasks reached DONE (one worker's transition did not block the other)")
    q2.close()

    print("\nPASS: Phase 5.2 concurrency contract holds (per-worker connections).")


if __name__ == "__main__":
    main()
