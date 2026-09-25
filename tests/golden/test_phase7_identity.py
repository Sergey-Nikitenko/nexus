"""Phase 7.1 golden task — identity contracts (User / Agent / Model).

Freeze the three identities before continuity, context adaptation, or routing can
invent their own. The invariant (AD-035): a MODEL swap changes only ModelIdentity
— UserIdentity and AgentIdentity are untouched.

Proves:
1. all three identities survive manifest persistence;
2. a fresh process reconstructs them;
3. manifest serialization is Nexus-level data only (no provider config);
4. changing the model changes ONLY ModelIdentity;
5. user/agent identity stays stable across model swaps;
6. 6.1/6.3 fingerprint behavior stays intact — a model change is a legitimate
   reproducibility-input difference, while user/agent are context, not inputs.

Run:  py tests/golden/test_phase7_identity.py
"""
import os
import sys
import tempfile
from dataclasses import asdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import (  # noqa: E402
    ModelIdentity, ModelResponse, ReplayStatus, Risk, ToolCall, ToolResult,
)
from control.policy import PolicyEngine, PolicyRules  # noqa: E402
from control.tools import ToolRegistry, ToolSpec  # noqa: E402
from execution.durable import DurableEventBus  # noqa: E402
from execution.queue import TaskQueue  # noqa: E402
from execution.run_records import RunRecordStore  # noqa: E402
from apps.runtime import NexusRuntime  # noqa: E402
from knowledge.inmemory import ComposedRetriever  # noqa: E402
from observability.replay import compare  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


MODEL_A = ModelIdentity(model_id="fake-deterministic", family="fake", version="1")
MODEL_B = ModelIdentity(model_id="other-model", family="other", version="1")


class ModelExecutor:
    """A stateless executor that reports its ModelIdentity (never the SDK object)."""

    def __init__(self, model_identity):
        self.model_identity = model_identity

    def run_model(self, request):
        if any(m.get("role") == "tool" for m in request.messages):
            return ModelResponse(model=self.model_identity.model_id, content="done", success=True)
        return ModelResponse(model=self.model_identity.model_id, content="", success=True,
                             tool_calls=[ToolCall(tool_name="github.read_file", arguments={})])

    def execute_tool(self, call):
        return ToolResult(tool_call=call, success=True, output={"ok": True})


def build(model_identity):
    d = tempfile.mkdtemp()
    bus = DurableEventBus(os.path.join(d, "events.db"))
    queue = TaskQueue(os.path.join(d, "queue.db"), bus=bus)
    records = RunRecordStore(os.path.join(d, "records.db"))
    retriever = ComposedRetriever()
    retriever.ingest("filesystem", "docs/x.md", "v1", "context")
    tools = ToolRegistry()
    tools.register(ToolSpec("github.read_file", "read a file", Risk.READ))
    runtime = NexusRuntime(retriever=retriever, executor=ModelExecutor(model_identity),
                           queue=queue, event_bus=bus, tools=tools, run_records=records,
                           policy=PolicyEngine(PolicyRules()))
    return runtime, records


def run(model_identity):
    runtime, records = build(model_identity)
    runtime.ask("fix the thing", user="alice", agent="researcher")
    outcome = runtime.run_one()
    return outcome, records


def main():
    print("Phase 7.1 golden task: identity contracts")
    out_a, records_a = run(MODEL_A)
    manifest_a = out_a.manifest

    # --- 1. the three identities are on the manifest ------------------------
    check(manifest_a.user.key == "user/alice", "UserIdentity is captured")
    check(manifest_a.agent.key == "agent/researcher@1", "AgentIdentity is captured (role + version)")
    check(manifest_a.model.key == "model/fake-deterministic@1",
          "ModelIdentity is captured (family + version)")

    # --- 2. serialization is Nexus-level data only --------------------------
    m = asdict(manifest_a)
    check(set(m["model"]) == {"model_id", "family", "version"},
          "ModelIdentity serializes to identity fields only (no API key/endpoint/SDK)")
    check(set(m["user"]) == {"user_id"} and set(m["agent"]) == {"agent_id", "role", "version"},
          "user/agent serialization carries no provider or auth fields")

    # --- 3. a fresh process reconstructs them -------------------------------
    records_a.close()
    records2 = RunRecordStore(records_a.path)
    rec = records2.get(out_a.run.run_id)
    check(rec["manifest"]["user"]["user_id"] == "alice"
          and rec["manifest"]["agent"]["agent_id"] == "researcher"
          and rec["manifest"]["model"]["model_id"] == "fake-deterministic",
          "a fresh process reconstructs all three identities from the manifest")
    records2.close()

    # --- 4. model swap changes ONLY ModelIdentity ---------------------------
    out_b, records_b = run(MODEL_B)
    manifest_b = out_b.manifest
    check(manifest_b.model.key == "model/other-model@1", "the model identity changed")
    check(manifest_b.user == manifest_a.user, "UserIdentity is unchanged across a model swap")
    check(manifest_b.agent == manifest_a.agent, "AgentIdentity is unchanged across a model swap")
    check(manifest_b.model != manifest_a.model, "only the ModelIdentity differs")

    # --- 5. 6.1/6.3 fingerprint behavior stays intact -----------------------
    report = compare(manifest_a, out_a.events, manifest_b, out_b.events)
    check(report.status == ReplayStatus.REPRODUCIBLE_WITH_DIFFERENCES,
          "a model change is a reproducibility difference")
    check(any(f == "model" for f, _, _ in report.input_differences),
          "the difference names 'model'")
    check(not any(f in ("user", "agent") for f, _, _ in report.input_differences),
          "user/agent are context, not reproducibility inputs")

    records_b.close()
    print("\nPASS: Phase 7.1 identity contracts hold (model swap changes only ModelIdentity).")


if __name__ == "__main__":
    main()
