"""Phase 4.3 golden task — the trace projector.

The projector derives a core.Trace from the durable event stream: deterministic,
reproducible, non-mutating, and it preserves causality, decisions (policy
verdicts), replans as attempts, incomplete operations, and recovery transitions.

Run:  py tests/golden/test_phase4_trace.py
"""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import (  # noqa: E402
    Evaluation, Event, ModelResponse, Risk, Task, ToolCall, new_id, utcnow,
)
from core.events import EventType  # noqa: E402
from control.evaluator import FakeEvaluator  # noqa: E402
from control.policy import PolicyEngine, PolicyRules  # noqa: E402
from control.tools import ToolRegistry, ToolSpec  # noqa: E402
from execution.fake import FakeExecutor  # noqa: E402
from execution.orchestrator import Orchestrator  # noqa: E402
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


def run_replan_task():
    retriever = ComposedRetriever()
    retriever.ingest("filesystem", "docs/x.md", "v1", "context")
    tools = ToolRegistry()
    tools.register(ToolSpec("github.read_file", "read a file", Risk.READ))
    tools.register(ToolSpec("github.force_push", "force push", Risk.DESTRUCTIVE))
    script = [
        ModelResponse(model="fake", content="", success=True, tool_calls=[
            ToolCall(tool_name="github.read_file", arguments={"path": "a.py"}),
            ToolCall(tool_name="github.force_push", arguments={}),
        ]),
        ModelResponse(model="fake", content="attempt 1", success=True),
        ModelResponse(model="fake", content="", success=True, tool_calls=[
            ToolCall(tool_name="github.read_file", arguments={"path": "a.py"}),
        ]),
        ModelResponse(model="fake", content="done", success=True),
    ]
    evaluator = FakeEvaluator([
        Evaluation(passed=False, reason="not fixed", replan_required=True),
        Evaluation(passed=True, reason="fixed"),
    ])
    orchestrator = Orchestrator(
        retriever=retriever, executor=FakeExecutor(model_script=script),
        policy=PolicyEngine(PolicyRules()), tools=tools, evaluator=evaluator)
    return orchestrator.run(Task(task_id=new_id("task"), title="fix the thing"))


def main():
    print("Phase 4.3 golden task: the trace projector")
    projector = TraceProjector()

    # 1. a real FAIL -> REPLAN -> PASS run (with an allowed + a denied tool)
    outcome = run_replan_task()
    events = outcome.events
    snapshot = copy.deepcopy(events)
    trace = projector.project(events)
    check(isinstance(trace.nodes, list), "the projector returns the core.Trace contract")

    # reproducible + non-mutating
    check(projector.project(events) == trace, "projection is deterministic/reproducible")
    check(events == snapshot, "projection does not modify the events (derived, not a second source)")

    types = {n["type"] for n in trace.nodes}
    check({"run", "plan", "retrieval", "model_request", "model_response",
           "decision", "tool", "evaluation", "replan"} <= types,
          "the trace has run/plan/retrieval/model/decision/tool/evaluation/replan nodes")

    # decisions are distinguishable from execution (the future Why panel)
    decisions = [n for n in trace.nodes if n["type"] == "decision"]
    read = [d for d in decisions if d["tool"] == "github.read_file"]
    push = [d for d in decisions if d["tool"] == "github.force_push"]
    check(any(d["verdict"] == "allow" and d["executed"] and d["risk"] == "read" for d in read),
          "the READ tool: proposed -> allowed -> executed")
    check(any(d["verdict"] == "deny" and not d["executed"] and d["risk"] == "destructive" for d in push),
          "the DESTRUCTIVE tool: proposed -> denied -> NOT executed")

    # replans appear as distinct attempts
    evals = [n for n in trace.nodes if n["type"] == "evaluation"]
    check(any(e["attempt"] == 1 and e["passed"] is False for e in evals)
          and any(e["attempt"] == 2 and e["passed"] is True for e in evals),
          "attempt 1 FAIL and attempt 2 PASS are distinct")
    check(any(n["type"] == "replan" and n["attempt"] == 2 for n in trace.nodes),
          "the REPLAN transition is visible")

    # causality survives
    check(all("event_id" in n and "parent_event_id" in n for n in trace.nodes),
          "every node preserves event_id + parent_event_id")

    # 2. an incomplete operation is explicit, never a manufactured success
    partial = projector.project([_ev(EventType.TOOL_REQUESTED, {"tool": "github.read_file"})])
    tool_nodes = [n for n in partial.nodes if n["type"] == "tool"]
    check(len(tool_nodes) == 1 and tool_nodes[0]["completed"] is False
          and tool_nodes[0]["interrupted"] is True,
          "tool.requested with no tool.completed -> interrupted, not success")

    # 3. recovery transitions are visible
    recovery = projector.project([
        _ev(EventType.TASK_CLAIMED, {"worker_id": "w1"}, run_id=""),
        _ev(EventType.TASK_REQUEUED, {}, run_id=""),
        _ev(EventType.TASK_CLAIMED, {"worker_id": "w2"}, run_id=""),
        _ev(EventType.TASK_COMPLETED, {"answer": "done"}, run_id=""),
    ])
    rtypes = [n["type"] for n in recovery.nodes]
    check(rtypes == ["task_claimed", "task_requeued", "task_claimed", "task_completed"],
          "the recovery transition (claimed -> requeued -> reclaimed -> completed) is visible")

    print("\nPASS: Phase 4.3 trace projection holds.")


if __name__ == "__main__":
    main()
