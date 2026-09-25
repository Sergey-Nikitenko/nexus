"""Phase 6.4 golden task — multi-worker arbitration.

Phase 5 proved task-level ownership; 6.4 asks the NEXT question: when workers run
concurrently, which run is AUTHORITATIVE for a task, and is that decision durable?

The new invariant (AD-032): a run's terminal fingerprint is published only by the
claim generation that is CURRENT at completion time. So:

- a stale worker (whose lease expired and whose task was re-claimed) cannot
  publish a terminal run record — its run keeps a manifest but a NULL fingerprint;
- recovery produces exactly ONE authoritative (terminal) run per task;
- run records stay partitioned by run_id, never cross-contaminating tasks.

Run:  py tests/golden/test_phase6_arbitration.py
"""
import os
import sys
import tempfile
import threading
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import ModelResponse, Risk, Task, ToolCall, ToolResult, new_id, utcnow  # noqa: E402
from core.events import EventBus, EventType  # noqa: E402
from control.evaluator import Evaluator  # noqa: E402
from control.policy import PolicyEngine, PolicyRules  # noqa: E402
from control.tools import ToolRegistry, ToolSpec  # noqa: E402
from execution.orchestrator import Orchestrator  # noqa: E402
from execution.queue import TaskQueue  # noqa: E402
from execution.run_records import RunRecordStore  # noqa: E402
from execution.worker import Worker  # noqa: E402
from knowledge.inmemory import ComposedRetriever  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


class SlowOnceExecutor:
    """Proposes a READ tool; the FIRST execution blocks until released, so w1 can
    be made stale (recovered) while it is still mid-run."""

    def __init__(self):
        self.blocked = threading.Event()
        self.release = threading.Event()
        self._calls = 0

    def run_model(self, request):
        if any(m.get("role") == "tool" for m in request.messages):
            return ModelResponse(model="fake", content="done", success=True)
        return ModelResponse(model="fake", content="", success=True, tool_calls=[
            ToolCall(tool_name="github.read_file", arguments={"path": "x.py"})])

    def execute_tool(self, call):
        self._calls += 1
        if self._calls == 1:
            self.blocked.set()
            self.release.wait(timeout=10)
        return ToolResult(tool_call=call, success=True, output={"ok": True})


def build_orchestrator(records, bus, executor):
    retriever = ComposedRetriever()
    retriever.ingest("filesystem", "docs/x.md", "v1", "context")
    tools = ToolRegistry()
    tools.register(ToolSpec("github.read_file", "read a file", Risk.READ))
    return Orchestrator(retriever=retriever, executor=executor,
                        policy=PolicyEngine(PolicyRules()), tools=tools,
                        evaluator=Evaluator(), bus=bus, run_records=records)


def main():
    print("Phase 6.4 golden task: multi-worker arbitration")
    tmp = tempfile.mkdtemp()

    # --- 1. a stale worker cannot publish a terminal run record -------------
    records = RunRecordStore(os.path.join(tmp, "records.db"))
    bus = EventBus()
    executor = SlowOnceExecutor()
    orch = build_orchestrator(records, bus, executor)
    queue = TaskQueue(os.path.join(tmp, "queue.db"))
    w1 = Worker(worker_id="w1", queue=queue, orchestrator=orch, run_records=records)
    w2 = Worker(worker_id="w2", queue=queue, orchestrator=orch, run_records=records)

    task = Task(task_id=new_id("task"), title="read")
    queue.enqueue(task)

    errors: list[Exception] = []

    def w1_run():
        try:
            w1.run_one()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=w1_run)
    t1.start()
    check(executor.blocked.wait(timeout=5), "w1 is mid-run (claim generation 1)")

    # recovery reclaims the task; w2 claims the NEXT generation and completes
    queue.recover_abandoned(lease_seconds=1, now=utcnow() + timedelta(seconds=10))
    outcome_w2 = w2.run_one()
    executor.release.set()
    t1.join()

    check(not errors, "no unexpected error during the race")
    rows = records.for_task(task.task_id)
    check(len(rows) == 2, "two runs were recorded (w1's abandoned + w2's authoritative)")
    terminal = [r for r in rows if r["fingerprint"] is not None]
    check(len(terminal) == 1, "exactly ONE authoritative (terminal) run")
    check(terminal[0]["run_id"] == outcome_w2.run.run_id,
          "the authoritative run is w2's (the current generation)")
    stale = [r for r in rows if r["fingerprint"] is None]
    check(len(stale) == 1 and stale[0]["manifest"] is not None,
          "w1's stale run keeps its manifest but NO terminal fingerprint")

    # --- 2. run records remain partitioned by run_id across workers ---------
    tmp2 = tempfile.mkdtemp()
    records2 = RunRecordStore(os.path.join(tmp2, "records.db"))
    bus2 = EventBus()
    orch2 = build_orchestrator(records2, bus2, SlowOnceExecutor())
    queue2 = TaskQueue(os.path.join(tmp2, "queue.db"))
    ta = Task(task_id=new_id("task"), title="read a")
    tb = Task(task_id=new_id("task"), title="read b")
    queue2.enqueue(ta)
    queue2.enqueue(tb)

    workers = [Worker(worker_id=f"c{i}", queue=queue2, orchestrator=orch2,
                      run_records=records2) for i in range(2)]
    barrier = threading.Barrier(2)

    def drain(w):
        barrier.wait()
        while w.run_one() is not None:
            pass

    threads = [threading.Thread(target=drain, args=(w,)) for w in workers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ra = records2.for_task(ta.task_id)
    rb = records2.for_task(tb.task_id)
    check(len(ra) == 1 and len(rb) == 1 and ra[0]["fingerprint"] is not None
          and rb[0]["fingerprint"] is not None,
          "each task has exactly one terminal run record")
    check(ra[0]["run_id"] != rb[0]["run_id"] and ra[0]["task_id"] == ta.task_id
          and rb[0]["task_id"] == tb.task_id,
          "run records are partitioned by run_id and never cross-contaminate tasks")

    records.close()
    records2.close()
    print("\nPASS: Phase 6.4 multi-worker arbitration holds.")


if __name__ == "__main__":
    main()
