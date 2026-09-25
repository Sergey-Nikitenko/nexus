"""Phase 6.6 golden task — multi-agent composition.

Multiple agent ROLES (supervisor, research, coding, review) collaborate through
the SAME Nexus contracts, policy, durable execution, recovery, and observability
— without a parallel execution model. Each agent is an `Agent` (a stable logical
identity + a runtime), not a new substrate.

Proves:
1. agent identity is explicit and NOT worker_id/task_id/run_id;
2. supervisor -> child delegation is durably correlated (parent_run_id + agent);
3. a child cannot bypass policy (coding = WRITE -> approval);
4. a failed child replans and produces an observable child outcome;
5. reconstruction tells what was delegated and by whom;
6. each child run retains its own identity and fingerprint (compositional).

Run:  py tests/golden/test_phase6_multiagent.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import Evaluation, ModelResponse, Risk, ToolCall, ToolResult  # noqa: E402
from core.events import EventType  # noqa: E402
from control.evaluator import Evaluator, FakeEvaluator  # noqa: E402
from control.policy import PolicyEngine, PolicyRules  # noqa: E402
from control.tools import ToolRegistry, ToolSpec  # noqa: E402
from execution.agent import Agent  # noqa: E402
from execution.approvals import ApprovalStore  # noqa: E402
from execution.durable import DurableEventBus  # noqa: E402
from execution.queue import TaskQueue  # noqa: E402
from execution.run_records import RunRecordStore  # noqa: E402
from apps.runtime import NexusRuntime  # noqa: E402
from knowledge.inmemory import ComposedRetriever  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


class ProposeTool:
    """Stateless executor: always proposes ONE tool (then answers). Replay-safe,
    so an approval-paused task resumes cleanly and a replanned task re-proposes."""

    def __init__(self, tool_name):
        self.tool_name = tool_name

    def run_model(self, request):
        if any(m.get("role") == "tool" for m in request.messages):
            return ModelResponse(model="fake", content=f"{self.tool_name} done", success=True)
        return ModelResponse(model="fake", content="", success=True, tool_calls=[
            ToolCall(tool_name=self.tool_name, arguments={})])

    def execute_tool(self, call):
        return ToolResult(tool_call=call, success=True, output={"ok": True})


def build_agent(agent_id, tool_name, tool_risk, evaluator=None):
    d = tempfile.mkdtemp()
    bus = DurableEventBus(os.path.join(d, "events.db"))
    queue = TaskQueue(os.path.join(d, "queue.db"), bus=bus)
    approvals = ApprovalStore(os.path.join(d, "approvals.db"), bus=bus)
    records = RunRecordStore(os.path.join(d, "records.db"))
    retriever = ComposedRetriever()
    retriever.ingest("filesystem", "docs/x.md", "v1", "context")
    tools = ToolRegistry()
    tools.register(ToolSpec("github.read_file", "read a file", Risk.READ))
    tools.register(ToolSpec("github.create_pr", "open a PR", Risk.WRITE))
    runtime = NexusRuntime(
        retriever=retriever, executor=ProposeTool(tool_name), queue=queue,
        event_bus=bus, tools=tools, approvals=approvals, run_records=records,
        policy=PolicyEngine(PolicyRules()), evaluator=evaluator or Evaluator())
    return Agent(agent_id=agent_id, runtime=runtime), runtime


def main():
    print("Phase 6.6 golden task: multi-agent composition")

    supervisor, _ = build_agent("supervisor", "github.read_file", Risk.READ)
    research, research_runtime = build_agent("research", "github.read_file", Risk.READ)
    coding, coding_runtime = build_agent("coding", "github.create_pr", Risk.WRITE)
    review, review_runtime = build_agent("review", "github.read_file", Risk.READ,
                                         evaluator=FakeEvaluator([
                                             Evaluation(passed=False, reason="needs work",
                                                         replan_required=True),
                                             Evaluation(passed=True, reason="ok"),
                                         ]))

    # --- supervisor's own run establishes the top-level identity -------------
    supervisor.submit("compose a feature")
    sup_out = supervisor.run_one()
    sup_run_id = sup_out.run.run_id
    check(sup_out.manifest.agent.agent_id == "supervisor" and sup_out.manifest.parent_run_id == "",
          "the supervisor's manifest records its agent role and no parent")

    # --- supervisor delegates to three child roles --------------------------
    r_task = research.submit("research the API", parent_run_id=sup_run_id)
    c_task = coding.submit("implement the feature", parent_run_id=sup_run_id)
    rv_task = review.submit("review the code", parent_run_id=sup_run_id)

    r_out = research.run_one()
    c_out = coding.run_one()      # WRITE -> pauses for approval
    rv_out = review.run_one()     # fails once, then replans and completes

    # --- policy is not bypassed by delegation -------------------------------
    check(c_out.waiting, "coding (WRITE) required approval — delegation does not grant authority")
    aid = [e.payload["approval_id"] for e in coding_runtime.event_bus.load_events(task_id=c_task)
           if e.event_type == EventType.APPROVAL_REQUIRED][0]
    coding_runtime.approve(aid)
    c_out2 = coding.run_one()

    # --- 1. agent identity + delegation correlation -------------------------
    for out, role in [(r_out, "research"), (c_out2, "coding"), (rv_out, "review")]:
        check(out.manifest.agent.agent_id == role and out.manifest.parent_run_id == sup_run_id,
              f"{role}: manifest carries its agent role AND the delegating (parent) run")
    check(rv_out.manifest.agent.agent_id != sup_run_id,
          "agent identity is not conflated with a run_id")

    # --- 2. a failed child replans, observably, without corrupting its own run
    replans = [e for e in rv_out.events if e.event_type == EventType.RUN_REPLANNED]
    check(len(replans) == 1, "the review child failed once and replanned (observable)")

    # --- 3. compositional: each child run keeps its own identity + fingerprint
    child_run_ids = [r_out.run.run_id, c_out2.run.run_id, rv_out.run.run_id]
    check(len(set(child_run_ids)) == 3, "each child run has its own distinct run identity")
    check(research_runtime.run_records.get(r_out.run.run_id)["fingerprint"] is not None,
          "a child run has its own terminal fingerprint")

    # --- 4. reconstruction: durable evidence says what was delegated ---------
    rec = research_runtime.run_records.get(r_out.run.run_id)
    check(rec["manifest"]["agent"]["agent_id"] == "research"
          and rec["manifest"]["parent_run_id"] == sup_run_id,
          "reconstruction (run record) identifies the child's role AND the delegating run")

    print("\nPASS: Phase 6.6 multi-agent composition holds (one execution model, many roles).")


if __name__ == "__main__":
    main()
