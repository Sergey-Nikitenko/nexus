"""Phase 4.4 golden task — the WebSocket event stream.

The WebSocket subscribes to the event bus (never the orchestrator), replays the
durable history, tails live events, filters by run_id at the edge, and sends
projected (Nexus-level) data. Removing every client never changes the execution
result or the durable event log — observability is genuinely downstream.

Run:  py tests/golden/test_phase4_ws.py
"""
import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi.testclient import TestClient  # noqa: E402

from core.contracts import (  # noqa: E402
    Event, ModelResponse, Risk, Task, ToolCall, new_id, utcnow,
)
from core.events import EventType  # noqa: E402
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
    retriever.ingest("filesystem", "docs/x.md", "v1", "context")
    tools = ToolRegistry()
    tools.register(ToolSpec("github.read_file", "read a file", Risk.READ))
    runtime = NexusRuntime(retriever=retriever, executor=FakeExecutor(model_script=SCRIPT),
                           queue=queue, event_bus=bus, tools=tools)
    return runtime, bus


def _ev(bus, event_type, run_id, payload=None):
    return Event(event_id=new_id("evt"), event_type=event_type, timestamp=utcnow(),
                 run_id=run_id, task_id="t1", component="x", status="success",
                 payload=payload or {})


def main():
    print("Phase 4.4 golden task: the WebSocket event stream")
    tmp = tempfile.mkdtemp()

    # --- Part A: a completed run replays deterministically, then closes -------
    runtime, bus = build_runtime(tmp)
    client = TestClient(create_app(runtime))
    runtime.ask("fix the thing")
    run = runtime.run_one().run
    run_id = run.run_id

    def drain(url):
        with client.websocket_connect(url) as ws:
            msgs = []
            while True:
                msg = ws.receive_json()
                msgs.append(msg)
                if msg.get("type") == "complete":
                    break
            return msgs

    replay = drain(f"/ws/runs/{run_id}")
    kinds = {m.get("type") for m in replay}
    check("run" in kinds and "plan" in kinds and "decision" in kinds
          and "tool" in kinds and "evaluation" in kinds,
          "the completed run replays its projected trace")
    check(replay[-1]["type"] == "complete", "the completed run closes cleanly after replay")
    check(drain(f"/ws/runs/{run_id}") == replay, "replay is deterministic")

    # --- Part B: live tail + filter (two runs) --------------------------------
    tmp2 = tempfile.mkdtemp()
    runtime2, bus2 = build_runtime(tmp2)
    client2 = TestClient(create_app(runtime2))
    with client2.websocket_connect("/ws/runs/rA") as ws:
        def publish():
            time.sleep(0.25)  # let the server subscribe before publishing
            bus2.publish(_ev(bus2, EventType.RUN_STARTED, "rB"))
            bus2.publish(_ev(bus2, EventType.RUN_STARTED, "rA"))
            bus2.publish(_ev(bus2, EventType.RUN_COMPLETED, "rA", {"answer": "x"}))

        t = threading.Thread(target=publish)
        t.start()
        msgs = []
        while True:
            msg = ws.receive_json()
            msgs.append(msg)
            if msg.get("type") == "complete":
                break
        t.join()
    event_msgs = [m for m in msgs if m.get("event")]
    check([m.get("run_id") for m in event_msgs] == ["rA", "rA"],
          "the client received only its selected run (rB filtered out)")
    check([m.get("event") for m in event_msgs]
          == [EventType.RUN_STARTED, EventType.RUN_COMPLETED],
          "the client tailed live events after replay")

    # --- Part C: removing every client does not change execution --------------
    runtime3, bus3 = build_runtime(tempfile.mkdtemp())
    client3 = TestClient(create_app(runtime3))
    tid = runtime3.ask("another task")
    outcome3 = runtime3.run_one()
    events_before = len(bus3.load_events(task_id=tid))
    with client3.websocket_connect(f"/ws/runs/{outcome3.run.run_id}") as ws:
        while True:
            m = ws.receive_json()
            if m.get("type") == "complete":
                break
    check(len(bus3.load_events(task_id=tid)) == events_before,
          "connecting + draining a WebSocket adds no events (observability is downstream)")
    check(runtime3.task(tid).status.value == "done", "the execution result is unchanged")

    # --- Part D: no provider-specific payload escapes -------------------------
    check(all(k in ("type", "event", "event_id", "parent_event_id", "status",
                    "answer", "tool", "verdict", "risk", "executed", "completed",
                    "success", "interrupted", "request_id", "model", "passed",
                    "reason", "attempt", "worker_id", "chunks", "run_id",
                    "task_id", "timestamp", "call_id", "detail", "name")
              for m in replay for k in m.keys()),
          "the wire format carries only Nexus-level keys (no provider vocabulary)")

    print("\nPASS: Phase 4.4 WebSocket holds (the orchestrator never knew a browser existed).")


if __name__ == "__main__":
    main()
