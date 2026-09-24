"""Phase 1 composition golden task — router proposes, policy disposes.

The invariant: the router NEVER bypasses policy. The router picks the best model
(capability/cost); the policy is the authority on what is ALLOWED.

Cases:
  private + huge context  -> router picks cloud -> policy DENIES (private data)
  private + small context -> router picks local -> policy ALLOWS
  write tool              -> policy APPROVAL_REQUIRED (unchanged, composed through)

Plus control-plane purity: same input -> same output, and no mutation of the
registries (no hidden side effects).

Run:  py tests/golden/test_phase1_composition.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import Risk  # noqa: E402
from control.models import ModelRegistry, ModelSpec  # noqa: E402
from control.policy import PolicyEngine, PolicyRules, PolicyVerdict  # noqa: E402
from control.router import Router, RoutingRequest  # noqa: E402
from control.tools import ToolRegistry, ToolSpec  # noqa: E402


def build():
    reg = ModelRegistry()
    reg.register(ModelSpec("ollama/qwen", "local", context_window=32_000,
                           cost_per_1k=0.0, private=True, supports_tools=True))
    reg.register(ModelSpec("gpt-4o", "cloud", context_window=128_000,
                           cost_per_1k=0.03, private=False, supports_tools=True))
    tools = ToolRegistry()
    tools.register(ToolSpec("github.read_file", "read a file", Risk.READ))
    tools.register(ToolSpec("github.create_pr", "open a PR", Risk.WRITE))
    policy = PolicyEngine(PolicyRules())  # default: private data never leaves the machine
    return Router(reg), policy, tools


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def main():
    print("Phase 1 composition: router proposes, policy disposes")
    router, policy, tools = build()

    # private + huge context -> router picks cloud -> policy DENIES
    req = RoutingRequest(task="analyze large document", context_needed=100_000, private=True)
    decision = router.route(req)
    check(decision.provider == "cloud", "router proposes cloud (no local model fits)")
    verdict = policy.decide_model(decision, private=req.private)
    check(verdict == PolicyVerdict.DENY, "policy DENIES: private data may not go to cloud")
    check(decision.provider == "cloud" and verdict == PolicyVerdict.DENY,
          "the router's choice does NOT override policy (router never bypasses policy)")

    # private + small context -> router picks local -> policy ALLOWS
    req2 = RoutingRequest(task="coding", context_needed=18_000, private=True, needs_tools=True)
    d2 = router.route(req2)
    v2 = policy.decide_model(d2, private=req2.private)
    check(d2.provider == "local" and v2 == PolicyVerdict.ALLOW,
          "local model for private data -> allow")

    # write tool -> policy APPROVAL_REQUIRED (unchanged, composed through)
    v3 = policy.decide_tool(tools.get("github.create_pr"))
    check(v3 == PolicyVerdict.APPROVAL_REQUIRED, "write tool -> approval required (unchanged)")

    # purity: same input -> same decision, no mutation (no hidden side effects)
    d_a = router.route(req2)
    d_b = router.route(req2)
    check(d_a == d_b, "router is pure: same request -> same Decision")
    before = sorted(m.name for m in tools.list())
    policy.decide_tool(tools.get("github.create_pr"))
    check(sorted(m.name for m in tools.list()) == before, "policy is pure: no registry mutation")

    print("\nPASS: Phase 1 composition holds (router never bypasses policy).")


if __name__ == "__main__":
    main()
