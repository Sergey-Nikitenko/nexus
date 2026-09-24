"""Subprocess tool execution — the first real Executor implementation.

    ToolCall ──► SubprocessToolExecutor ──► OS process ──► ToolResult

The model NEVER chooses an executable. A ToolCall names a LOGICAL tool
("formatter"); the registry maps that name to an argv template (SubprocessSpec).
Arguments are substituted into declared `{placeholders}` only — never shell.

Failure semantics (AD-010):
- Expected tool outcome (non-zero exit, timeout) -> ToolResult(success=False, ...).
- Launch failure (unknown tool, missing executable, malformed spec) -> raise.

No subprocess object, Popen, pipe, or raw return code crosses the Executor
boundary: the caller gets a ToolResult whose output is bounded, plain data.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass

from core.contracts import ToolCall, ToolResult


@dataclass
class SubprocessSpec:
    """How one logical tool maps to an OS process.

    Lives in the execution layer, not in ToolCall: the tool NAME is the contract,
    the executable is the implementation. `argv` is an argv list (shell=False);
    `{key}` placeholders are filled from ToolCall.arguments."""
    name: str
    argv: list[str]
    timeout_seconds: float = 30.0
    max_output_bytes: int = 64 * 1024


def _bound(text: str, limit: int) -> str:
    """Bound captured output so an arbitrary subprocess cannot dump unlimited
    stdout/stderr into an event or trace. Truncation is explicit, never silent."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"... [truncated at {limit} bytes]"


class SubprocessToolExecutor:
    """Runs registered tools as subprocesses (argv list, shell=False)."""

    def __init__(self, specs: list[SubprocessSpec] | None = None) -> None:
        self._specs: dict[str, SubprocessSpec] = {}
        for spec in specs or []:
            self.register(spec)

    def register(self, spec: SubprocessSpec) -> None:
        self._specs[spec.name] = spec

    def _argv(self, spec: SubprocessSpec, arguments: dict) -> list[str]:
        argv = []
        for part in spec.argv:
            if "{" in part:
                try:
                    part = part.format(**arguments)
                except KeyError as exc:
                    # a missing declared argument is a programming error, not a tool outcome
                    raise ValueError(f"tool {spec.name}: missing argument {exc.args[0]}")
            argv.append(part)
        return argv

    def execute_tool(self, call: ToolCall) -> ToolResult:
        spec = self._specs.get(call.tool_name)
        if spec is None:
            # programming/configuration error, not a tool outcome (AD-010)
            raise ValueError(f"unknown tool: {call.tool_name}")

        argv = self._argv(spec, call.arguments)

        # launch failure (missing executable / OS error) propagates as an exception
        proc = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, shell=False,
        )
        try:
            out, err = proc.communicate(timeout=spec.timeout_seconds)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()  # reap; bounded below
            return ToolResult(
                tool_call=call,
                success=False,
                output={
                    "stdout": _bound(out or "", spec.max_output_bytes),
                    "stderr": _bound(err or "", spec.max_output_bytes),
                },
                error=f"timeout after {spec.timeout_seconds:g}s",
            )

        out_b = _bound(out or "", spec.max_output_bytes)
        err_b = _bound(err or "", spec.max_output_bytes)
        if proc.returncode == 0:
            return ToolResult(tool_call=call, success=True,
                              output={"stdout": out_b, "stderr": err_b})
        # expected failure (non-zero exit) -> ToolResult, never an exception
        return ToolResult(
            tool_call=call,
            success=False,
            output={"stdout": out_b, "stderr": err_b},
            error=f"exit status {proc.returncode}" + (f": {err_b[:200]}" if err_b else ""),
        )
