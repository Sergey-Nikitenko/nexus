"""Model registry — the catalog of reasoning engines Nexus can route to.

A model is described by a ModelSpec (name, provider, context window, cost,
privacy, tool support, capabilities). The registry holds no behavior — the
router (control/router.py) is the only thing that reads it.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ModelSpec:
    name: str
    provider: str              # "local" | "cloud"
    context_window: int        # tokens
    cost_per_1k: float = 0.0   # dollars; 0.0 = local inference
    private: bool = True       # does data stay on the machine?
    supports_tools: bool = True
    capabilities: list[str] = field(default_factory=list)


class ModelRegistry:
    def __init__(self) -> None:
        self._models: dict[str, ModelSpec] = {}

    def register(self, spec: ModelSpec) -> None:
        self._models[spec.name] = spec

    def get(self, name: str) -> ModelSpec:
        return self._models[name]

    def list(self) -> list[ModelSpec]:
        return list(self._models.values())
