"""Conformance: the Executor boundary holds across implementations.

`execute_tool(ToolCall) -> ToolResult`, and only contracts cross the boundary:
the result is plain, serializable data — never a subprocess object, Popen, pipe,
or raw return code. The same assertions run against the reference FakeExecutor
and the real SubprocessToolExecutor (and, later, the MCP adapter).

Run:  py tests/conformance/test_executor_boundary.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import ToolCall, ToolResult  # noqa: E402
from execution.fake import FakeExecutor  # noqa: E402
from execution.subprocess import SubprocessSpec, SubprocessToolExecutor  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def assert_executor_boundary(executor, call, name: str) -> None:
    tr = executor.execute_tool(call)
    check(isinstance(tr, ToolResult), f"{name}: execute_tool returns a ToolResult (the contract)")
    json.dumps(tr.output)  # raises if a non-serializable implementation object leaked
    check(tr.success is True, f"{name}: success is a plain bool")
    check(tr.error is None, f"{name}: error is None on success")
    check(tr.tool_call.tool_name == call.tool_name,
          f"{name}: the result carries the originating call")


def main() -> None:
    print("Conformance: Executor boundary holds across implementations")
    assert_executor_boundary(
        FakeExecutor(),
        ToolCall(tool_name="format", arguments={"path": "a.py"}),
        "Fake",
    )
    sub = SubprocessToolExecutor([SubprocessSpec("echo", [sys.executable, "-c", "print('hi')"])])
    assert_executor_boundary(sub, ToolCall(tool_name="echo", arguments={}), "Subprocess")
    print("\nPASS: the Executor boundary is implementation-agnostic (only contracts cross).")


if __name__ == "__main__":
    main()
