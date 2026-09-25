"""The deterministic reference Executor — proves execution semantics before any
real side effect (subprocess, network, model SDK) exists.

Decision != Action: this is the Action half of the boundary. It is deliberately
boring and side-effect-free so the semantics are testable without a provider.
A real executor (MCP tools, subprocesses, model SDKs) is a later adapter behind
the same Executor protocol — the caller never knows which produced the result.
"""
from __future__ import annotations

from core.contracts import ModelIdentity, ModelRequest, ModelResponse, ToolCall, ToolResult


class FakeExecutor:
    """Deterministic, in-memory, side-effect-free Executor — the reference.

    - execute_tool: succeeds and echoes the call's arguments.
    - run_model: returns a fixed, echo-able response keyed off the last message,
      unless a `model_script` is supplied — then it plays back the scripted
      ModelResponses in order (each may be a ModelResponse or a callable
      receiving the request). The script is what lets a deterministic orchestrator
      exercise "model suggests a tool call, then answers" without any real model.

    Same input always yields the same result — that determinism is what lets a
    golden test pin the execution semantics before any provider exists.
    """

    model_identity = ModelIdentity(model_id="fake-deterministic", family="fake", version="1")

    def __init__(self, model_script: list | None = None) -> None:
        self._model_script = list(model_script or [])

    def execute_tool(self, call: ToolCall) -> ToolResult:
        return ToolResult(tool_call=call, success=True, output={"echo": dict(call.arguments)})

    def run_model(self, request: ModelRequest) -> ModelResponse:
        if self._model_script:
            entry = self._model_script.pop(0)
            return entry(request) if callable(entry) else entry
        last = request.messages[-1]["content"] if request.messages else ""
        return ModelResponse(
            model="fake",
            content=f"echo: {last}",
            tokens_in=len(last.split()),
            tokens_out=2,
            latency_ms=0,
            cost=0.0,
            success=True,
        )
