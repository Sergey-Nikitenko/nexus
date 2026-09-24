"""Router — turns a task's requirements into an explainable Decision.

The router returns a Decision (a Phase 0 contract): model + provider + WHY +
constraints. The reasons list is what the dashboard's "Why" panel renders later,
so the router's whole job is to make its choice defensible, not just correct.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.contracts import Decision
from .models import ModelRegistry, ModelSpec


@dataclass
class RoutingRequest:
    task: str                 # e.g. "coding", "summarize", "classification"
    context_needed: int = 0   # tokens the request needs
    private: bool = False     # must data stay on-machine?
    needs_tools: bool = False
    max_cost: float = float("inf")


class Router:
    def __init__(self, registry: ModelRegistry) -> None:
        self.registry = registry

    def route(self, req: RoutingRequest) -> Decision:
        # Hard filters: no model outside these bounds is ever eligible.
        fits = [
            m for m in self.registry.list()
            if m.context_window >= req.context_needed
            and (not req.needs_tools or m.supports_tools)
            and m.cost_per_1k <= req.max_cost
        ]
        # Privacy is a soft constraint: preferred, but relaxable on fallback.
        private_fits = [m for m in fits if not req.private or m.private]

        escalated = False
        if private_fits:
            chosen = min(private_fits, key=lambda m: m.cost_per_1k)
        elif fits:
            chosen = min(fits, key=lambda m: m.cost_per_1k)
            escalated = True
        else:
            raise RuntimeError("no model satisfies the request")

        return Decision(
            model=chosen.name,
            provider=chosen.provider,
            reasons=self._reasons(chosen, req, escalated),
            constraints={
                "max_tokens": min(chosen.context_window, 4096),
                "timeout_ms": 30000,
            },
        )

    @staticmethod
    def _reasons(chosen: ModelSpec, req: RoutingRequest, escalated: bool) -> list[str]:
        reasons = [f"task requires {req.task}"]
        if req.private and chosen.private:
            reasons.append("data is private -> local (on-machine) model")
        if escalated:
            reasons.append("no local model satisfies the requirements -> escalated to cloud")
        reasons.append(f"context requirement {req.context_needed} <= window {chosen.context_window}")
        if req.needs_tools:
            reasons.append("tool calling supported")
        reasons.append("$0 inference cost" if chosen.cost_per_1k == 0
                       else f"${chosen.cost_per_1k}/1k tokens")
        return reasons
