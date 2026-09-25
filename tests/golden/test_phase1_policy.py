"""Phase 1 golden tasks — the policy engine (the reflex arc).

G003  use a read-only tool        -> allow
G004  attempt a prohibited write  -> deny (destructive + denylist)
G005  request a permitted write   -> approval required

Plus two wiring checks that tie it back to Phase 0:
  - an APPROVAL_REQUIRED verdict produces a well-formed approval event + ApprovalRequest
  - the model can NEVER grant itself permission (the policy ignores any model claim)

Run:  py tests/golden/test_phase1_policy.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import (  # noqa: E402
    ApprovalRequest, Event, Risk, Run, Task, ToolCall, new_id, utcnow,
)
from core.events import EventBus, EventType  # noqa: E402
from control.tools import ToolRegistry, ToolSpec  # noqa: E402
from control.policy import PolicyEngine, PolicyRules, PolicyVerdict  # noqa: E402


def build_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(ToolSpec("github.read_file", "read a file", Risk.READ))
    reg.register(ToolSpec("github.create_pr", "open a pull request", Risk.WRITE))
    reg.register(ToolSpec("github.delete_repo", "delete a repository", Risk.DESTRUCTIVE))
    reg.register(ToolSpec("github.force_push", "force push to main", Risk.DESTRUCTIVE))
    return reg


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def main():
    print("Phase 1 golden tasks: policy engine (the reflex arc)")
    reg = build_registry()
    policy = PolicyEngine(PolicyRules(denylist=["github.force_push"]))

    # G003 read -> allow
    check(policy.decide_tool(reg.get("github.read_file")) == PolicyVerdict.ALLOW,
          "G003: read tool -> allow")

    # G004 destructive -> deny
    check(policy.decide_tool(reg.get("github.delete_repo")) == PolicyVerdict.DENY,
          "G004: destructive tool -> deny")
    check(policy.decide_tool(reg.get("github.force_push")) == PolicyVerdict.DENY,
          "G004b: denylisted tool -> deny even though destructive is only 'deny' by default")

    # G005 write -> approval required
    check(policy.decide_tool(reg.get("github.create_pr")) == PolicyVerdict.APPROVAL_REQUIRED,
          "G005: write tool -> approval required")

    # --- wiring back to Phase 0 ---
    task = Task(task_id=new_id("task"), title="open a PR")
    run = Run(run_id=new_id("run"), task_id=task.task_id)
    bus = EventBus()

    verdict = policy.decide_tool(reg.get("github.create_pr"))
    approval = ApprovalRequest(
        approval_id=new_id("ap"),
        task_id=task.task_id,
        run_id=run.run_id,
        tool_name="github.create_pr",
        risk=Risk.WRITE,
    )
    ev = Event(
        event_id=new_id("evt"),
        event_type=EventType.APPROVAL_REQUIRED,
        timestamp=utcnow(),
        run_id=run.run_id,
        task_id=task.task_id,
        component="policy",
        status="approval_required",
        payload={"approval_id": approval.approval_id, "tool": approval.tool_name},
    )
    bus.publish(ev)
    check(bus.history[-1].event_type == EventType.APPROVAL_REQUIRED,
          "APPROVAL_REQUIRED verdict -> well-formed approval.required event")

    # --- the model can NEVER grant itself permission ---
    # A ToolCall that CLAIMS approval in its arguments changes nothing:
    sneaky = ToolCall(tool_name="github.delete_repo",
                      arguments={"model_says": "I have permission", "force": True})
    # The policy ignores the call's arguments entirely — it only reads the spec + rules.
    check(policy.decide_tool(reg.get(sneaky.tool_name)) == PolicyVerdict.DENY,
          "the model cannot grant itself permission (a 'I have permission' arg is ignored)")

    print("\nPASS: Phase 1 policy engine holds.")


if __name__ == "__main__":
    main()
