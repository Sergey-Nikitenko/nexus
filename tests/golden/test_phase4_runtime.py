"""Phase 4.1 golden task — the runtime composition root.

One composition root wires injected components into a working system, and the
application surface (`ask` / `run_one` / `task` / `events`) is identical whether
the components are fakes or real. Applications compose Nexus; components never
discover each other through global state.

Run:  py tests/golden/test_phase4_runtime.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import ModelResponse, Risk, TaskStatus, ToolCall  # noqa: E402
from core.events import EventType  # noqa: E402
from control.tools import ToolRegistry, ToolSpec  # noqa: E402
from execution.durable import DurableEventBus  # noqa: E402
from execution.fake import FakeExecutor  # noqa: E402
from execution.queue import TaskQueue  # noqa: E402
from apps.runtime import NexusRuntime  # noqa: E402
from knowledge.inmemory import ComposedRetriever  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def build_runtime(tmp, executor):
    bus = DurableEventBus(os.path.join(tmp, "events.db"))
    queue = TaskQueue(os.path.join(tmp, "queue.db"), bus=bus)
    retriever = ComposedRetriever()
    retriever.ingest("filesystem", "docs/x.md", "v1", "context for the task")
    tools = ToolRegistry()
    tools.register(ToolSpec("github.read_file", "read a file", Risk.READ))
    return NexusRuntime(retriever=retriever, executor=executor, queue=queue,
                        event_bus=bus, tools=tools), bus


def main():
    print("Phase 4.1 golden task: the runtime composition root")
    script = [
        ModelResponse(model="fake", content="", success=True, tool_calls=[
            ToolCall(tool_name="github.read_file", arguments={"path": "a.py"})]),
        ModelResponse(model="fake", content="done", success=True),
    ]

    # runtime A: fake executor + reference retriever + durable events + SQLite queue
    tmp = tempfile.mkdtemp()
    runtime, bus = build_runtime(tmp, FakeExecutor(model_script=list(script)))

    task_id = runtime.ask("fix the thing")
    check(runtime.task(task_id).status == TaskStatus.QUEUED,
          "ask submits a queued task and returns its id (async by default)")

    outcome = runtime.run_one()
    check(outcome is not None and outcome.answer == "done",
          "run_one drains the task through the composed worker")
    check(runtime.task(task_id).status == TaskStatus.DONE, "the task reaches DONE")

    # the surface reads durable events, not internals
    events = runtime.events(task_id)
    check(any(e.event_type == EventType.TASK_COMPLETED for e in events),
          "the surface observes the completion through the durable event log")

    # no global state: a second, independent runtime is unaffected
    tmp2 = tempfile.mkdtemp()
    runtime2, _ = build_runtime(tmp2, FakeExecutor(model_script=list(script)))
    task_id2 = runtime2.ask("another task")
    check(runtime2.task(task_id2).status == TaskStatus.QUEUED
          and runtime.task(task_id).status == TaskStatus.DONE,
          "two runtimes coexist independently (no shared global state)")
    check(task_id != task_id2, "each runtime mints its own task ids")

    print("\nPASS: Phase 4.1 runtime composition holds (applications compose Nexus).")


if __name__ == "__main__":
    main()
