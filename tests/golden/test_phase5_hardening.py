"""Phase 5 hardening golden tasks — audit findings #3..#7.

- #3 tool calls carry a per-call `call_id` (correlated across requested/completed/policy).
- #4 approval state is event-sourced (`ApprovalState.reconstruct`).
- #5 a normal exception reaches a terminal `step.failed` (vs. process death = none).
- #6 two recoverers racing to requeue the same task requeue it exactly once.
- #7 the dead event `TASK_CREATED` is removed; model-selection events are reserved.

Run:  py tests/golden/test_phase5_hardening.py
"""
import os
import sys
import tempfile
import threading
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import (  # noqa: E402
    ApprovalRequest, Event, ModelResponse, Risk, Task, TaskStatus, ToolCall,
    ToolResult, new_id, utcnow,
)
from core.events import EventType  # noqa: E402
from core.state import ApprovalState  # noqa: E402
from control.evaluator import Evaluator  # noqa: E402
from control.policy import PolicyEngine, PolicyRules  # noqa: E402
from control.tools import ToolRegistry, ToolSpec  # noqa: E402
from execution.approvals import ApprovalStore  # noqa: E402
from execution.durable import DurableEventBus  # noqa: E402
from execution.orchestrator import Orchestrator  # noqa: E402
from execution.queue import TaskQueue  # noqa: E402
from knowledge.inmemory import ComposedRetriever  # noqa: E402
from observability.trace import TraceProjector  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def _ev(event_type, payload=None, run_id="r1", task_id="t1"):
    return Event(event_id=new_id("evt"), event_type=event_type, timestamp=utcnow(),
                 run_id=run_id, task_id=task_id, component="x", status="success",
                 payload=payload or {})


class FailingTool:
    """Model proposes a tool; the tool raises (a normal, in-process exception)."""

    def run_model(self, request):
        if any(m.get("role") == "tool" for m in request.messages):
            return ModelResponse(model="fake", content="done", success=True)
        return ModelResponse(model="fake", content="", success=True, tool_calls=[
            ToolCall(tool_name="github.read_file", arguments={})])

    def execute_tool(self, call):
        raise RuntimeError("boom")


def main():
    print("Phase 5 hardening golden tasks: findings #3..#7")

    # --- #3: call identity ---------------------------------------------------
    projector = TraceProjector()
    trace = projector.project([
        _ev(EventType.TOOL_REQUESTED, {"tool": "github.read_file", "call_id": "c1"}),
        _ev(EventType.TOOL_REQUESTED, {"tool": "github.read_file", "call_id": "c2"}),
        _ev(EventType.TOOL_COMPLETED, {"tool": "github.read_file", "call_id": "c2", "success": True}),
    ])
    tools = [n for n in trace.nodes if n["type"] == "tool"]
    by_call = {n["call_id"]: n for n in tools}
    check(by_call["c1"]["completed"] is False and by_call["c1"]["interrupted"],
          "call c1 (no completed) is interrupted")
    check(by_call["c2"]["completed"] is True and by_call["c2"]["success"] is True,
          "call c2 pairs to its own completed (same name, different call -> no cross-pairing)")

    # --- #4: approval state is event-sourced --------------------------------
    events = [
        _ev(EventType.APPROVAL_REQUIRED, {"approval_id": "a1", "tool": "x", "risk": "write"}),
        _ev(EventType.APPROVAL_GRANTED, {"approval_id": "a1", "tool": "x"}),
        _ev(EventType.APPROVAL_CONSUMED, {"approval_id": "a1", "tool": "x"}),
    ]
    check(ApprovalState.reconstruct("a1", events).status == "consumed",
          "ApprovalState.reconstruct reproduces the approval lifecycle from events")

    # --- #5: terminal step semantics (normal exception -> step.failed) ------
    tmp = tempfile.mkdtemp()
    bus = DurableEventBus(os.path.join(tmp, "events.db"))
    retriever = ComposedRetriever()
    retriever.ingest("filesystem", "docs/x.md", "v1", "context")
    tools_reg = ToolRegistry()
    tools_reg.register(ToolSpec("github.read_file", "read a file", Risk.READ))
    orch = Orchestrator(retriever=retriever, executor=FailingTool(),
                        policy=PolicyEngine(PolicyRules()), tools=tools_reg,
                        evaluator=Evaluator(), bus=bus)
    try:
        orch.run(Task(task_id=new_id("task"), title="fix"))
    except RuntimeError:
        pass
    kinds = [e.event_type for e in bus.history]
    check(EventType.STEP_FAILED in kinds, "a normal exception reaches a terminal step.failed")
    check(EventType.TOOL_REQUESTED in kinds and EventType.TOOL_COMPLETED not in kinds,
          "the raising tool left tool.requested with no tool.completed (interrupted)")

    # --- #6: two recoverers requeue the same task exactly once --------------
    path = os.path.join(tempfile.mkdtemp(), "queue.db")
    q = TaskQueue(path)
    task = Task(task_id=new_id("task"), title="x")
    q.enqueue(task)
    q.claim("w1")  # CLAIMED, with a lease
    q.close()

    barrier = threading.Barrier(2)
    results = [None, None]
    lock = threading.Lock()

    def recover(idx):
        qq = TaskQueue(path)  # separate connection
        barrier.wait()
        r = qq.recover_abandoned(lease_seconds=1, now=utcnow() + timedelta(seconds=10))
        with lock:
            results[idx] = r
        qq.close()

    ts = [threading.Thread(target=recover, args=(i,)) for i in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    got = [r for r in results if r == [task.task_id]]
    empty = [r for r in results if r == []]
    check(len(got) == 1 and len(empty) == 1,
          "exactly one recoverer requeues the abandoned task (atomic conditional UPDATE)")

    # --- #7: dead event taxonomy -------------------------------------------
    check(not hasattr(EventType, "TASK_CREATED"), "TASK_CREATED is removed (superseded by task.queued)")
    check(hasattr(EventType, "MODEL_SELECTED") and hasattr(EventType, "MODEL_FALLBACK"),
          "MODEL_SELECTED / MODEL_FALLBACK are RESERVED (Phase 6 model selection)")

    print("\nPASS: Phase 5 hardening holds (findings #3..#7 resolved).")


if __name__ == "__main__":
    main()
