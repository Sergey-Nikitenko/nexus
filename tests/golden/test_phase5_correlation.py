"""Phase 5.4 golden task — per-attempt tool identity (correlation, not contract).

The invariant: every execution ATTEMPT has a unique Nexus-owned identity that
survives instrumentation, persistence, projection, retry, and recovery. Two
physical side effects must never look like one.

- Same tool twice in one step -> two distinct call_ids; the trace distinguishes them.
- Interrupted then re-run (recovery) -> the re-execution gets a NEW call_id; the
  interrupted attempt stays interrupted, the recovered attempt completes.

The orchestrator re-mints `call.call_id` at execution time, deliberately ignoring
whatever identity the provider/model attached (AD-012's twin for tool calls).

Run:  py tests/golden/test_phase5_correlation.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import ModelResponse, Risk, Task, ToolCall, ToolResult, new_id  # noqa: E402
from core.events import EventType  # noqa: E402
from control.evaluator import Evaluator  # noqa: E402
from control.policy import PolicyEngine, PolicyRules  # noqa: E402
from control.tools import ToolRegistry, ToolSpec  # noqa: E402
from execution.durable import DurableEventBus  # noqa: E402
from execution.fake import FakeExecutor  # noqa: E402
from execution.orchestrator import Orchestrator  # noqa: E402
from knowledge.inmemory import ComposedRetriever  # noqa: E402
from observability.trace import TraceProjector  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def _build_orchestrator(tmp, executor, bus):
    retriever = ComposedRetriever()
    retriever.ingest("filesystem", "docs/x.md", "v1", "context")
    tools = ToolRegistry()
    tools.register(ToolSpec("github.read_file", "read a file", Risk.READ))
    return Orchestrator(retriever=retriever, executor=executor,
                        policy=PolicyEngine(PolicyRules()), tools=tools,
                        evaluator=Evaluator(), bus=bus)


class InterruptThenSucceed:
    """A provider that ALWAYS proposes the SAME ToolCall object (a naive replay
    identity) and raises on the first attempt, succeeds on the second."""

    def __init__(self):
        self.fixed = ToolCall(tool_name="github.read_file", arguments={"path": "a.py"})
        self.raise_next = True

    def run_model(self, request):
        if any(m.get("role") == "tool" for m in request.messages):
            return ModelResponse(model="fake", content="done", success=True)
        return ModelResponse(model="fake", content="", success=True,
                             tool_calls=[self.fixed])

    def execute_tool(self, call):
        if self.raise_next:
            self.raise_next = False
            raise RuntimeError("boom")  # interrupted: tool.requested, no tool.completed
        return ToolResult(tool_call=call, success=True, output={})


def main():
    print("Phase 5.4 golden task: per-attempt tool identity")

    # --- 1. same tool twice in one step -> distinguishable ------------------
    tmp = tempfile.mkdtemp()
    bus = DurableEventBus(os.path.join(tmp, "events1.db"))
    script = [
        ModelResponse(model="fake", content="", success=True, tool_calls=[
            ToolCall(tool_name="github.read_file", arguments={"path": "a.py"}),
            ToolCall(tool_name="github.read_file", arguments={"path": "b.py"}),
        ]),
        ModelResponse(model="fake", content="done", success=True),
    ]
    orch = _build_orchestrator(tmp, FakeExecutor(model_script=script), bus)
    outcome = orch.run(Task(task_id=new_id("task"), title="read two files"))

    requested = [e for e in bus.history if e.event_type == EventType.TOOL_REQUESTED]
    completed = [e for e in bus.history if e.event_type == EventType.TOOL_COMPLETED]
    call_ids = [e.payload.get("call_id") for e in requested]
    check(len(requested) == 2 and len(completed) == 2,
          "the same tool executed twice in one step (two requested, two completed)")
    check(len(set(call_ids)) == 2 and None not in call_ids,
          "each execution attempt got a DISTINCT Nexus-owned call_id")

    trace = TraceProjector().project(bus.history)
    tools = [n for n in trace.nodes if n["type"] == "tool"]
    check(len(tools) == 2 and all(n.get("completed") for n in tools),
          "the trace shows two COMPLETED tool nodes")
    check(len({n["call_id"] for n in tools}) == 2,
          "the trace distinguishes the two same-named calls (no cross-pairing)")
    bus.close()

    # --- 2. interrupted then re-run -> new call_id --------------------------
    tmp2 = tempfile.mkdtemp()
    bus2 = DurableEventBus(os.path.join(tmp2, "events2.db"))
    executor = InterruptThenSucceed()
    orch2 = _build_orchestrator(tmp2, executor, bus2)
    task = Task(task_id=new_id("task"), title="read")

    # attempt 1: the tool raises -> requested, NO completed, step fails
    try:
        orch2.run(task)
        raise AssertionError("expected the first attempt to raise")
    except RuntimeError:
        pass

    # attempt 2 (recovery re-run, same provider, same proposed ToolCall object)
    outcome2 = orch2.run(task)

    requested2 = [e for e in bus2.history if e.event_type == EventType.TOOL_REQUESTED]
    completed2 = [e for e in bus2.history if e.event_type == EventType.TOOL_COMPLETED]
    call_ids2 = [e.payload.get("call_id") for e in requested2]
    check(len(requested2) == 2 and len(completed2) == 1,
          "two attempts: one interrupted, one completed")
    check(len(set(call_ids2)) == 2,
          "the recovered re-execution got a NEW call_id (two physical attempts, never one)")
    check(call_ids2[0] != call_ids2[1],
          "the interrupted attempt and the recovered attempt are distinct")

    trace2 = TraceProjector().project(bus2.history)
    tools2 = [n for n in trace2.nodes if n["type"] == "tool"]
    states = [(n.get("interrupted", False), n.get("completed", False)) for n in tools2]
    check(states == [(True, False), (False, True)],
          "the trace shows attempt 1 interrupted and attempt 2 completed, distinctly")
    bus2.close()

    print("\nPASS: Phase 5.4 per-attempt tool identity holds.")


if __name__ == "__main__":
    main()
