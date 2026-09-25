"""Phase 3.8 golden task — MCP as another tool transport (implementation #3).

One end-to-end task uses an MCP-backed tool while the Orchestrator is completely
unchanged. Then the same task runs with the FakeExecutor. The only thing that
changes is dependency injection.

Run:  py tests/golden/test_phase3_mcp.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "tests"))

from fixtures import fake_mcp_server_argv  # noqa: E402

from core.contracts import ModelResponse, Risk, Task, ToolCall, new_id  # noqa: E402
from core.events import EventBus, EventType  # noqa: E402
from control.evaluator import Evaluator  # noqa: E402
from control.policy import PolicyEngine, PolicyRules  # noqa: E402
from control.tools import ToolRegistry, ToolSpec  # noqa: E402
from execution.fake import FakeExecutor  # noqa: E402
from execution.orchestrator import Orchestrator  # noqa: E402
from integrations.mcp import McpToolExecutor  # noqa: E402
from knowledge.inmemory import ComposedRetriever  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def _lines(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return f.read().splitlines()


class _Composite:
    """Wiring: one Executor that routes run_model -> model, execute_tool -> tool."""

    def __init__(self, model, tool):
        self._model = model
        self._tool = tool

    def run_model(self, request):
        return self._model.run_model(request)

    def execute_tool(self, call):
        return self._tool.execute_tool(call)


def build_registry(mcp):
    tools = ToolRegistry()
    for d in mcp.discover():
        risk = Risk.DESTRUCTIVE if d.name == "github.force_push" else Risk.READ
        tools.register(ToolSpec(d.name, d.description, risk, parameters=d.input_schema))
    return tools


def build_orchestrator(executor, tools, bus):
    retriever = ComposedRetriever()
    retriever.ingest("filesystem", "docs/x.md", "v1", "context for the task")
    return Orchestrator(retriever=retriever, executor=executor,
                        policy=PolicyEngine(PolicyRules()), tools=tools,
                        evaluator=Evaluator(), bus=bus)


def main():
    print("Phase 3.8 golden task: MCP as another tool transport")
    tmp = tempfile.mkdtemp()
    os.environ["CALL_LOG_PATH"] = os.path.join(tmp, "calls.txt")

    mcp = McpToolExecutor(fake_mcp_server_argv())
    mcp.start()
    tools = build_registry(mcp)

    script = [
        ModelResponse(model="fake", content="", success=True, tool_calls=[
            ToolCall(tool_name="github.read_file", arguments={"path": "a.py"}),
            ToolCall(tool_name="github.force_push", arguments={}),
        ]),
        ModelResponse(model="fake", content="done via mcp", success=True),
    ]

    # 1. the Orchestrator (unchanged) runs an MCP-backed tool
    bus = EventBus()
    outcome = build_orchestrator(
        _Composite(FakeExecutor(model_script=script), mcp), tools, bus
    ).run(Task(task_id=new_id("task"), title="read and maybe push"))
    check(outcome.answer == "done via mcp",
          "the orchestrator runs the MCP-backed tool with no change to the loop")

    # 2. policy stays above MCP: the DENIED tool never reached the server
    check(_lines(os.environ["CALL_LOG_PATH"]) == ["github.read_file"],
          "only the ALLOWED tool reached the MCP server (policy denied force_push first)")

    # 3. instrumentation is universal: requested -> MCP call -> completed
    t_req = [i for i, e in enumerate(outcome.events)
             if e.event_type == EventType.TOOL_REQUESTED]
    t_done = [i for i, e in enumerate(outcome.events)
              if e.event_type == EventType.TOOL_COMPLETED]
    check(len(t_req) == 1 and len(t_done) == 1 and t_req[0] < t_done[0],
          "the MCP call is instrumented (tool.requested before tool.completed)")

    # 4. only dependency injection changes: same task, fake executor, same answer
    bus2 = EventBus()
    fake_out = build_orchestrator(
        FakeExecutor(model_script=list(script)), tools, bus2
    ).run(Task(task_id=new_id("task"), title="read and maybe push"))
    check(fake_out.answer == outcome.answer,
          "same answer with the fake executor (only dependency injection changed)")

    mcp.close()
    print("\nPASS: Phase 3.8 MCP transport holds (implementation #3, anticlimactic).")


if __name__ == "__main__":
    main()
