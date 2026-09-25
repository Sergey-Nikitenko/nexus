"""Conformance: Nexus-as-an-MCP-server boundary holds (the inverse of 3.8).

3.8 proved Nexus can USE an MCP server as a tool provider. This proves Nexus can
BE an MCP server without changing the orchestrator. The adapter owns MCP's
vocabulary; only Nexus contracts cross:

- tools/list -> Nexus ToolDefinition contracts (no MCP objects leak);
- tools/call -> a Nexus-level request (durable task lifecycle, not a bypass);
- the response is re-wrapped in MCP's envelope only at the adapter;
- failure taxonomy: malformed request -> protocol error; a valid Nexus operation
  that fails (unknown task/tool) -> an isError RESULT, never a protocol error.

Provider neutrality is enforced structurally: this module is under `apps/`, so
`test_no_provider_leakage.py` fails if it ever imports `mcp`.

Run:  py tests/conformance/test_nexus_mcp_boundary.py
"""
import io
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from core.contracts import Risk, TaskStatus, ToolDefinition  # noqa: E402
from control.tools import ToolRegistry, ToolSpec  # noqa: E402
from execution.durable import DurableEventBus  # noqa: E402
from execution.fake import FakeExecutor  # noqa: E402
from execution.queue import TaskQueue  # noqa: E402
from apps.mcp_server import NexusMcpServer  # noqa: E402
from apps.runtime import NexusRuntime  # noqa: E402
from knowledge.inmemory import ComposedRetriever  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def build_server(tmp):
    bus = DurableEventBus(os.path.join(tmp, "events.db"))
    queue = TaskQueue(os.path.join(tmp, "queue.db"), bus=bus)
    retriever = ComposedRetriever()
    retriever.ingest("filesystem", "docs/x.md", "v1", "context")
    tools = ToolRegistry()
    tools.register(ToolSpec("github.read_file", "read a file", Risk.READ))
    runtime = NexusRuntime(retriever=retriever, executor=FakeExecutor(), queue=queue,
                           event_bus=bus, tools=tools)
    return NexusMcpServer(runtime), runtime


def main():
    print("Conformance: Nexus-as-an-MCP-server boundary holds")
    server, runtime = build_server(tempfile.mkdtemp())

    # --- 1. discovery -> Nexus ToolDefinition contracts ---------------------
    defs = server.tools()
    check(all(isinstance(d, ToolDefinition) for d in defs),
          "tools() returns Nexus ToolDefinition contracts")
    check({d.name for d in defs} == {"nexus.ask", "nexus.task"},
          "the Nexus capabilities are exposed as tools")
    listing = server.handle("tools/list", {}).get("result", {}).get("tools", [])
    check(all(set(t) == {"name", "description", "inputSchema"} for t in listing),
          "tools/list is MCP wire vocabulary only (no Nexus/provider object)")

    # --- 2. invocation -> a Nexus-level request, durable lifecycle ----------
    resp = server.handle("tools/call", {"name": "nexus.ask",
                                        "arguments": {"title": "fix the thing"}})
    result = resp["result"]
    check("content" in result and "isError" not in result,
          "tools/call returns an MCP content envelope (not a Nexus object)")
    text = json.loads(result["content"][0]["text"])
    task_id = text["task_id"]
    check(text["status"] == "queued", "nexus.ask returns a queued task_id")
    check(runtime.task(task_id).status == TaskStatus.QUEUED,
          "the task is durably queued (the MCP path uses the durable lifecycle, not a bypass)")
    check(task_id.startswith("task_"), "the task_id is a fresh Nexus correlation id")

    # --- 3. failure taxonomy: valid op that fails -> isError RESULT ---------
    unknown_task = server.handle("tools/call", {"name": "nexus.task",
                                                "arguments": {"task_id": "nope"}})
    check(unknown_task.get("result", {}).get("isError") is True,
          "a valid Nexus operation that fails -> isError result (not a protocol error)")
    unknown_tool = server.handle("tools/call", {"name": "nexus.nope", "arguments": {}})
    check(unknown_tool.get("result", {}).get("isError") is True,
          "an unknown tool -> isError result (not a protocol error)")

    # --- 4. failure taxonomy: malformed request -> protocol error -----------
    bad_params = server.handle("tools/call", {"arguments": {}})
    check(bad_params.get("error", {}).get("code") == -32602,
          "malformed tools/call -> invalid-params protocol error")
    bad_method = server.handle("bogus", {})
    check(bad_method.get("error", {}).get("code") == -32601,
          "unknown method -> method-not-found protocol error")

    # --- 5. stdio round-trip: line-delimited JSON-RPC -----------------------
    stdin = io.StringIO(
        json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/list", "params": {}}) + "\n" +
        json.dumps({"jsonrpc": "2.0", "id": 8, "method": "tools/call",
                    "params": {"name": "nexus.task",
                               "arguments": {"task_id": task_id}}}) + "\n" +
        "not json\n")
    stdout = io.StringIO()
    server.serve(stdin, stdout)
    lines = [json.loads(l) for l in stdout.getvalue().splitlines() if l.strip()]
    check(lines[0]["id"] == 7 and "tools" in lines[0]["result"],
          "serve() answers tools/list over stdio")
    check(lines[1]["id"] == 8 and json.loads(lines[1]["result"]["content"][0]["text"])["status"] == "queued",
          "serve() answers tools/call over stdio, preserving correlation")
    check(lines[2]["error"]["code"] == -32700, "a malformed line -> parse-error response")

    print("\nPASS: Nexus-as-an-MCP-server boundary holds (adapter owns MCP; Nexus owns semantics).")


if __name__ == "__main__":
    main()
