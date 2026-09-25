"""Conformance: the MCP adapter boundary holds.

The MCP adapter OWNS MCP's vocabulary; only Nexus contracts cross:
- ToolCall -> ToolResult (serializable; provider-specific fields dropped).
- tools/list -> ToolDefinition (a Nexus contract, not an MCP object).
- isError -> ToolResult(success=False) (AD-010: an expected tool failure).
- transport/config/launch failure -> raise (AD-010).

This is the MCP twin of the 3.3 OpenAI-shaped-payload test.

Run:  py tests/conformance/test_mcp_boundary.py
"""
import dataclasses
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from fixtures import fake_mcp_server_argv  # noqa: E402

from core.contracts import ToolCall, ToolDefinition, ToolResult  # noqa: E402
from integrations.mcp import McpToolExecutor  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def main() -> None:
    print("Conformance: MCP adapter boundary holds")
    mcp = McpToolExecutor(fake_mcp_server_argv())
    mcp.start()

    # discovery -> Nexus ToolDefinition contracts, no MCP object
    defs = mcp.discover()
    check(all(isinstance(d, ToolDefinition) for d in defs),
          "discover returns Nexus ToolDefinition contracts")
    check({d.name for d in defs} == {"github.read_file", "github.force_push"},
          "MCP tools became Nexus tool definitions (no MCP objects leaked)")

    # success -> ToolResult with ONLY Nexus-level data (provider fields dropped)
    tr = mcp.execute_tool(ToolCall(tool_name="github.read_file", arguments={"path": "a.py"}))
    check(isinstance(tr, ToolResult) and tr.success, "ToolCall -> ToolResult (success)")
    check(tr.output == {"text": "read a.py"},
          "only Nexus-level data crosses (structuredContent/_server_session dropped)")
    json.dumps(dataclasses.asdict(tr))  # raises if a non-serializable object leaked

    # expected tool failure -> ToolResult(success=False), not an exception
    tf = mcp.execute_tool(ToolCall(tool_name="github.force_push", arguments={}))
    check(tf.success is False and "rejected" in tf.error,
          "MCP isError -> ToolResult(success=False) (expected failure, AD-010)")

    mcp.close()

    # transport/config failure -> raise (AD-010)
    try:
        McpToolExecutor(fake_mcp_server_argv()).execute_tool(
            ToolCall(tool_name="github.read_file", arguments={}))
        check(False, "not-started executor -> raise")
    except RuntimeError:
        check(True, "transport/config failure raises (AD-010)")

    print("\nPASS: the MCP adapter boundary is implementation-agnostic (only contracts cross).")


if __name__ == "__main__":
    main()
