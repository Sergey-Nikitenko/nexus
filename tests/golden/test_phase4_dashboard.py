"""Phase 4.6 golden task — the disposable dashboard.

The dashboard is a pure projection consumer: it renders the projected trace's
state, decisions, attempts, recovery, and interruptions. It never recomputes
authority (the "why" comes from the durable policy.decision event) and never
executes a capability.

Run:  py tests/golden/test_phase4_dashboard.py
"""
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
from apps.dashboard import build_view, why  # noqa: E402
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
    print("Phase 4.6 golden task: the disposable dashboard")
    projector = TraceProjector()

    # 1. live rendering: a real FAIL->REPLAN->PASS run (allow + deny) reaches terminal
    view = build_view(projector.project(run_replan_task().events))
    check(view["status"] == "completed", "the dashboard reaches the terminal status")
    check(any("plan" in m for m in view["milestones"])
          and any("retrieve" in m for m in view["milestones"]),
          "the live-run milestones list the loop phases")

    # 2. Why panel: decisions render the projected fields, never recompute them
    read = [d for d in view["decisions"] if d["tool"] == "github.read_file"][0]
    check(read["risk"] == "read" and read["verdict"] == "allow" and read["executed"],
          "READ/ALLOW decision renders without recomputation")
    check("allows" in read["reason"], "the decision carries its OWN reason (from the event)")
    push = [d for d in view["decisions"] if d["tool"] == "github.force_push"][0]
    check(push["risk"] == "destructive" and push["verdict"] == "deny" and not push["executed"],
          "DESTRUCTIVE/DENY decision renders without recomputation")
    w = why(read)
    check(w["tool"] == "github.read_file" and w["verdict"] == "allow",
          "why() renders the projected decision")

    # 3. replan: attempt 1 and attempt 2 are distinct
    check([a["attempt"] for a in view["attempts"]] == [1, 2],
          "attempt history distinguishes attempt 1 and attempt 2")
    check(view["attempts"][0]["passed"] is False and view["attempts"][1]["passed"] is True,
          "attempt 1 FAIL and attempt 2 PASS are distinct")

    # 4. recovery: claimed -> requeued -> claimed -> completed remains visible
    rview = build_view(projector.project([
        _ev(EventType.TASK_CLAIMED, {"worker_id": "w1"}, run_id=""),
        _ev(EventType.TASK_REQUEUED, {}, run_id=""),
        _ev(EventType.TASK_CLAIMED, {"worker_id": "w2"}, run_id=""),
        _ev(EventType.TASK_COMPLETED, {"answer": "done"}, run_id=""),
    ]))
    check([r["event"] for r in rview["recovery"]]
          == ["claimed", "requeued", "claimed", "completed"],
          "recovery (claimed -> requeued -> claimed -> completed) is visible")

    # 5. interruption: tool.requested without tool.completed -> interrupted
    iview = build_view(projector.project([_ev(EventType.TOOL_REQUESTED, {"tool": "github.read_file"})]))
    check([i["tool"] for i in iview["interrupted"]] == ["github.read_file"],
          "tool.requested without tool.completed renders as interrupted/incomplete")

    # 6. provider neutrality + event detail (the details drawer)
    allowed_keys = {"tool", "risk", "verdict", "executed", "reason", "event_id",
                    "parent_event_id", "run_id", "task_id", "timestamp"}
    check(all(set(d.keys()) <= allowed_keys for d in view["decisions"]),
          "the browser payload uses Nexus vocabulary only (no provider terms)")
    check(all("event_id" in d and "timestamp" in d and "run_id" in d and "task_id" in d
              for d in view["decisions"]),
          "every decision carries the durable event identity (details drawer)")

    print("\nPASS: Phase 4.6 dashboard holds (disposable presentation, no authority).")


if __name__ == "__main__":
    main()
