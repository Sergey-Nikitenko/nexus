"""Phase 0 acceptance — the golden task that proves the foundation.

A FAKE agent executes a multi-step task against mocks. The contracts, events,
trace, and state here are the real ones — only the agent's "brain" is fake.
The Phase 3 orchestrator drops into this same harness.

Run:  py tests/golden/test_phase0_foundation.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import (  # noqa: E402
    Decision, Evaluation, Event, RetrievedChunk, RetrievalResult, Run, Step,
    StepStatus, Task, TaskStatus, ToolCall, Trace, new_id, utcnow,
)
from core.events import EventBus, EventType  # noqa: E402
from core.state import RunState  # noqa: E402


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


def fake_agent(task: Task):
    """Run a 4-step task against mocks, emitting events and building a trace."""
    bus = EventBus()
    run = Run(run_id=new_id("run"), task_id=task.task_id)
    trace = Trace(run_id=run.run_id)

    _emit(bus, EventType.RUN_STARTED, run, "orchestrator", "success")

    # plan
    s = Step(step_id=new_id("step"), name="plan")
    run.steps.append(s)
    _emit(bus, EventType.STEP_STARTED, run, "planner", "running", {"step_id": s.step_id})
    _emit(bus, EventType.STEP_COMPLETED, run, "planner", "success", {"step_id": s.step_id})
    trace.nodes.append({"type": "plan", "step_id": s.step_id})

    # retrieve
    ret = RetrievalResult(query="auth bug", chunks=[
        RetrievedChunk(text="...", source="middleware.py", document="middleware.py"),
    ])
    _emit(bus, EventType.RETRIEVAL_COMPLETED, run, "retrieval", "success", {"chunks": len(ret.chunks)})
    trace.nodes.append({"type": "retrieve", "chunks": len(ret.chunks)})

    # act — routing decision + tool call
    decision = Decision(model="ollama/qwen", provider="local", reasons=["private repo"])
    _emit(bus, EventType.MODEL_SELECTED, run, "router", "success", {"model": decision.model})
    tool = ToolCall(tool_name="github.read_file", arguments={"path": "middleware.py"})
    _emit(bus, EventType.TOOL_REQUESTED, run, "mcp", "running", {"tool": tool.tool_name})
    _emit(bus, EventType.TOOL_COMPLETED, run, "mcp", "success", {"tool": tool.tool_name})
    trace.nodes.append({"type": "tool", "tool": tool.tool_name})

    # verify
    eval_ = Evaluation(checks={"tests": "pass", "groundedness": 0.94})
    _emit(bus, EventType.EVALUATION_COMPLETED, run, "evaluator", "success", eval_.checks)
    trace.nodes.append({"type": "verify", "checks": eval_.checks})

    _emit(bus, EventType.RUN_COMPLETED, run, "orchestrator", "success")
    for st in run.steps:
        st.status = StepStatus.PASS
    return run, trace, bus.history


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def main():
    print("Phase 0 acceptance: fake agent -> multi-step task -> trace + recoverable state")
    task = Task(task_id=new_id("task"), title="fix authentication bug")
    run, trace, events = fake_agent(task)

    check(len(trace.nodes) >= 4, "trace has a node per step")
    for ev in events:
        check(bool(ev.event_id and ev.run_id == run.run_id and ev.task_id == task.task_id),
              f"event '{ev.event_type}' well-formed")
    check(len(events) >= 8, "events emitted for the whole run")

    # recoverable state: rebuild purely from the event log (simulates a crash)
    state = RunState.reconstruct(run, events)
    check(state.task_status == TaskStatus.DONE, "task status recovered as done")
    check(len(state.completed_steps) >= 1, "step statuses recovered from events")

    print("\nPASS: Phase 0 foundation holds.")


if __name__ == "__main__":
    main()
