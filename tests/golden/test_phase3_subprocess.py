"""Phase 3.2 golden task — subprocess tool execution (the first real Executor).

The real implementation proves the SAME execution semantics the FakeExecutor
established, plus the 3.2 acceptance properties: expected failures become
ToolResult, timeout is bounded, output is bounded, the registry (not the model)
is the authority for executables, and the requested-before-completed invariant
still holds through InstrumentedExecutor.

Run:  py tests/golden/test_phase3_subprocess.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import ToolCall, ToolResult  # noqa: E402
from core.events import EventBus, EventType  # noqa: E402
from execution.instrumented import InstrumentedExecutor  # noqa: E402
from execution.subprocess import SubprocessSpec, SubprocessToolExecutor  # noqa: E402

PY = sys.executable


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def main():
    print("Phase 3.2 golden task: subprocess tool execution (same semantics as FakeExecutor)")
    executor = SubprocessToolExecutor([
        SubprocessSpec("echo", [PY, "-c", "print('hello')"]),
        SubprocessSpec("upper", [PY, "-c", "import sys; print(sys.argv[1].upper())", "{text}"]),
        SubprocessSpec("fail", [PY, "-c", "import sys; print('boom', file=sys.stderr); sys.exit(3)"]),
        SubprocessSpec("slow", [PY, "-c", "import time; time.sleep(5)"], timeout_seconds=0.5),
        SubprocessSpec("big", [PY, "-c", "print('x' * 200000)"], max_output_bytes=256),
    ])

    # 1. the contract: ToolCall -> ToolResult, plain data only
    tr = executor.execute_tool(ToolCall(tool_name="echo", arguments={}))
    check(isinstance(tr, ToolResult) and tr.success, "ToolCall -> ToolResult (success)")
    check(tr.output["stdout"].strip() == "hello",
          "stdout is captured as plain data (no Popen/pipe/return-code object escapes)")
    check(isinstance(tr.output["stdout"], str) and isinstance(tr.output["stderr"], str),
          "output is bounded, serializable data")

    # 2. arguments map through declared placeholders — never shell, never an executable
    tr2 = executor.execute_tool(ToolCall(tool_name="upper", arguments={"text": "hello"}))
    check(tr2.output["stdout"].strip() == "HELLO", "arguments fill declared placeholders (positional)")

    tr3 = executor.execute_tool(
        ToolCall(tool_name="upper", arguments={"text": "hi", "executable": "evil.exe"}))
    check(tr3.output["stdout"].strip() == "HI",
          "the model's arguments cannot choose an executable (the registry is the authority)")

    # 3. expected failure (non-zero exit) -> ToolResult, not an exception
    tr4 = executor.execute_tool(ToolCall(tool_name="fail", arguments={}))
    check(tr4.success is False and "exit status 3" in tr4.error,
          "non-zero exit -> ToolResult(success=False) (expected failure, no exception)")
    check("boom" in tr4.output["stderr"], "stderr is captured and bounded")

    # 4. timeout -> bounded ToolResult, Nexus does not hang
    tr5 = executor.execute_tool(ToolCall(tool_name="slow", arguments={}))
    check(tr5.success is False and tr5.error.startswith("timeout"),
          "timeout -> ToolResult(success=False), a non-exiting process does not hang Nexus")

    # 5. bounded output — no unlimited stdout/stderr into an event or trace
    tr6 = executor.execute_tool(ToolCall(tool_name="big", arguments={}))
    check(len(tr6.output["stdout"]) <= 256 + 40, "stdout is bounded")
    check("truncated" in tr6.output["stdout"], "truncation is explicit, not silent")

    # 6. infrastructure failure (unknown tool) -> raises (programming error, not a tool outcome)
    try:
        executor.execute_tool(ToolCall(tool_name="rm_rf", arguments={}))
        check(False, "unknown tool -> raise")
    except ValueError:
        check(True, "unknown tool raises (programming error, not a tool outcome)")

    # 7. the invariant holds through InstrumentedExecutor
    bus = EventBus()
    InstrumentedExecutor(executor, bus, run_id="r1", task_id="t1").execute_tool(
        ToolCall(tool_name="echo", arguments={}))
    kinds = [e.event_type for e in bus.history]
    check(kinds.index(EventType.TOOL_REQUESTED) < kinds.index(EventType.TOOL_COMPLETED),
          "requested-before-completed holds for a real subprocess")

    bus2 = EventBus()
    InstrumentedExecutor(executor, bus2, run_id="r2", task_id="t2").execute_tool(
        ToolCall(tool_name="fail", arguments={}))
    done = [e for e in bus2.history if e.event_type == EventType.TOOL_COMPLETED][0]
    check(done.payload.get("success") is False,
          "an expected failure completes with success=False (observable outcome)")

    bus3 = EventBus()
    instr3 = InstrumentedExecutor(executor, bus3, run_id="r3", task_id="t3")
    try:
        instr3.execute_tool(ToolCall(tool_name="rm_rf", arguments={}))
    except ValueError:
        pass
    k3 = [e.event_type for e in bus3.history]
    check(EventType.TOOL_REQUESTED in k3 and EventType.TOOL_COMPLETED not in k3,
          "an infrastructure failure leaves tool.requested but NO tool.completed")

    print("\nPASS: Phase 3.2 subprocess execution holds.")


if __name__ == "__main__":
    main()
