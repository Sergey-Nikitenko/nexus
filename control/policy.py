"""Policy engine — authority, independent of the LLM.

This is the reflex arc: it decides DENY / ALLOW / APPROVAL_REQUIRED purely from
the tool's risk level and the configured rules. It takes NO model input and it
is a pure function — the model can request a tool, but it can never talk itself
into permission. That boundary is the whole point.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from core.contracts import Risk
from .tools import ToolSpec


class PolicyVerdict(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    APPROVAL_REQUIRED = "approval_required"


@dataclass
class PolicyRules:
    """What the policy does per risk level, plus explicit overrides."""
    read: PolicyVerdict = PolicyVerdict.ALLOW
    write: PolicyVerdict = PolicyVerdict.APPROVAL_REQUIRED
    destructive: PolicyVerdict = PolicyVerdict.DENY
    allowlist: list[str] = field(default_factory=list)
    denylist: list[str] = field(default_factory=list)


class PolicyEngine:
    def __init__(self, rules: PolicyRules | None = None) -> None:
        self.rules = rules or PolicyRules()

    def decide(self, tool: ToolSpec) -> PolicyVerdict:
        # Denylist wins first (an explicitly-banned tool is never callable).
        if tool.name in self.rules.denylist:
            return PolicyVerdict.DENY
        # Allowlist is the explicit exception (e.g. a safe write the org blessed).
        if tool.name in self.rules.allowlist:
            return PolicyVerdict.ALLOW
        verdict_by_risk = {
            Risk.READ: self.rules.read,
            Risk.WRITE: self.rules.write,
            Risk.DESTRUCTIVE: self.rules.destructive,
        }
        return verdict_by_risk[tool.risk]
