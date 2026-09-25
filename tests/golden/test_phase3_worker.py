"""Phase 3.6 golden task — durable task execution (queue + worker).

The full path: Task -> Queue -> Worker -> Orchestrator -> Events -> State, where
ownership is a durable event (never in-memory), completion/failure is durable,
and state reconstructs from the event log — even after a fresh connection.

Run:  py tests/golden/test_phase3_worker.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import (  # noqa: E402
    ModelResponse, Risk, Task, TaskStatus, ToolCall, new_id,
)
from core.events import EventType  # noqa: E402
from core.state import RunState, TaskState  # noqa: E402
from control.evaluator import Evaluator  # noqa: E402
from control.policy import PolicyEngine, PolicyRules  # noqa: E402
from control.tools import ToolRegistry, ToolSpec  # noqa: E402
from execution.durable import DurableEventBus  # noqa: E402
from execution.fake import FakeExecutor  # noqa: E402
from execution.orchestrator import Orchestrator  # noqa: E402
from execution.queue import TaskQueue  # noqa: E402
from execution.worker import Worker  # noqa: E402
from knowledge.inmemory import ComposedRetriever  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def build(tmp):
    bus = DurableEventBus(os.path.join(tmp, "events.db"))
    queue = TaskQueue(os.path.join(tmp, "queue.db"), bus=bus)

    retriever = ComposedRetriever()
    retriever.ingest("filesystem", "docs/middleware.md", "v1",
                     "the authentication middleware has a bug in token verification")
    tools = ToolRegistry()
    tools.register(ToolSpec("github.read_file", "read a file", Risk.READ))
    script = [
        ModelResponse(model="fake", content="", success=True, tool_calls=[
            ToolCall(tool_name="github.read_file", arguments={"path": "middleware.py"})]),
        ModelResponse(model="fake", content="fixed", success=True),
    ]
    orchestrator = Orchestrator(
        retriever=retriever, executor=FakeExecutor(model_script=script),
        policy=PolicyEngine(PolicyRules()), tools=tools, evaluator=Evaluator(), bus=bus)
    return bus, queue, Worker(worker_id="w1", queue=queue, orchestrator=orchestrator)


def main():
    print("Phase 3.6 golden task: durable task execution (queue + worker)")
    tmp = tempfile.mkdtemp()
    bus, queue, worker = build(tmp)
    task = Task(task_id=new_id("task"), title="fix authentication bug")

    queue.enqueue(task)
    outcome = worker.run_one()

    # the full path completed
    check(outcome is not None and outcome.answer == "fixed", "Task -> Queue -> Worker -> Answer")
    check(queue.get(task.task_id).status == TaskStatus.DONE, "completion is durable (queue status DONE)")

    # the durable event log is the source of truth for the task lifecycle
    events = bus.load_events(task_id=task.task_id)

    # ownership is a durable, observable event
    claimed = [e for e in events if e.event_type == EventType.TASK_CLAIMED]
    check(len(claimed) == 1 and claimed[0].payload.get("worker_id") == "w1",
          "task.claimed records the owning worker (ownership observable)")

    # completion is a durable event
    completed = [e for e in events if e.event_type == EventType.TASK_COMPLETED]
    check(len(completed) == 1 and completed[0].payload.get("answer") == "fixed",
          "task.completed records the answer (completion observable)")

    # durability across a fresh connection: reload + reconstruct
    reloaded = DurableEventBus(os.path.join(tmp, "events.db")).load_events(task_id=task.task_id)
    check(len(reloaded) == len(events), "events survive a fresh connection (durable log)")

    task_state = TaskState.reconstruct(task, reloaded)
    check(task_state.status == TaskStatus.DONE and task_state.worker_id == "w1",
          "task state reconstructs to DONE + owner from the durable event log")
    check(RunState.reconstruct(outcome.run, reloaded) == outcome.live_state,
          "run state reconstructs from the durable event log (state remains reconstructible)")

    # a task can't be silently lost: claim without completing stays CLAIMED (recoverable)
    task2 = Task(task_id=new_id("task"), title="second task")
    queue.enqueue(task2)
    queue.claim("w1")  # claimed, but never completed (a worker "died" before running)
    check(queue.get(task2.task_id).status == TaskStatus.CLAIMED,
          "an abandoned task stays CLAIMED (durable), never silently lost")
    t2 = TaskState.reconstruct(task2, bus.load_events(task_id=task2.task_id))
    check(t2.status == TaskStatus.CLAIMED and t2.worker_id == "w1",
          "the abandoned task's ownership is recoverable from events")

    print("\nPASS: Phase 3.6 durable task execution holds.")


if __name__ == "__main__":
    main()
