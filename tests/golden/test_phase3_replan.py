"""Phase 3.5 golden task — verification + bounded replanning.

One deterministic task proves the controlled feedback loop:

    attempt 1 -> tool result -> evaluation FAIL -> REPLAN -> attempt 2 ->
    tool result -> evaluation PASS -> completed

with three invariants:
1. Verification is explicit: the tool SUCCEEDS on attempt 1, but the evaluation
   still FAILS (tool success != task success).
2. Replanning is bounded: max_replans is part of the plan; exhaustion is terminal.
3. Every attempt is observable and reconstructible from the event log.

Run:  py tests/golden/test_phase3_replan.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import (  # noqa: E402
    Evaluation, ModelResponse, Risk, Task, TaskStatus, ToolCall, new_id,
)
from core.events import EventType  # noqa: E402
from core.state import RunState  # noqa: E402
from control.evaluator import FakeEvaluator  # noqa: E402
from control.policy import PolicyEngine, PolicyRules  # noqa: E402
from control.tools import ToolRegistry, ToolSpec  # noqa: E402
from execution.fake import FakeExecutor  # noqa: E402
from execution.orchestrator import Orchestrator  # noqa: E402
from knowledge.inmemory import ComposedRetriever  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def build(model_script, evaluator, max_replans=2):
    retriever = ComposedRetriever()
    retriever.ingest("filesystem", "docs/middleware.md", "v1",
                     "the authentication middleware has a bug in token verification")
    tools = ToolRegistry()
    tools.register(ToolSpec("github.read_file", "read a file", Risk.READ))
    policy = PolicyEngine(PolicyRules())
    return Orchestrator(retriever=retriever, executor=FakeExecutor(model_script=model_script),
                        policy=policy, tools=tools, evaluator=evaluator,
                        max_replans=max_replans)


def main():
    print("Phase 3.5 golden task: verification + bounded replanning")
    script = [
        ModelResponse(model="fake", content="", success=True, tool_calls=[
            ToolCall(tool_name="github.read_file", arguments={"path": "middleware.py"})]),
        ModelResponse(model="fake", content="attempt 1 answer", success=True),
        ModelResponse(model="fake", content="", success=True, tool_calls=[
            ToolCall(tool_name="github.read_file", arguments={"path": "middleware.py"})]),
        ModelResponse(model="fake", content="attempt 2 answer", success=True),
    ]
    evaluator = FakeEvaluator([
        Evaluation(passed=False, reason="not fixed yet", replan_required=True),
        Evaluation(passed=True, reason="fixed"),
    ])

    outcome = build(script, evaluator, max_replans=2).run(
        Task(task_id=new_id("task"), title="fix authentication bug"))
    events = outcome.events

    # the loop completes with the passing attempt's answer
    check(outcome.answer == "attempt 2 answer", "attempt 2's answer is the final answer")
    check(outcome.live_state.task_status == TaskStatus.DONE, "task completed")

    # invariant 1: verification is explicit — tool success != task success
    tool_done = [e for e in events if e.event_type == EventType.TOOL_COMPLETED]
    check(len(tool_done) == 2 and all(e.payload.get("success") for e in tool_done),
          "the tool SUCCEEDED on every attempt")
    evals = outcome.live_state.evaluations
    check(evals[0]["passed"] is False and evals[1]["passed"] is True,
          "but the evaluation FAILED then PASSED (tool success != task success)")

    # invariant 2: replanning is bounded and the budget is run/plan state
    replans = [e for e in events if e.event_type == EventType.RUN_REPLANNED]
    check(len(replans) == 1 and outcome.live_state.replan_count == 1,
          "exactly one replan, recorded in run state")
    plan = [n for n in outcome.trace.nodes if n["type"] == "plan"][0]
    check(plan["max_replans"] == 2, "max_replans is part of the plan, not a local counter")

    # invariant 3: every attempt observable + reconstructible from events
    eval_events = [e for e in events if e.event_type == EventType.EVALUATION_COMPLETED]
    check(len(eval_events) == 2, "both attempts' evaluations are events")
    rebuilt = RunState.reconstruct(outcome.run, events)
    check(rebuilt == outcome.live_state,
          "state rebuilt from events == state from live execution (attempt history included)")
    check(rebuilt.replan_count == 1 and len(rebuilt.evaluations) == 2,
          "reconstructed replan_count + evaluation history reproduce the attempt trail")

    # budget exhaustion is terminal, never an infinite loop
    always_fail = FakeEvaluator(default=Evaluation(passed=False, reason="broken",
                                                   replan_required=True))
    fail_script = [
        ModelResponse(model="fake", content="", success=True,
                      tool_calls=[ToolCall("github.read_file", {"path": "a.py"})]),
        ModelResponse(model="fake", content="x", success=True),
        ModelResponse(model="fake", content="", success=True,
                      tool_calls=[ToolCall("github.read_file", {"path": "a.py"})]),
        ModelResponse(model="fake", content="x", success=True),
    ]
    fail_out = build(fail_script, always_fail, max_replans=1).run(
        Task(task_id=new_id("task"), title="x"))
    check(fail_out.live_state.task_status == TaskStatus.FAILED,
          "budget exhaustion is a terminal outcome (FAILED, not an infinite loop)")
    check(fail_out.live_state.replan_count == 1,
          "replan count is bounded by max_replans")

    print("\nPASS: Phase 3.5 verification + replanning holds.")


if __name__ == "__main__":
    main()
