"""Conformance: the Executor boundary holds across implementations.

`execute_tool(ToolCall) -> ToolResult` and `run_model(ModelRequest) ->
ModelResponse`, and only contracts cross the boundary: the result is plain,
serializable data — never a subprocess object, Popen, pipe, SDK response, or raw
return code. The same assertions run against the reference FakeExecutor, the
real SubprocessToolExecutor, and the real SubprocessModelExecutor (and, later,
the MCP adapter).

Run:  py tests/conformance/test_executor_boundary.py
"""
import dataclasses
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from fixtures import fake_model_argv  # noqa: E402

from core.contracts import ModelRequest, ModelResponse, ToolCall, ToolResult  # noqa: E402
from execution.fake import FakeExecutor  # noqa: E402
from execution.models import ModelSpec, SubprocessModelExecutor  # noqa: E402
from execution.subprocess import SubprocessSpec, SubprocessToolExecutor  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def assert_tool_boundary(executor, call, name: str) -> None:
    tr = executor.execute_tool(call)
    check(isinstance(tr, ToolResult), f"{name}: execute_tool returns a ToolResult (the contract)")
    json.dumps(tr.output)  # raises if a non-serializable implementation object leaked
    check(tr.success is True, f"{name}: success is a plain bool")
    check(tr.error is None, f"{name}: error is None on success")
    check(tr.tool_call.tool_name == call.tool_name,
          f"{name}: the result carries the originating call")


def assert_model_boundary(executor, request, name: str) -> None:
    resp = executor.run_model(request)
    check(isinstance(resp, ModelResponse), f"{name}: run_model returns a ModelResponse (the contract)")
    json.dumps(dataclasses.asdict(resp))  # raises if a provider object leaked
    check(resp.success is True, f"{name}: success is a plain bool")
    check(resp.error is None, f"{name}: error is None on success")
    check(isinstance(resp.content, str), f"{name}: content is plain serializable data")


def main() -> None:
    print("Conformance: Executor boundary holds across implementations")

    fake = FakeExecutor()
    assert_tool_boundary(fake, ToolCall(tool_name="format", arguments={"path": "a.py"}), "Fake")
    assert_model_boundary(fake, ModelRequest(messages=[{"role": "user", "content": "hi"}]), "Fake")

    sub = SubprocessToolExecutor([SubprocessSpec("echo", [sys.executable, "-c", "print('hi')"])])
    assert_tool_boundary(sub, ToolCall(tool_name="echo", arguments={}), "Subprocess")

    model = SubprocessModelExecutor(ModelSpec("local-llm", fake_model_argv()))
    assert_model_boundary(model, ModelRequest(messages=[{"role": "user", "content": "hi"}]), "Model")

    print("\nPASS: the Executor boundary is implementation-agnostic (only contracts cross).")


if __name__ == "__main__":
    main()
