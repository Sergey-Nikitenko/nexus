"""Phase 4.5 golden task — the approval lifecycle.

Approval is a durable task-state transition, never an HTTP callback to a waiting
worker. The model proposes a WRITE tool; the policy requires approval; the task
pauses durably; a human approves a SPECIFIC proposal; the worker resumes and the
policy is re-checked before the tool executes.

Run:  py tests/golden/test_phase4_approval.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi.testclient import TestClient  # noqa: E402

from core.contracts import ModelResponse, Risk, TaskStatus, ToolCall, ToolResult  # noqa: E402
from core.events import EventType  # noqa: E402
from control.policy import PolicyEngine, PolicyRules  # noqa: E402
from control.tools import ToolRegistry, ToolSpec  # noqa: E402
from execution.approvals import ApprovalStore  # noqa: E402
from execution.durable import DurableEventBus  # noqa: E402
from execution.queue import TaskQueue  # noqa: E402
from apps.http import create_app  # noqa: E402
from apps.runtime import NexusRuntime  # noqa: E402
from knowledge.inmemory import ComposedRetriever  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


class AlwaysProposeWrite:
    """A deterministic executor that always proposes a WRITE tool, then answers."""

    def run_model(self, request):
        if any(m.get("role") == "tool" for m in request.messages):
            return ModelResponse(model="fake", content="done", success=True)
        return ModelResponse(model="fake", content="", success=True, tool_calls=[
            ToolCall(tool_name="github.create_pr", arguments={})])

    def execute_tool(self, call):
        return ToolResult(tool_call=call, success=True, output={"ok": True})


def build_runtime(tmp, policy=None):
    bus = DurableEventBus(os.path.join(tmp, "events.db"))
    queue = TaskQueue(os.path.join(tmp, "queue.db"), bus=bus)
    approvals = ApprovalStore(os.path.join(tmp, "approvals.db"), bus=bus)
    retriever = ComposedRetriever()
    retriever.ingest("filesystem", "docs/x.md", "v1", "context")
    tools = ToolRegistry()
    tools.register(ToolSpec("github.create_pr", "open a PR", Risk.WRITE))
    runtime = NexusRuntime(
        retriever=retriever, executor=AlwaysProposeWrite(), queue=queue,
        event_bus=bus, tools=tools, approvals=approvals,
        policy=policy or PolicyEngine(PolicyRules()))
    return runtime, bus, queue, approvals


def approval_id(events):
    for e in events:
        if e.event_type == EventType.APPROVAL_REQUIRED:
            return e.payload.get("approval_id")
    return None


def main():
    print("Phase 4.5 golden task: the approval lifecycle")
    tmp = tempfile.mkdtemp()
    runtime, bus, queue, approvals = build_runtime(tmp)
    client = TestClient(create_app(runtime))

    # 1. propose a WRITE tool -> policy APPROVAL_REQUIRED -> durable pause
    task_id = runtime.ask("open a pull request")
    outcome = runtime.run_one()
    check(outcome is not None and outcome.waiting, "the run pauses for approval")
    check(queue.get(task_id).status == TaskStatus.AWAITING_APPROVAL,
          "the task is durably WAITING_APPROVAL (not lost, not failed)")
    aid = approval_id(bus.load_events(task_id=task_id))
    check(aid is not None and approvals.get(aid).status == "pending",
          "a PENDING approval exists, bound to this proposal")
    check(not any(e.event_type == EventType.TOOL_REQUESTED for e in bus.load_events(task_id=task_id)),
          "the tool has NOT executed (policy stopped it)")

    # 2. durability: kill/recreate the runtime -> the approval is still pending
    bus.close(); queue.close(); approvals.close()
    runtime2, bus2, queue2, approvals2 = build_runtime(tmp)
    client2 = TestClient(create_app(runtime2))  # a fresh client over the recreated runtime
    check(approvals2.get(aid).status == "pending", "the pending approval survives a restart")
    check(queue2.get(task_id).status == TaskStatus.AWAITING_APPROVAL,
          "WAITING_APPROVAL survives a restart")

    # 3. downstream purity: the HTTP approve requeues, but never executes the tool
    resp = client2.post(f"/approvals/{aid}/approve")
    check(resp.status_code == 200, "POST /approvals/{id}/approve succeeds")
    check(approvals2.get(aid).status == "approved", "the approval is APPROVED")
    check(queue2.get(task_id).status == TaskStatus.QUEUED, "the task is requeued")
    check(not any(e.event_type == EventType.TOOL_REQUESTED
                  for e in bus2.load_events(task_id=task_id)),
          "approval itself never executes the tool (surface commands, never executes)")

    # 4. resume: a worker re-runs, policy is re-checked, the tool executes once
    outcome2 = runtime2.run_one()
    check(outcome2 is not None and not outcome2.waiting, "the resumed run completes")
    check(queue2.get(task_id).status == TaskStatus.DONE, "the task reaches DONE")
    check(approvals2.get(aid).status == "consumed", "the approval was CONSUMED (single-use)")
    seq = [e.event_type for e in bus2.load_events(task_id=task_id)]
    check(EventType.POLICY_DECISION in seq and EventType.APPROVAL_REQUIRED in seq
          and EventType.APPROVAL_GRANTED in seq and EventType.APPROVAL_CONSUMED in seq
          and EventType.TOOL_REQUESTED in seq and EventType.TOOL_COMPLETED in seq,
          "the audit trail is reconstructible (decision -> required -> granted -> consumed -> executed)")

    # 5. single-use: the consumed approval cannot be replayed against another call
    check(approvals2.find_approved(task_id, "github.create_pr", Risk.WRITE) is None,
          "a consumed approval cannot authorize a later execution")

    # 6. authorization specificity: approving one proposal does not authorize another
    runtime3, bus3, queue3, approvals3 = build_runtime(tempfile.mkdtemp())
    tA = runtime3.ask("task A")
    runtime3.run_one()  # A pauses for approval
    aA = approval_id(bus3.load_events(task_id=tA))
    approvals3.approve(aA)  # approve A, but do NOT requeue A yet
    tB = runtime3.ask("task B")  # B is the only queued task
    runtime3.run_one()  # claims B -> B's identical proposal has no matching approval
    check(queue3.get(tB).status == TaskStatus.AWAITING_APPROVAL,
          "approving A does NOT authorize B (the approval is task-bound)")
    check(approval_id(bus3.load_events(task_id=tB)) != aA,
          "B received its OWN approval request")

    # 7. policy remains authoritative: a granted approval does not bypass a DENY
    tmp7 = tempfile.mkdtemp()
    runtime4, bus4, queue4, approvals4 = build_runtime(tmp7)
    tC = runtime4.ask("task C")
    runtime4.run_one()  # C pauses
    aC = approval_id(bus4.load_events(task_id=tC))
    approvals4.approve(aC)
    queue4.requeue(tC)
    bus4.close(); queue4.close(); approvals4.close()
    # recreate with a DENYLISTED policy on the SAME durable paths
    deny_policy = PolicyEngine(PolicyRules(denylist=["github.create_pr"]))
    runtime5, bus5, queue5, approvals5 = build_runtime(tmp7, policy=deny_policy)
    runtime5.run_one()  # resume C -> policy DENY short-circuits before the approval
    check(not any(e.event_type == EventType.TOOL_REQUESTED
                  for e in bus5.load_events(task_id=tC)),
          "a granted approval does NOT bypass a DENY policy (policy re-checked)")
    check(approvals5.get(aC).status == "approved",
          "the approval is unconsumed (DENY short-circuited before it)")

    print("\nPASS: Phase 4.5 approval lifecycle holds.")


if __name__ == "__main__":
    main()
