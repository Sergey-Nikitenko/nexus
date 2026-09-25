"""Phase 4.2 golden task — the thin HTTP adapter over the runtime.

POST /ask, GET /tasks/{id}, GET /traces/{id}. The HTTP layer is an edge adapter:
it delegates to NexusRuntime and projects contracts to JSON. FastAPI/Pydantic
never cross below apps/.

Run:  py tests/golden/test_phase4_api.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi.testclient import TestClient  # noqa: E402

from core.contracts import ModelResponse, Risk, TaskStatus, ToolCall  # noqa: E402
from control.tools import ToolRegistry, ToolSpec  # noqa: E402
from execution.durable import DurableEventBus  # noqa: E402
from execution.fake import FakeExecutor  # noqa: E402
from execution.queue import TaskQueue  # noqa: E402
from apps.http import create_app  # noqa: E402
from apps.runtime import NexusRuntime  # noqa: E402
from knowledge.inmemory import ComposedRetriever  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


SCRIPT = [
    ModelResponse(model="fake", content="", success=True, tool_calls=[
        ToolCall(tool_name="github.read_file", arguments={"path": "a.py"})]),
    ModelResponse(model="fake", content="done", success=True),
]


def build_runtime(tmp):
    bus = DurableEventBus(os.path.join(tmp, "events.db"))
    queue = TaskQueue(os.path.join(tmp, "queue.db"), bus=bus)
    retriever = ComposedRetriever()
    retriever.ingest("filesystem", "docs/x.md", "v1", "context for the task")
    tools = ToolRegistry()
    tools.register(ToolSpec("github.read_file", "read a file", Risk.READ))
    runtime = NexusRuntime(retriever=retriever, executor=FakeExecutor(model_script=SCRIPT),
                           queue=queue, event_bus=bus, tools=tools)
    return runtime, bus, queue


def main():
    print("Phase 4.2 golden task: thin HTTP adapter")
    tmp = tempfile.mkdtemp()
    runtime, bus, queue = build_runtime(tmp)
    client = TestClient(create_app(runtime))

    # 1. /ask is asynchronous: 202 + queued, orchestrator NOT run inline
    resp = client.post("/ask", json={"title": "fix the thing"})
    check(resp.status_code == 202, "/ask returns 202 Accepted")
    body = resp.json()
    task_id = body["task_id"]
    check(body["status"] == "queued", "/ask returns status=queued")
    check(runtime.task(task_id).status == TaskStatus.QUEUED,
          "/ask did NOT run the orchestrator inline (asynchronous)")
    check(resp.headers.get("x-task-id") == task_id,
          "correlation header preserves task_id")

    # 2. /tasks/{id} reflects durable state (queued, then done after a worker runs)
    resp = client.get(f"/tasks/{task_id}")
    check(resp.status_code == 200 and resp.json()["status"] == "queued",
          "/tasks/{id} reports queued before a worker runs")
    outcome = runtime.run_one()  # run the worker independently of HTTP
    run_id = outcome.run.run_id
    resp = client.get(f"/tasks/{task_id}")
    check(resp.status_code == 200 and resp.json()["status"] == "done",
          "/tasks/{id} reports done after the worker runs")

    # 3. /traces/{run_id} exposes the existing event history (run id != task id)
    resp = client.get(f"/traces/{run_id}")
    check(resp.status_code == 200, "/traces/{run_id} returns the run's events")
    events = resp.json()["events"]
    check(any(e["event_type"] == "run.completed" for e in events),
          "the trace includes run.completed")
    check(all(e["run_id"] == run_id for e in events),
          "every event carries its run_id (correlation preserved)")

    # 4. missing resources have deterministic semantics
    check(client.get("/tasks/nope").status_code == 404, "unknown task -> 404")
    check(client.get("/traces/nope").status_code == 404, "unknown trace -> 404")
    check(client.post("/ask", json={}).status_code == 422, "malformed /ask -> 422")

    # 5. the trace endpoint is observational (does not mutate execution)
    before = len(runtime.events(task_id=task_id))
    client.get(f"/traces/{run_id}")
    check(len(runtime.events(task_id=task_id)) == before,
          "GET /traces is observational (no event-log mutation)")

    # 6. restart: recreate the runtime; the endpoint still reports durable state
    bus.close()
    queue.close()
    runtime2, _, _ = build_runtime(tmp)
    client2 = TestClient(create_app(runtime2))
    resp = client2.get(f"/tasks/{task_id}")
    check(resp.status_code == 200 and resp.json()["status"] == "done",
          "after restart, /tasks/{id} still reports durable DONE")

    print("\nPASS: Phase 4.2 HTTP adapter holds.")


if __name__ == "__main__":
    main()
