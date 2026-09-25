"""Phase 3.7 golden task — crash recovery under real process death.

The deterministic failure-injection test:

    enqueue -> worker w1 claims -> orchestrator runs -> KILL PROCESS
    -> fresh process/connection -> reconstruct from durable evidence
    -> identify abandoned CLAIMED task -> recovery requeues it
    -> worker w2 claims -> orchestrator re-runs (at-least-once) -> task.completed

Two failure points:
- A: after task.claimed, before the tool (no side effect yet).
- B: after the tool's side effect, before the completion event (at-least-once:
  the tool executes again on re-run — expected and documented, not an anomaly).

Run:  py tests/golden/test_phase3_recovery.py
"""
import os
import subprocess
import sys
import tempfile
import time
from datetime import timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from fixtures import KillableExecutor  # noqa: E402

from core.contracts import Risk, Task, TaskStatus, new_id, utcnow  # noqa: E402
from core.state import RunState, TaskState  # noqa: E402
from control.evaluator import Evaluator  # noqa: E402
from control.policy import PolicyEngine, PolicyRules  # noqa: E402
from control.tools import ToolRegistry, ToolSpec  # noqa: E402
from execution.durable import DurableEventBus  # noqa: E402
from execution.orchestrator import Orchestrator  # noqa: E402
from execution.queue import TaskQueue  # noqa: E402
from execution.recovery import RecoveryManager  # noqa: E402
from execution.worker import Worker  # noqa: E402
from knowledge.inmemory import ComposedRetriever  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def _wait_for_file(path, timeout=25):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return True
        time.sleep(0.05)
    return False


def _spawn_worker(tmp, worker_id, kill_phase, sleep_seconds):
    env = {
        **os.environ,
        "PYTHONPATH": ROOT + os.pathsep + os.path.join(ROOT, "tests"),
        "EVENTS_PATH": os.path.join(tmp, "events.db"),
        "QUEUE_PATH": os.path.join(tmp, "queue.db"),
        "SIGNAL_PATH": os.path.join(tmp, "signal.txt"),
        "SIDE_EFFECT_PATH": os.path.join(tmp, "side_effect.txt"),
        "WORKER_ID": worker_id,
        "KILL_PHASE": kill_phase,
        "SLEEP_SECONDS": str(sleep_seconds),
    }
    code = "from fixtures import run_worker_from_env; run_worker_from_env()"
    return subprocess.Popen([sys.executable, "-c", code], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _lines(path):
    if not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8") as f:
        return len(f.read().splitlines())


def _run_w2(tmp, worker_id):
    """A fresh worker (fresh connections) that completes the task, in-process."""
    bus = DurableEventBus(os.path.join(tmp, "events.db"))
    queue = TaskQueue(os.path.join(tmp, "queue.db"), bus=bus)
    retriever = ComposedRetriever()
    retriever.ingest("filesystem", "docs/x.md", "v1", "context for the task")
    tools = ToolRegistry()
    tools.register(ToolSpec("github.read_file", "read a file", Risk.READ))
    executor = KillableExecutor(os.path.join(tmp, "signal.txt"),
                                os.path.join(tmp, "side_effect.txt"),
                                sleep_seconds=0, sleep_phase="tool")
    orchestrator = Orchestrator(retriever=retriever, executor=executor,
                                policy=PolicyEngine(PolicyRules()), tools=tools,
                                evaluator=Evaluator(), bus=bus)
    worker = Worker(worker_id=worker_id, queue=queue, orchestrator=orchestrator)
    outcome = worker.run_one()
    return outcome, bus, queue


def run_case(kill_phase, expected_side_effects, label):
    tmp = tempfile.mkdtemp()
    events_path = os.path.join(tmp, "events.db")
    queue_path = os.path.join(tmp, "queue.db")
    signal_path = os.path.join(tmp, "signal.txt")
    side_effect_path = os.path.join(tmp, "side_effect.txt")

    # 1. enqueue (parent)
    bus = DurableEventBus(events_path)
    queue = TaskQueue(queue_path, bus=bus)
    task = Task(task_id=new_id("task"), title="fix the thing")
    queue.enqueue(task)
    bus.close()
    queue.close()

    # 2. worker w1 claims + runs in a subprocess, then dies hard
    proc = _spawn_worker(tmp, "w1", kill_phase, sleep_seconds=60)
    check(_wait_for_file(signal_path), f"{label}: worker w1 reached its kill point")
    proc.kill()
    proc.wait()

    # 3. fresh process / fresh connections: reconstruct from durable evidence
    bus2 = DurableEventBus(events_path)
    queue2 = TaskQueue(queue_path, bus=bus2)
    ts = TaskState.reconstruct(task, bus2.load_events(task_id=task.task_id))
    check(ts.status == TaskStatus.CLAIMED, f"{label}: durable evidence -> task is CLAIMED (abandoned)")
    check(ts.worker_id == "w1", f"{label}: durable evidence -> it was owned by w1")

    # 4. recovery (queue infrastructure, NOT the orchestrator) requeues it
    recovered = RecoveryManager(queue2, lease_seconds=1).recover(
        now=utcnow() + timedelta(seconds=10))
    check(recovered == [task.task_id], f"{label}: recovery requeues the abandoned task")
    check(queue2.get(task.task_id).status == TaskStatus.QUEUED,
          f"{label}: task is QUEUED again after recovery")
    bus2.close()
    queue2.close()

    # 5. worker w2 claims + completes (a fresh worker, unaware it is a recovery)
    outcome, bus3, queue3 = _run_w2(tmp, "w2")
    check(outcome is not None and queue3.get(task.task_id).status == TaskStatus.DONE,
          f"{label}: task.completed after recovery")
    check(outcome.answer == "the answer", f"{label}: the answer survives recovery")

    # 6. at-least-once is explicit: the tool's side effect count
    side_after = _lines(side_effect_path)
    check(side_after == expected_side_effects,
          f"{label}: tool side effects = {side_after} (expected {expected_side_effects})")

    # 7. reconstruct TaskState + RunState from the durable log
    final_events = bus3.load_events(task_id=task.task_id)
    check(TaskState.reconstruct(task, final_events)
          == TaskState(task=task, status=TaskStatus.DONE, worker_id="w2"),
          f"{label}: TaskState reconstructs the full lifecycle -> DONE + w2")
    w2_events = [e for e in final_events if e.run_id == outcome.run.run_id]
    check(RunState.reconstruct(outcome.run, w2_events) == outcome.live_state,
          f"{label}: RunState reconstructed from the durable log == live state")
    bus3.close()
    queue3.close()


def main():
    print("Phase 3.7 golden task: crash recovery under real process death")
    run_case("model", 1, "A (killed before the tool)")
    run_case("tool", 2, "B (killed after the tool side effect)")
    print("\nPASS: Phase 3.7 crash recovery holds (recoverable agent execution platform).")


if __name__ == "__main__":
    main()
