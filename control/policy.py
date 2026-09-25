"""Policy engine — authority, independent of the LLM.

This is the reflex arc: it decides DENY / ALLOW / APPROVAL_REQUIRED purely from
the tool's risk level and the configured rules. It takes NO model input and it
is a pure function — the model can request a tool, but it can never talk itself
into permission. That boundary is the whole point.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.contracts import Decision, PolicyVerdict, Risk
from .tools import ToolSpec


@dataclass
class PolicyRules:
    """What the policy does per risk level, plus explicit overrides."""
    version: str = "policy@1"
    read: PolicyVerdict = PolicyVerdict.ALLOW
    write: PolicyVerdict = PolicyVerdict.APPROVAL_REQUIRED
    destructive: PolicyVerdict = PolicyVerdict.DENY
    allowlist: list[str] = field(default_factory=list)
    denylist: list[str] = field(default_factory=list)
    # Model policy: may private data ever be processed by a cloud model?
    allow_cloud_for_private: bool = False


class PolicyEngine:
    def __init__(self, rules: PolicyRules | None = None) -> None:
        self.rules = rules or PolicyRules()

    def decide_tool(self, tool: ToolSpec) -> PolicyVerdict:
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

    def decide_model(self, decision: Decision, *, private: bool) -> PolicyVerdict:
        """Gate a model choice. The router proposes; the policy disposes.
        Default rule: private data never leaves the machine to a cloud model.
        The router can never bypass this — the policy is the authority."""
        if private and decision.provider == "cloud" and not self.rules.allow_cloud_for_private:
            return PolicyVerdict.DENY
        return PolicyVerdict.ALLOW
