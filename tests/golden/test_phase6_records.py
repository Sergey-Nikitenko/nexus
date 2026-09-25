"""Phase 6.3 golden task — durable run identity / fingerprints.

The RunManifest (conditions) and the semantic fingerprint (outcome) are now
first-class persisted records, retrievable by run_id alone. This proves:

1. the manifest is persisted at start and the fingerprint at terminal;
2. a fresh process (store reopened) retrieves both by run_id;
3. identical deterministic inputs -> same fingerprint despite different run ids;
4. a changed input -> different fingerprint + an attributable difference;
5. a partial/crashed run has a manifest but NO fabricated terminal fingerprint;
6. the persisted record is Nexus vocabulary only.

Runs go through the WORKER (claim -> run -> ack), because the terminal fingerprint
is written by the worker once its claim generation is confirmed current (AD-032).

Run:  py tests/golden/test_phase6_records.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import Evaluation, ModelResponse, ReplayStatus, Risk, Task, ToolCall, new_id  # noqa: E402
from core.events import EventBus, EventType  # noqa: E402
from control.evaluator import FakeEvaluator  # noqa: E402
from control.policy import PolicyEngine, PolicyRules  # noqa: E402
from control.tools import ToolRegistry, ToolSpec  # noqa: E402
from execution.fake import FakeExecutor  # noqa: E402
from execution.orchestrator import Orchestrator  # noqa: E402
from execution.queue import TaskQueue  # noqa: E402
from execution.run_records import RunRecordStore  # noqa: E402
from execution.worker import Worker  # noqa: E402
from knowledge.inmemory import ComposedRetriever  # noqa: E402
from observability.replay import compare, fingerprint  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


KNOWLEDGE_V1 = "the authentication middleware has a bug in token verification"
KNOWLEDGE_V2 = KNOWLEDGE_V1 + " " + " ".join(["word"] * 90)


def make_script():
    return [
        ModelResponse(model="fake", content="", success=True, tool_calls=[
            ToolCall(tool_name="github.read_file", arguments={"path": "middleware.py"})]),
        ModelResponse(model="fake", content="attempt 1 answer", success=True),
        ModelResponse(model="fake", content="", success=True, tool_calls=[
            ToolCall(tool_name="github.read_file", arguments={"path": "middleware.py"})]),
        ModelResponse(model="fake", content="attempt 2 answer", success=True),
    ]


def build_system(knowledge_text, version, records, bus, executor=None):
    queue = TaskQueue(os.path.join(tempfile.mkdtemp(), "queue.db"))
    retriever = ComposedRetriever()
    retriever.ingest("filesystem", "docs/middleware.md", version, knowledge_text)
    tools = ToolRegistry()
    tools.register(ToolSpec("github.read_file", "read a file", Risk.READ))
    policy = PolicyEngine(PolicyRules(version="policy@1"))
    evaluator = FakeEvaluator([
        Evaluation(passed=False, reason="not fixed yet", replan_required=True),
        Evaluation(passed=True, reason="fixed"),
    ])
    orch = Orchestrator(retriever=retriever,
                        executor=executor or FakeExecutor(model_script=make_script()),
                        policy=policy, tools=tools, evaluator=evaluator, bus=bus,
                        run_records=records, max_replans=2)
    worker = Worker(worker_id="w", queue=queue, orchestrator=orch, run_records=records)
    return queue, worker


def run_task(queue, worker, title):
    task = Task(task_id=new_id("task"), title=title)
    queue.enqueue(task)
    outcome = worker.run_one()
    return outcome, task.task_id


class CrashExecutor:
    """Proposes a READ tool then raises on execution (a normal crash, no terminal)."""

    def run_model(self, request):
        if any(m.get("role") == "tool" for m in request.messages):
            return ModelResponse(model="fake", content="done", success=True)
        return ModelResponse(model="fake", content="", success=True, tool_calls=[
            ToolCall(tool_name="github.read_file", arguments={"path": "x.py"})])

    def execute_tool(self, call):
        raise RuntimeError("boom")


def main():
    print("Phase 6.3 golden task: durable run identity / fingerprints")
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "records.db")
    records = RunRecordStore(path)

    # --- 1. manifest at start + fingerprint at terminal ---------------------
    bus_a = EventBus()
    queue_a, worker_a = build_system(KNOWLEDGE_V1, "v1", records, bus_a)
    out_a, _ = run_task(queue_a, worker_a, "fix authentication bug")
    run_id_a = out_a.run.run_id
    rec_a = records.get(run_id_a)
    check(rec_a is not None, "the RunManifest is persisted durably")
    check(rec_a["status"] == "completed" and rec_a["fingerprint"] is not None,
          "the fingerprint is persisted once the run reaches a terminal state")
    check(all(isinstance(v, (str, int, dict)) for v in rec_a["manifest"].values()),
          "the persisted manifest is Nexus vocabulary only (strings/ints/plain dicts)")
    check(rec_a["fingerprint"] == fingerprint(out_a.events),
          "the persisted fingerprint matches the canonical projection of the run's events")

    # --- 2. a fresh process retrieves it by run_id --------------------------
    records.close()
    records2 = RunRecordStore(path)
    rec_a2 = records2.get(run_id_a)
    check(rec_a2 is not None and rec_a2["fingerprint"] == rec_a["fingerprint"]
          and rec_a2["manifest"]["knowledge"] == rec_a["manifest"]["knowledge"],
          "a fresh process retrieves the manifest + fingerprint using only the run_id")

    # --- 3. identical inputs -> same fingerprint, different run ids ---------
    queue_b, worker_b = build_system(KNOWLEDGE_V1, "v1", records2, EventBus())
    out_b, _ = run_task(queue_b, worker_b, "fix authentication bug")
    run_id_b = out_b.run.run_id
    rec_b = records2.get(run_id_b)
    check(run_id_b != run_id_a, "the two runs have different runtime run ids")
    check(rec_b["fingerprint"] == rec_a2["fingerprint"],
          "identical deterministic inputs -> the SAME fingerprint (different ids/timestamps)")

    # --- 4. a changed input -> different fingerprint + attributable diff -----
    queue_c, worker_c = build_system(KNOWLEDGE_V2, "v2", records2, EventBus())
    out_c, _ = run_task(queue_c, worker_c, "fix authentication bug")
    rec_c = records2.get(out_c.run.run_id)
    check(rec_c["fingerprint"] != rec_a2["fingerprint"],
          "a changed input -> a different fingerprint")
    report = compare(rec_a2["manifest"], out_a.events, rec_c["manifest"], out_c.events)
    check(report.status == ReplayStatus.REPRODUCIBLE_WITH_DIFFERENCES
          and any(f == "knowledge" for f, _, _ in report.input_differences),
          "the difference is attributable (knowledge snapshot changed)")

    # --- 5. a partial/crashed run gets no fabricated terminal fingerprint ----
    bus_d = EventBus()
    queue_d, worker_d = build_system(KNOWLEDGE_V1, "v1", records2, bus_d,
                                     executor=CrashExecutor())
    task_d = Task(task_id=new_id("task"), title="x")
    queue_d.enqueue(task_d)
    try:
        worker_d.run_one()
        raise AssertionError("expected the run to crash")
    except RuntimeError:
        pass
    run_id_d = [e.run_id for e in bus_d.history if e.event_type == EventType.RUN_MANIFEST][0]
    rec_d = records2.get(run_id_d)
    check(rec_d is not None and rec_d["manifest"] is not None,
          "a crashed run's manifest is persisted (recorded at start)")
    check(rec_d["fingerprint"] is None and rec_d["status"] is None,
          "a partial/crashed run has NO fabricated terminal fingerprint")

    records2.close()
    print("\nPASS: Phase 6.3 durable run identity / fingerprints hold.")


if __name__ == "__main__":
    main()
