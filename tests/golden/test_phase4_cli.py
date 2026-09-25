"""Phase 4.7 golden task — the thin CLI.

The CLI is another surface adapter over NexusRuntime. `ask` is asynchronous
(same as HTTP), and `task` / `trace` render the SAME projected information the
dashboard uses. REST, WebSocket, the dashboard, and the CLI are four views of
the same contracts and events.

Run:  py tests/golden/test_phase4_cli.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import (  # noqa: E402
    Evaluation, ModelResponse, Risk, TaskStatus, ToolCall, ToolResult,
)
from core.events import EventType  # noqa: E402
from control.evaluator import Evaluator, FakeEvaluator  # noqa: E402
from control.policy import PolicyEngine, PolicyRules  # noqa: E402
from control.tools import ToolRegistry, ToolSpec  # noqa: E402
from execution.approvals import ApprovalStore  # noqa: E402
from execution.durable import DurableEventBus  # noqa: E402
from execution.fake import FakeExecutor  # noqa: E402
from execution.queue import TaskQueue  # noqa: E402
from apps.cli import dispatch  # noqa: E402
from apps.runtime import NexusRuntime  # noqa: E402
from knowledge.inmemory import ComposedRetriever  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


class ProposeWrite:
    def run_model(self, request):
        if any(m.get("role") == "tool" for m in request.messages):
            return ModelResponse(model="fake", content="done", success=True)
        return ModelResponse(model="fake", content="", success=True, tool_calls=[
            ToolCall(tool_name="github.create_pr", arguments={})])

    def execute_tool(self, call):
        return ToolResult(tool_call=call, success=True, output={"ok": True})


def build_runtime(tmp, approvals=False, executor=None, evaluator=None):
    bus = DurableEventBus(os.path.join(tmp, "events.db"))
    queue = TaskQueue(os.path.join(tmp, "queue.db"), bus=bus)
    store = ApprovalStore(os.path.join(tmp, "approvals.db"), bus=bus) if approvals else None
    retriever = ComposedRetriever()
    retriever.ingest("filesystem", "docs/x.md", "v1", "context")
    tools = ToolRegistry()
    tools.register(ToolSpec("github.read_file", "read a file", Risk.READ))
    tools.register(ToolSpec("github.force_push", "force push", Risk.DESTRUCTIVE))
    tools.register(ToolSpec("github.create_pr", "open a PR", Risk.WRITE))
    runtime = NexusRuntime(
        retriever=retriever, executor=executor or FakeExecutor(), queue=queue,
        event_bus=bus, tools=tools, approvals=store,
        policy=PolicyEngine(PolicyRules()), evaluator=evaluator or Evaluator())
    return runtime, bus, queue, store


def main():
    print("Phase 4.7 golden task: the thin CLI")
    tmp = tempfile.mkdtemp()

    # 1. ask is asynchronous, identical to HTTP
    runtime, bus, queue, _ = build_runtime(tmp)
    out = dispatch(runtime, ["ask", "summarize the repository"])
    check("Task:" in out and "queued" in out, "ask returns a queued task id (async, like HTTP)")
    task_id = out.split("\n")[0].split(": ")[1]
    check(queue.get(task_id).status == TaskStatus.QUEUED, "ask did NOT run the task inline")

    # run a FAIL->REPLAN->PASS task (read allowed, force_push denied)
    script = [
        ModelResponse(model="fake", content="", success=True, tool_calls=[
            ToolCall(tool_name="github.read_file", arguments={"path": "a.py"}),
            ToolCall(tool_name="github.force_push", arguments={}),
        ]),
        ModelResponse(model="fake", content="x", success=True),
        ModelResponse(model="fake", content="", success=True, tool_calls=[
            ToolCall(tool_name="github.read_file", arguments={"path": "a.py"}),
        ]),
        ModelResponse(model="fake", content="done", success=True),
    ]
    evaluator = FakeEvaluator([
        Evaluation(passed=False, reason="not fixed", replan_required=True),
        Evaluation(passed=True, reason="fixed"),
    ])
    runtime2, bus2, queue2, _ = build_runtime(
        tempfile.mkdtemp(), executor=FakeExecutor(model_script=script), evaluator=evaluator)
    t2 = runtime2.ask("fix the thing")
    run = runtime2.run_one().run

    # 2. task renders status + attempts + decisions (the dashboard's view model)
    out = dispatch(runtime2, ["task", t2])
    check("Status: done" in out, "task renders the task status")
    check("attempt 1: evaluation FAIL" in out and "attempt 2: evaluation PASS" in out,
          "task renders the attempt history")
    check("policy github.force_push: destructive -> deny" in out,
          "task renders the policy decision (no recomputation)")

    # 3. trace renders the run's projected trace
    out = dispatch(runtime2, ["trace", run.run_id])
    check(f"Trace: {run.run_id}" in out and "policy github.read_file" in out,
          "trace renders the run's projected trace")

    # 4. approve/deny are thin commands into the runtime
    runtime3, bus3, queue3, store3 = build_runtime(tempfile.mkdtemp(), approvals=True,
                                                   executor=ProposeWrite())
    tid = runtime3.ask("open a PR")
    runtime3.run_one()  # pauses for approval
    aid = [e.payload["approval_id"] for e in bus3.load_events(task_id=tid)
           if e.event_type == EventType.APPROVAL_REQUIRED][0]
    out = dispatch(runtime3, ["approve", aid])
    check("approved" in out, "the CLI approve command approves (thin call)")
    check(queue3.get(tid).status == TaskStatus.QUEUED, "approve requeued the task")
    runtime3.run_one()
    check(queue3.get(tid).status == TaskStatus.DONE, "the task completes after approval")

    print("\nPASS: Phase 4.7 CLI holds (REST/WS/dashboard/CLI are one execution model).")


if __name__ == "__main__":
    main()
