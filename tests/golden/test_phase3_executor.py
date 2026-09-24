"""Phase 3 golden task — execution semantics + the event-before-complete invariant.

The reference executor (FakeExecutor) proves what `execute_tool` / `run_model`
mean before any real side effect exists. The InstrumentedExecutor proves the
invariant: every externally observable step emits an event BEFORE it is
considered complete — so a killed worker's event log still tells the truth.

Run:  py tests/golden/test_phase3_executor.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import ModelRequest, ToolCall, ToolResult  # noqa: E402
from core.events import EventBus, EventType  # noqa: E402
from execution.fake import FakeExecutor  # noqa: E402
from execution.instrumented import InstrumentedExecutor  # noqa: E402


class RaisingExecutor:
    """A deliberately broken executor: every step raises before completing."""

    def execute_tool(self, call):
        raise RuntimeError("boom")

    def run_model(self, request):
        raise RuntimeError("boom")


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def main():
    print("Phase 3 golden task: execution semantics + event-before-complete")
    fake = FakeExecutor()
    tool = ToolCall(tool_name="github.read_file", arguments={"path": "middleware.py"})

    # --- the Executor contract (Decision != Action: this is the Action half) ---
    tr = fake.execute_tool(tool)
    check(isinstance(tr, ToolResult) and tr.success,
          "execute_tool returns a successful ToolResult (the contract)")
    check(tr.output["echo"] == {"path": "middleware.py"},
          "the reference executor echoes the call (deterministic output)")

    mr = fake.run_model(ModelRequest(messages=[{"role": "user", "content": "hello world"}]))
    check(mr.model == "fake" and mr.content == "echo: hello world",
          "run_model returns a ModelResponse (the contract)")

    # --- determinism: the reference is a fixed point, not a provider ---
    tr2 = fake.execute_tool(tool)
    check(tr2.output == tr.output and tr2.success == tr.success,
          "the reference executor is deterministic (same call -> same result)")

    # --- the invariant: event BEFORE complete (success path, tool) ---
    bus = EventBus()
    instr = InstrumentedExecutor(fake, bus, run_id="r1", task_id="t1")
    instr.execute_tool(tool)
    kinds = [e.event_type for e in bus.history]
    check(EventType.TOOL_REQUESTED in kinds and EventType.TOOL_COMPLETED in kinds,
          "a tool execution emits tool.requested AND tool.completed")
    check(kinds.index(EventType.TOOL_REQUESTED) < kinds.index(EventType.TOOL_COMPLETED),
          "tool.requested is emitted BEFORE tool.completed (event before complete)")
    done = bus.history[kinds.index(EventType.TOOL_COMPLETED)]
    check(done.payload.get("success") is True, "tool.completed carries the result")

    # --- the invariant (success path, model) ---
    bus2 = EventBus()
    instr2 = InstrumentedExecutor(fake, bus2, run_id="r2", task_id="t2")
    instr2.run_model(ModelRequest(messages=[{"role": "user", "content": "hi"}]))
    k2 = [e.event_type for e in bus2.history]
    check(EventType.MODEL_REQUESTED in k2 and EventType.MODEL_COMPLETED in k2
          and k2.index(EventType.MODEL_REQUESTED) < k2.index(EventType.MODEL_COMPLETED),
          "a model execution emits model.requested BEFORE model.completed")

    # --- the invariant (failure path): an un-completed step leaves no completed event ---
    bus3 = EventBus()
    instr3 = InstrumentedExecutor(RaisingExecutor(), bus3, run_id="r3", task_id="t3")
    try:
        instr3.execute_tool(tool)
    except RuntimeError:
        pass
    k3 = [e.event_type for e in bus3.history]
    check(EventType.TOOL_REQUESTED in k3 and EventType.TOOL_COMPLETED not in k3,
          "a raising executor leaves tool.requested but NO tool.completed (step not complete)")

    print("\nPASS: Phase 3 execution semantics hold.")


if __name__ == "__main__":
    main()
