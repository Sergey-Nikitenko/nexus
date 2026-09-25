"""Phase 5 system golden task — all layers, simultaneously and concurrently.

The individual layer tests and the cross-layer golden tasks already exist in the
suite. This is the missing angle: MULTIPLE workers running FULL tasks end-to-end
through ONE shared system (core -> control -> execution -> knowledge ->
observability), concurrently, and the system stays correct under load:

- distinct READ tasks drain concurrently through the full loop;
- a WRITE task pauses for approval, is approved, and re-runs to DONE (single-use);
- a BOOM task fails durably without corrupting its neighbors;
- every execution attempt carries a unique Nexus-owned call_id ACROSS workers;
- the durable log reconstructs each task and never cross-contaminates.

Run:  py tests/golden/test_phase5_system.py
"""
import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import ModelResponse, Risk, Task, TaskStatus, ToolCall, ToolResult, new_id  # noqa: E402
from core.events import EventType  # noqa: E402
from control.evaluator import Evaluator  # noqa: E402
from control.policy import PolicyEngine, PolicyRules  # noqa: E402
from control.tools import ToolRegistry, ToolSpec  # noqa: E402
from execution.approvals import ApprovalStore  # noqa: E402
from execution.durable import DurableEventBus  # noqa: E402
from execution.orchestrator import Orchestrator  # noqa: E402
from execution.queue import TaskQueue  # noqa: E402
from execution.worker import Worker  # noqa: E402
from knowledge.inmemory import ComposedRetriever  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


class ConcurrentExecutor:
    """Stateless + thread-safe: keys the proposed tool off the task title so one
    shared executor can exercise READ (allow), WRITE (approval), and BOOM (raise)
    without any shared mutable script."""

    def run_model(self, request):
        if any(m.get("role") == "tool" for m in request.messages):
            return ModelResponse(model="fake", content="done", success=True)
        title = (request.messages[-1]["content"] if request.messages else "").lower()
        if "write" in title:
            tool = ToolCall(tool_name="github.create_pr", arguments={"title": title})
        else:
            tool = ToolCall(tool_name="github.read_file",
                            arguments={"path": "boom" if "boom" in title else "x.py"})
        return ModelResponse(model="fake", content="", success=True, tool_calls=[tool])

    def execute_tool(self, call):
        if call.arguments.get("path") == "boom":
            raise RuntimeError("boom")  # a normal in-process failure
        return ToolResult(tool_call=call, success=True, output={"ok": True})


def _build_system(tmp):
    bus = DurableEventBus(os.path.join(tmp, "events.db"))
    queue = TaskQueue(os.path.join(tmp, "queue.db"), bus=bus)
    approvals = ApprovalStore(os.path.join(tmp, "approvals.db"), bus=bus)
    retriever = ComposedRetriever()
    retriever.ingest("filesystem", "docs/x.md", "v1", "context for the task")
    tools = ToolRegistry()
    tools.register(ToolSpec("github.read_file", "read a file", Risk.READ))
    tools.register(ToolSpec("github.create_pr", "open a PR", Risk.WRITE))
    orchestrator = Orchestrator(
        retriever=retriever, executor=ConcurrentExecutor(),
        policy=PolicyEngine(PolicyRules()), tools=tools,
        evaluator=Evaluator(), bus=bus, approvals=approvals)
    return bus, queue, approvals, orchestrator


def _drain(workers, cap, barrier):
    """Run `workers` concurrently, each looping claim->run until the queue is
    empty (or `cap` iterations). A task that raises is durably failed by the
    worker before the exception propagates; the loop swallows it and continues."""
    errors: list[str] = []
    lock = threading.Lock()

    def loop(w):
        barrier.wait()
        for _ in range(cap):
            try:
                if w.run_one() is None:
                    return
            except Exception as exc:  # noqa: BLE001 — durably recorded by the worker
                with lock:
                    errors.append(repr(exc))

    threads = [threading.Thread(target=loop, args=(w,)) for w in workers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return errors


def _approval_ids(events):
    return [e.payload["approval_id"] for e in events
            if e.event_type == EventType.APPROVAL_REQUIRED]


def main():
    print("Phase 5 system golden task: all layers, concurrently")

    tmp = tempfile.mkdtemp()
    bus, queue, approvals, orchestrator = _build_system(tmp)

    # enqueue a mix: READ (allow), WRITE (approval), BOOM (raise)
    titles = [f"read task {i}" for i in range(6)] + \
             [f"write task {i}" for i in range(2)] + \
             ["boom task 0", "boom task 1"]
    task_ids = []
    for title in titles:
        task = Task(task_id=new_id("task"), title=title)
        queue.enqueue(task)
        task_ids.append(task.task_id)

    n_workers = 4
    workers = [Worker(worker_id=f"w{i}", queue=queue, orchestrator=orchestrator)
               for i in range(n_workers)]

    # phase 1: drain concurrently (READ -> DONE, BOOM -> FAILED, WRITE -> waiting)
    errs = _drain(workers, cap=len(titles) + 2, barrier=threading.Barrier(n_workers))
    locked = [e for e in errs if "locked" in e.lower() or "database" in e.lower()]
    check(not locked and all("boom" in e for e in errs),
          "no 'database is locked'; the only failures are the expected BOOM tasks")

    # approve + requeue the WRITE tasks, then drain again to DONE
    write_ids = [t for t in task_ids if queue.get(t).status == TaskStatus.AWAITING_APPROVAL]
    check(len(write_ids) == 2, "the two WRITE tasks paused durably for approval")
    for tid in write_ids:
        aid = _approval_ids(bus.load_events(task_id=tid))[0]
        approvals.approve(aid)
        queue.requeue(tid)
    errs2 = _drain(workers, cap=len(write_ids) + 2, barrier=threading.Barrier(n_workers))
    check(not errs2, "no error during the post-approval re-drain (WRITE tasks succeed)")

    # 1. every task reached its expected terminal state
    statuses = {tid: queue.get(tid).status for tid in task_ids}
    read_ok = all(statuses[t] == TaskStatus.DONE for t in task_ids[:6])
    write_ok = all(statuses[t] == TaskStatus.DONE for t in write_ids)
    boom_ok = all(statuses[t] == TaskStatus.FAILED for t in task_ids[-2:])
    check(read_ok and write_ok and boom_ok,
          "every task reached its expected terminal state (READ/WRITE -> DONE, BOOM -> FAILED)")

    # 2. per-attempt call_id uniqueness across ALL workers
    all_events = bus.load_events()
    call_ids = [e.payload.get("call_id") for e in all_events
                if e.event_type == EventType.TOOL_REQUESTED]
    check(len(call_ids) == len(set(call_ids)) and None not in call_ids,
          "every tool execution across every worker got a DISTINCT Nexus-owned call_id")

    # 3. each WRITE tool executed exactly once (approval single-use, end-to-end)
    for tid in write_ids:
        completed = [e for e in bus.load_events(task_id=tid)
                     if e.event_type == EventType.TOOL_COMPLETED
                     and e.payload.get("tool") == "github.create_pr"]
        check(len(completed) == 1, "a WRITE task's tool executed exactly once")
    check(all(approvals.get(_approval_ids(bus.load_events(task_id=t))[0]).status == "consumed"
              for t in write_ids),
          "every WRITE approval reached CONSUMED")

    # 4. no cross-contamination: a run_id never appears in two tasks' logs
    run_to_task: dict[str, str] = {}
    for tid in task_ids:
        for e in bus.load_events(task_id=tid):
            if e.run_id:
                if e.run_id in run_to_task and run_to_task[e.run_id] != tid:
                    raise AssertionError(f"run {e.run_id} leaked across tasks")
                run_to_task[e.run_id] = tid
    check(True, "no run_id leaked across tasks (durable log is cleanly partitioned)")

    # 5. the durable log is the recovery substrate: each DONE task reconstructs
    for tid in task_ids[:6] + write_ids:
        seq = [e.event_type for e in bus.load_events(task_id=tid)]
        check(EventType.RUN_STARTED in seq and EventType.RUN_COMPLETED in seq,
              "a DONE task's durable log reconstructs start -> completed")

    bus.close()
    queue.close()
    approvals.close()
    print("\nPASS: Phase 5 system holds under concurrent full-stack execution.")


if __name__ == "__main__":
    main()
