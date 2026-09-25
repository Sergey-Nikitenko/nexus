"""Phase 6.1 golden task — run identity + deterministic replay.

Reproducibility is a CONTRACT: a run is reproducible only when Nexus persisted
the INPUTS that define it (the RunManifest) alongside the OUTPUTS (the event
log). This test proves:

1. a run captures an immutable RunManifest (inputs) BEFORE execution;
2. the manifest is Nexus vocabulary — no provider object, no SDK config;
3. replaying the SAME inputs yields a MATCHING semantic trace (not byte-identical:
   event ids / timestamps / run ids legitimately differ);
4. changing ONE input (the knowledge snapshot) is reported as exactly that —
   "knowledge changed", with the first divergent event named.

The loop exercises the full machinery: retrieve -> model -> policy -> tool ->
verify -> replan -> second attempt -> answer.

Run:  py tests/golden/test_phase6_replay.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import (  # noqa: E402
    Evaluation, ModelIdentity, ModelResponse, ReplayStatus, Risk, Task, ToolCall,
    new_id,
)
from core.events import EventType  # noqa: E402
from control.evaluator import FakeEvaluator  # noqa: E402
from control.policy import PolicyEngine, PolicyRules  # noqa: E402
from control.tools import ToolRegistry, ToolSpec  # noqa: E402
from execution.fake import FakeExecutor  # noqa: E402
from execution.orchestrator import Orchestrator  # noqa: E402
from knowledge.inmemory import ComposedRetriever  # noqa: E402
from observability.replay import compare, fingerprint  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


KNOWLEDGE_V1 = "the authentication middleware has a bug in token verification"
KNOWLEDGE_V2 = KNOWLEDGE_V1 + " " + " ".join(["word"] * 90)  # more chunks -> retrieval differs


def make_script():
    return [
        ModelResponse(model="fake", content="", success=True, tool_calls=[
            ToolCall(tool_name="github.read_file", arguments={"path": "middleware.py"})]),
        ModelResponse(model="fake", content="attempt 1 answer", success=True),
        ModelResponse(model="fake", content="", success=True, tool_calls=[
            ToolCall(tool_name="github.read_file", arguments={"path": "middleware.py"})]),
        ModelResponse(model="fake", content="attempt 2 answer", success=True),
    ]


def build(knowledge_text, version):
    """A fresh runtime (fresh bus/script/evaluator) with the given knowledge."""
    retriever = ComposedRetriever()
    retriever.ingest("filesystem", "docs/middleware.md", version, knowledge_text)
    tools = ToolRegistry()
    tools.register(ToolSpec("github.read_file", "read a file", Risk.READ))
    policy = PolicyEngine(PolicyRules(version="policy@1"))
    evaluator = FakeEvaluator([
        Evaluation(passed=False, reason="not fixed yet", replan_required=True),
        Evaluation(passed=True, reason="fixed"),
    ])
    return Orchestrator(retriever=retriever, executor=FakeExecutor(model_script=make_script()),
                        policy=policy, tools=tools, evaluator=evaluator, max_replans=2)


def run(knowledge_text, version):
    out = build(knowledge_text, version).run(
        Task(task_id=new_id("task"), title="fix authentication bug"))
    return out.manifest, out.events


def main():
    print("Phase 6.1 golden task: run identity + deterministic replay")

    # --- 1. a run captures an immutable manifest BEFORE execution -----------
    manifest_a, events_a = run(KNOWLEDGE_V1, "v1")
    check(manifest_a is not None, "a run captures a RunManifest")
    kinds = [e.event_type for e in events_a]
    check(EventType.RUN_MANIFEST in kinds,
          "the manifest is emitted as a run.manifest event")
    check(kinds.index(EventType.RUN_MANIFEST) < kinds.index(EventType.RETRIEVAL_REQUESTED),
          "the manifest is captured BEFORE the first capability decision")

    # the manifest is Nexus vocabulary (strings/ints), not a provider object
    check(isinstance(manifest_a.knowledge, str) and isinstance(manifest_a.model, ModelIdentity)
          and isinstance(manifest_a.max_replans, int),
          "the manifest is Nexus vocabulary (strings + identity contracts, no provider object)")
    check(manifest_a.model.key == "model/fake-deterministic@1",
          "model identity is a ModelIdentity contract")
    check("policy@1" in manifest_a.policy, "policy identity/version is captured")

    # --- 2. replay the SAME inputs -> MATCH (not byte-identical) ------------
    manifest_b, events_b = run(KNOWLEDGE_V1, "v1")
    report = compare(manifest_a, events_a, manifest_b, events_b)
    check(report.status == ReplayStatus.REPRODUCIBLE, "same inputs -> REPRODUCIBLE")
    check(report.input_differences == [], "no input differences")
    check(fingerprint(events_a) == fingerprint(events_b),
          "semantic fingerprints match (while event ids/timestamps/run ids differ)")

    # --- 3. change ONE input -> the difference is NAMED ---------------------
    manifest_c, events_c = run(KNOWLEDGE_V2, "v2")
    report2 = compare(manifest_a, events_a, manifest_c, events_c)
    check(report2.status == ReplayStatus.REPRODUCIBLE_WITH_DIFFERENCES,
          "changed knowledge -> REPRODUCIBLE_WITH_DIFFERENCES")
    check(any(f == "knowledge" for f, _, _ in report2.input_differences),
          "the difference names the changed input (knowledge), not just 'trace differs'")
    check(report2.first_divergent_event == "retrieval.completed",
          "the first divergent event is retrieval.completed (the retrieval result changed)")
    check(fingerprint(events_a) != fingerprint(events_c),
          "the fingerprint differs when an input changes (not replaying cached results)")

    print("\nPASS: Phase 6.1 run identity + deterministic replay holds.")


if __name__ == "__main__":
    main()
