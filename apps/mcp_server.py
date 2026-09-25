"""Nexus as an MCP server — the ingress twin of `integrations/mcp.py`.

Phase 3.8 proved Nexus can USE an MCP server as a tool provider. This is the
inverse boundary: Nexus can BE an MCP server, exposing its capabilities to an
external MCP client — without the orchestrator or any core/control/execution/
knowledge component ever seeing an MCP object.

The adapter owns MCP's vocabulary (JSON-RPC 2.0 over stdio: `initialize`,
`tools/list`, `tools/call`, `content` blocks, `isError`). Only Nexus contracts
cross: a `tools/call` becomes a Nexus-level request (`runtime.ask` / `runtime.task`),
and the result is wrapped in MCP's envelope here, nowhere else.

Failure taxonomy (no second taxonomy is invented):
- a MALFORMED JSON-RPC request -> a protocol error response;
- a valid Nexus operation that fails (unknown task/tool) -> an `isError` RESULT,
  not a protocol error;
- a transport failure (closed stdin) -> the loop simply exits.

Provider neutrality is enforced by `test_no_provider_leakage.py`: this module is
under `apps/`, so it must NOT `import mcp` (the SDK) — and it does not; it speaks
JSON-RPC in stdlib, exactly like the client adapter.
"""
from __future__ import annotations

import json
import sys

from core.contracts import ToolDefinition

PROTOCOL_VERSION = "2024-11-05"


class NexusMcpServer:
    """Serves a Nexus runtime over MCP (JSON-RPC 2.0, stdio, stdlib only)."""

    def __init__(self, runtime) -> None:
        self.runtime = runtime  # duck-typed: .ask(title) and .task(task_id)

    # -- Nexus capabilities, exposed as ToolDefinition contracts -------------
    def tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="nexus.ask",
                description="Submit a task to Nexus (durable, asynchronous)",
                input_schema={"type": "object",
                              "properties": {"title": {"type": "string"}},
                              "required": ["title"]},
            ),
            ToolDefinition(
                name="nexus.task",
                description="Read a task's durable status",
                input_schema={"type": "object",
                              "properties": {"task_id": {"type": "string"}},
                              "required": ["task_id"]},
            ),
        ]

    def list_tools(self) -> list[dict]:
        """MCP `tools/list` payload — Nexus contracts, re-shaped for the wire."""
        return [{"name": t.name, "description": t.description,
                 "inputSchema": t.input_schema} for t in self.tools()]

    # -- invocation: MCP tools/call -> Nexus request -> MCP result -----------
    def call_tool(self, name: str, arguments: dict) -> dict:
        """Return an MCP `tools/call` RESULT (content blocks or isError), never a
        Nexus object."""
        if name == "nexus.ask":
            task_id = self.runtime.ask(arguments.get("title", ""))
            return self._text_result(json.dumps({"task_id": task_id, "status": "queued"}))
        if name == "nexus.task":
            task = self.runtime.task(arguments.get("task_id", ""))
            if task is None:
                return {"isError": True, "content": self._text("unknown task")}
            return self._text_result(
                json.dumps({"task_id": task.task_id, "status": task.status.value}))
        return {"isError": True, "content": self._text(f"unknown tool: {name}")}

    # -- JSON-RPC 2.0 --------------------------------------------------------
    def handle(self, method: str, params: dict, req_id=None) -> dict:
        """Dispatch one JSON-RPC request to a response (result or error)."""
        if method == "initialize":
            return self._result(req_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "serverInfo": {"name": "nexus", "version": "0.1"},
            })
        if method == "tools/list":
            return self._result(req_id, {"tools": self.list_tools()})
        if method == "tools/call":
            if not isinstance(params, dict) or not isinstance(params.get("name"), str):
                return self._error(req_id, -32602, "invalid params")
            return self._result(req_id, self.call_tool(params["name"], params.get("arguments") or {}))
        return self._error(req_id, -32601, f"method not found: {method}")

    def serve(self, stdin=None, stdout=None) -> None:
        """Run the stdio JSON-RPC loop (line-delimited). A malformed line is a
        parse-error response; a closed stdin ends the loop."""
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError:
                stdout.write(json.dumps(self._error(None, -32700, "parse error")) + "\n")
                stdout.flush()
                continue
            resp = self.handle(req.get("method"), req.get("params", {}), req.get("id"))
            stdout.write(json.dumps(resp) + "\n")
            stdout.flush()

    # -- helpers -------------------------------------------------------------
    @staticmethod
    def _text(text: str) -> list[dict]:
        return [{"type": "text", "text": text}]

    def _text_result(self, text: str) -> dict:
        return {"content": self._text(text)}

    @staticmethod
    def _result(req_id, result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    @staticmethod
    def _error(req_id, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
