"""MCP (Model Context Protocol) tool adapter — implementation #3, anticlimactic.

The adapter OWNS MCP's vocabulary (JSON-RPC 2.0 over stdio: `initialize`,
`tools/list`, `tools/call`, `server_name`, `content`, `isError`). None of it
crosses the boundary:

    ToolCall -> McpToolExecutor -> MCP server -> ToolResult
    tools/list -> discovery -> ToolDefinition (a Nexus contract)

The orchestrator has no idea the tool ran over MCP — same `Executor` contract as
the fake and subprocess adapters. Failure semantics follow AD-010: an MCP
`isError` result is an expected failure (ToolResult success=False); a transport/
config/launch failure raises.
"""
from __future__ import annotations

import json
import subprocess

from core.contracts import ToolCall, ToolDefinition, ToolResult


class McpToolExecutor:
    """Executes Nexus ToolCalls against an MCP server over stdio (JSON-RPC 2.0)."""

    def __init__(self, argv: list[str]) -> None:
        self._argv = list(argv)
        self._proc = None
        self._next_id = 0

    def start(self) -> None:
        """Spawn the MCP server and complete the initialize handshake."""
        self._proc = subprocess.Popen(
            self._argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, shell=False, bufsize=1)
        self._rpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "nexus", "version": "0.1"},
        })

    def close(self) -> None:
        if self._proc is None:
            return
        try:
            self._proc.stdin.close()
        except Exception:
            pass
        self._proc.terminate()
        try:
            self._proc.wait(timeout=5)
        except Exception:
            self._proc.kill()
        self._proc = None

    # -- discovery: MCP tool definitions -> Nexus ToolDefinition contracts ----
    def discover(self) -> list[ToolDefinition]:
        result = self._rpc("tools/list", {})
        tools = result.get("tools", []) if isinstance(result, dict) else []
        return [
            ToolDefinition(
                name=t.get("name", ""),
                description=t.get("description", ""),
                input_schema=t.get("inputSchema", {}) if isinstance(t.get("inputSchema"), dict) else {},
            )
            for t in tools if isinstance(t, dict)
        ]

    # -- execution: ToolCall -> MCP invocation -> ToolResult -----------------
    def execute_tool(self, call: ToolCall) -> ToolResult:
        result = self._rpc("tools/call", {"name": call.tool_name, "arguments": call.arguments})
        if not isinstance(result, dict):
            return ToolResult(tool_call=call, success=False, error="invalid MCP result")
        # expected tool failure (AD-010): the MCP tool reported isError
        if result.get("isError"):
            text = self._extract_text(result)
            return ToolResult(tool_call=call, success=False,
                              error=text or "tool returned an error")
        # translate provider -> Nexus: keep only the text, drop MCP's envelope
        return ToolResult(tool_call=call, success=True, output={"text": self._extract_text(result)})

    @staticmethod
    def _extract_text(result: dict) -> str:
        content = result.get("content", [])
        if not isinstance(content, list):
            return ""
        return "\n".join(
            c.get("text", "") for c in content
            if isinstance(c, dict) and c.get("type") == "text"
        )

    # -- MCP transport (owned vocabulary, never leaks) ----------------------
    def _rpc(self, method: str, params: dict) -> dict:
        if self._proc is None:
            raise RuntimeError("MCP server not started")  # config/launch failure
        self._next_id += 1
        req = {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params}
        try:
            self._proc.stdin.write(json.dumps(req) + "\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise RuntimeError(f"MCP server closed during {method}") from exc
        line = self._proc.stdout.readline()
        if not line:
            raise RuntimeError(f"MCP server closed before responding to {method}")  # transport
        resp = json.loads(line)
        if resp.get("error"):
            raise RuntimeError(f"MCP error on {method}: {resp['error']}")  # protocol failure
        return resp.get("result", {})
