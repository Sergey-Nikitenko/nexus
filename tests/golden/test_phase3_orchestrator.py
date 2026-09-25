"""Phase 3.4 golden task — the agent loop, proven with deterministic capabilities.

Given only FakeExecutor + the reference retriever + state + an event bus, a task
runs the full loop: Task -> plan -> retrieve -> model -> tool (policy-gated) ->
model -> verify -> answer. Three things are proven:

1. Complete deterministic execution (Task -> Answer, no real infrastructure).
2. Causally ordered events (run.started ... run.completed).
3. State/event consistency (state rebuilt from events == state from live execution).

And, crucially, the Phase 1 policy stays authoritative INSIDE the loop: a
model-suggested READ tool executes, a WRITE tool is approval-gated (not
executed), and a DESTRUCTIVE tool is denied (not executed).

Run:  py tests/golden/test_phase3_orchestrator.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import ModelResponse, Risk, Task, TaskStatus, ToolCall, new_id  # noqa: E402
from core.events import EventType  # noqa: E402
from core.state import RunState  # noqa: E402
from control.evaluator import Evaluator  # noqa: E402
from control.policy import PolicyEngine, PolicyRules  # noqa: E402
from control.tools import ToolRegistry, ToolSpec  # noqa: E402
from execution.fake import FakeExecutor  # noqa: E402
from execution.orchestrator import Orchestrator  # noqa: E402
from knowledge.inmemory import ComposedRetriever  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def indices(events, event_type):
    return [i for i, e in enumerate(events) if e.event_type == event_type]


def build_orchestrator():
    retriever = ComposedRetriever()
    retriever.ingest("filesystem", "docs/middleware.md", "v1",
                     "the authentication middleware has a bug in token verification")

    script = [
        ModelResponse(model="fake", content="", success=True, tool_calls=[
            ToolCall(tool_name="github.read_file", arguments={"path": "middleware.py"}),
            ToolCall(tool_name="github.create_pr", arguments={"title": "fix auth"}),
            ToolCall(tool_name="github.force_push", arguments={}),
        ]),
        ModelResponse(model="fake", content="authentication bug fixed", success=True),
    ]

    tools = ToolRegistry()
    tools.register(ToolSpec("github.read_file", "read a file", Risk.READ))
    tools.register(ToolSpec("github.create_pr", "open a PR", Risk.WRITE))
    tools.register(ToolSpec("github.force_push", "force push", Risk.DESTRUCTIVE))

    policy = PolicyEngine(PolicyRules())  # read=ALLOW, write=APPROVAL_REQUIRED, destructive=DENY
    return Orchestrator(retriever=retriever, executor=FakeExecutor(model_script=script),
                        policy=policy, tools=tools, evaluator=Evaluator())


def main():
    print("Phase 3.4 golden task: the orchestrator coordinates; capabilities execute")
    orchestrator = build_orchestrator()
    task = Task(task_id=new_id("task"), title="fix authentication bug")

    outcome = orchestrator.run(task)
    events = outcome.events

    # 1. complete deterministic execution: Task -> Answer
    check(outcome.answer == "authentication bug fixed", "Task -> Answer (no real infrastructure)")
    types = [n["type"] for n in outcome.trace.nodes]
    check(types[0] == "plan" and "retrieve" in types and "model" in types
          and "tool" in types and "verify" in types and types[-1] == "answer",
          "the trace records plan -> retrieve -> model -> tool -> verify -> answer")

    # 2. causally ordered events
    check(indices(events, EventType.RUN_MANIFEST) == [0]
          and indices(events, EventType.RUN_STARTED) == [1],
          "run.manifest precedes run.started (inputs captured before execution)")
    check(indices(events, EventType.RUN_COMPLETED) == [len(events) - 1],
          "run.completed is the last event")
    check(indices(events, EventType.RETRIEVAL_REQUESTED)[0]
          < indices(events, EventType.RETRIEVAL_COMPLETED)[0],
          "retrieval.requested precedes retrieval.completed")
    m_req = indices(events, EventType.MODEL_REQUESTED)
    m_done = indices(events, EventType.MODEL_COMPLETED)
    check(len(m_req) == 2 and len(m_done) == 2 and all(
        m_req[i] < m_done[i] for i in range(2)),
        "each model.requested precedes its model.completed (two model calls)")
    t_req = indices(events, EventType.TOOL_REQUESTED)
    t_done = indices(events, EventType.TOOL_COMPLETED)
    check(len(t_req) == 1 and len(t_done) == 1 and t_req[0] < t_done[0],
          "exactly one tool execution (requested precedes completed)")

    # 3. policy stays authoritative INSIDE the loop
    requested_tools = [e.payload.get("tool") for e in events
                       if e.event_type == EventType.TOOL_REQUESTED]
    check(requested_tools == ["github.read_file"],
          "only the READ tool executed; WRITE and DESTRUCTIVE were gated")
    approvals = [e.payload.get("tool") for e in events
                 if e.event_type == EventType.APPROVAL_REQUIRED]
    check(approvals == ["github.create_pr"],
          "the WRITE tool raised approval.required and was NOT executed")
    verdicts = {n["tool"]: n["verdict"] for n in outcome.trace.nodes if n["type"] == "tool"}
    check(verdicts["github.read_file"] == "allow"
          and verdicts["github.create_pr"] == "approval_required"
          and verdicts["github.force_push"] == "deny",
          "the trace records allow / approval_required / deny per tool")

    # 4. state/event consistency (reconnects to the Phase 0 recovery guarantee)
    rebuilt = RunState.reconstruct(outcome.run, events)
    check(rebuilt == outcome.live_state,
          "state rebuilt from events == state from live execution")
    check(rebuilt.task_status == TaskStatus.DONE, "task status recovered as done")
    check(len(rebuilt.completed_steps) == len(outcome.run.steps),
          "every step recovered as completed from the event log")

    print("\nPASS: Phase 3.4 orchestration holds (Nexus's central nervous system).")


if __name__ == "__main__":
    main()
