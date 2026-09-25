"""Tool registry — the catalog of capabilities Nexus can call.

A tool is described by a ToolSpec (name, description, risk, parameters). The
registry is just a lookup; it holds no behavior. This keeps the tool *surface*
separate from the tool *implementation* — the policy engine (and later the
orchestrator) only ever see the spec.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.contracts import Risk


@dataclass
class ToolSpec:
    name: str
    description: str
    risk: Risk
    parameters: dict[str, Any] = field(default_factory=dict)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        return self._tools[name]

    def list(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def snapshot(self) -> str:
        """Deterministic identity of the registry's contents (Nexus vocabulary,
        for a RunManifest)."""
        return ";".join(sorted(f"{s.name}({s.risk.value})" for s in self._tools.values()))
