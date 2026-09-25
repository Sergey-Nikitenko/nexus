"""Phase 3.5 golden task — policy stays authoritative across replans.

A model suggests a DESTRUCTIVE tool on attempt 1 (denied), the evaluation asks
for a replan, and the model suggests the SAME destructive tool on attempt 2.
The replan must change the plan, not the authority: the destructive tool is
denied on BOTH attempts and never executes.

Run:  py tests/golden/test_phase3_replan_policy.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import (  # noqa: E402
    Evaluation, ModelResponse, Risk, Task, TaskStatus, ToolCall, new_id,
)
from core.events import EventType  # noqa: E402
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


def main():
    print("Phase 3.5 golden task: policy stays authoritative across replans")
    retriever = ComposedRetriever()
    retriever.ingest("filesystem", "docs/repo.md", "v1", "the repository has commits")

    tools = ToolRegistry()
    tools.register(ToolSpec("github.force_push", "force push", Risk.DESTRUCTIVE))
    policy = PolicyEngine(PolicyRules())  # destructive -> DENY

    script = [
        ModelResponse(model="fake", content="", success=True,
                      tool_calls=[ToolCall("github.force_push", {})]),
        ModelResponse(model="fake", content="attempt 1 answer", success=True),
        ModelResponse(model="fake", content="", success=True,
                      tool_calls=[ToolCall("github.force_push", {})]),
        ModelResponse(model="fake", content="attempt 2 answer", success=True),
    ]
    evaluator = FakeEvaluator([
        Evaluation(passed=False, reason="try again", replan_required=True),
        Evaluation(passed=True, reason="done"),
    ])

    orchestrator = Orchestrator(
        retriever=retriever, executor=FakeExecutor(model_script=script),
        policy=policy, tools=tools, evaluator=evaluator, max_replans=2)
    outcome = orchestrator.run(Task(task_id=new_id("task"), title="force push the fix"))
    events = outcome.events

    # the loop ran two attempts, and the destructive tool was never executed
    requested = [e for e in events if e.event_type == EventType.TOOL_REQUESTED]
    check(requested == [], "the DESTRUCTIVE tool never executed, on any attempt")
    denies = [n for n in outcome.trace.nodes
              if n["type"] == "tool" and n["verdict"] == "deny"]
    check(len(denies) == 2 and all(n["tool"] == "github.force_push" for n in denies),
          "the destructive tool was DENIED on both attempts (one deny per attempt)")
    check(outcome.live_state.replan_count == 1, "replanning still happened")
    check(outcome.answer == "attempt 2 answer" and outcome.live_state.task_status == TaskStatus.DONE,
          "the run still completed on the passing attempt")

    print("\nPASS: replanning changes the plan, never the authority.")


if __name__ == "__main__":
    main()
