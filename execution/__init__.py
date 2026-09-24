"""The execution plane — where Decisions become Actions.

The control plane returns contracts (a Decision, a ToolCall); it never acts.
This layer turns a ToolCall into a ToolResult and a ModelRequest into a
ModelResponse, through the Executor capability — and, crucially, it emits an
event BEFORE any externally observable step is considered complete, so a killed
worker can still answer "what does Nexus know happened?".

The reference executor (execution/fake.py) is deterministic and side-effect-free;
real side effects (subprocesses, network, model SDKs, MCP) are later adapters
behind the same Executor protocol.
"""
