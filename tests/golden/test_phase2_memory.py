"""Phase 2 golden task — memory: run -> episode -> retrieve.

Memory is the SEQUENCE plane (what did we attempt, how did it end), distinct
from knowledge (the CONTENT plane). A finished run is projected — deterministically,
no LLM — into an Episode and stored for retrieval.

Run:  py tests/golden/test_phase2_memory.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import (  # noqa: E402
    Event, Evaluation, Run, Step, StepStatus, Task, new_id, utcnow,
)
from core.events import EventBus, EventType  # noqa: E402
from memory.inmemory import InMemoryEpisodeStore, extract_episode  # noqa: E402


def _emit(bus, event_type, run, component, status, payload=None):
    ev = Event(
        event_id=new_id("evt"),
        event_type=event_type,
        timestamp=utcnow(),
        run_id=run.run_id,
        task_id=run.task_id,
        component=component,
        status=status,
        payload=payload or {},
    )
    bus.publish(ev)
    return ev


def fake_run(task, *, success=True):
    """A finished run with a tool call touching a real file, plus evaluation."""
    bus = EventBus()
    run = Run(run_id=new_id("run"), task_id=task.task_id)
    for name in ("plan", "act", "verify"):
        s = Step(step_id=new_id("step"), name=name, status=StepStatus.PASS)
        run.steps.append(s)
        _emit(bus, EventType.STEP_STARTED, run, "orchestrator", "running", {"step_id": s.step_id})
        _emit(bus, EventType.STEP_COMPLETED, run, "orchestrator", "success", {"step_id": s.step_id})

    _emit(bus, EventType.TOOL_REQUESTED, run, "mcp", "running",
          {"tool": "github.read_file", "path": "middleware.py"})
    _emit(bus, EventType.TOOL_COMPLETED, run, "mcp", "success",
          {"tool": "github.read_file"})

    eval_ = Evaluation(checks={"tests": "pass" if success else "fail"})
    _emit(bus, EventType.EVALUATION_COMPLETED, run, "evaluator", "success", eval_.checks)
    _emit(bus, EventType.RUN_COMPLETED if success else EventType.RUN_FAILED,
          run, "orchestrator", "success" if success else "failed")
    return task, run, bus.history, eval_


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def main():
    print("Phase 2 golden task: run -> episode -> retrieve (memory, not a vector DB)")
    task = Task(task_id=new_id("task"), title="fix authentication token refresh")
    task, run, events, eval_ = fake_run(task)

    ep = extract_episode(task, run, events, eval_)
    check(ep.task_id == task.task_id, "episode links back to its task")
    check(ep.summary == task.title, "summary is the task title (no LLM summarization)")
    check(ep.outcome == "success", "outcome is derived from the run.completed event")
    check("github.read_file" in ep.relevant_entities, "tool name is a relevant entity")
    check("middleware.py" in ep.relevant_entities, "resource path is a relevant entity")
    check(ep.provenance["run_id"] == run.run_id, "provenance keeps the run_id (audit trail)")
    check(ep.provenance["evaluation"] == eval_.checks, "evaluation evidence survives into provenance")

    # determinism: the same run must project to the same episode content
    ep2 = extract_episode(task, run, events, eval_)
    same = (ep2.summary == ep.summary and ep2.outcome == ep.outcome
            and ep2.relevant_entities == ep.relevant_entities
            and ep2.provenance == ep.provenance)
    check(same, "extraction is deterministic (same run -> same episode content)")

    # a failed run must project to a failed episode (outcome from events, not vibes)
    task2 = Task(task_id=new_id("task"), title="deploy database migration")
    t2, r2, e2, ev2 = fake_run(task2, success=False)
    ep_failed = extract_episode(t2, r2, e2, ev2)
    check(ep_failed.outcome == "failed", "a failed run yields a failed episode")

    # store + retrieve
    store = InMemoryEpisodeStore()
    store.add(ep)
    store.add(ep_failed)
    store.add(ep)  # idempotent: same episode_id -> no duplicate

    hits = store.search("token refresh", k=5)
    check(any(h.episode_id == ep.episode_id for h in hits),
          "retrieval finds the auth episode by its summary")
    check(store.get(ep.episode_id) is ep, "store.get returns the episode by id")
    check(len(store.search("token", k=5)) == 1, "re-adding an episode does not duplicate it")

    successes = store.search("token", filters={"outcome": "success"}, k=5)
    check(all(h.outcome == "success" for h in successes), "filters are honored (outcome)")

    print("\nPASS: Phase 2 memory holds.")


if __name__ == "__main__":
    main()
