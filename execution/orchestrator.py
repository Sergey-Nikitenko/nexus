"""The orchestrator — Nexus's central nervous system.

The orchestrator OWNS sequencing and correlation; the capabilities OWN
execution. It may call `retriever.search(...)`, `executor.run_model(...)`,
`executor.execute_tool(...)`, `policy.decide_tool(...)`, `state` and `emit(...)`
— but it NEVER does capability work itself: no subprocess, no file I/O, no
network, no vector store, no model SDK, no MCP client. That boundary is enforced
by tests/conformance/test_orchestrator_purity.py.

The deterministic loop (3.4 — no replanning/retries/verification yet):

    Task -> plan -> retrieve -> model -> [tool (policy-gated)] -> model -> verify -> answer

A model may SUGGEST a tool call (ModelResponse.tool_calls). The orchestrator
DECIDES whether and when to invoke it, and the Phase 1 policy stays
authoritative: DENY / APPROVAL_REQUIRED / ALLOW, never "the model said so".
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.contracts import (
    Event, ModelRequest, PolicyVerdict, Run, Step, StepStatus, Task, TaskStatus, Trace,
    new_id, utcnow,
)
from core.events import EventBus, EventType
from core.state import RunState
from execution.instrumented import InstrumentedExecutor


@dataclass
class Outcome:
    """What one run produced: the answer, the run, its trace, the LIVE state
    (updated during execution), and the event log (the durable record)."""
    answer: str
    run: Run
    trace: Trace
    live_state: RunState
    events: list[Event] = field(default_factory=list)


class Orchestrator:
    """Composes injected capabilities behind one deterministic loop."""

    def __init__(self, *, retriever, executor, policy, tools, bus=None) -> None:
        self.retriever = retriever      # .search(query, filters, k) -> RetrievalResult
        self.executor = executor        # raw capability (FakeExecutor, subprocess, ...)
        self.policy = policy            # PolicyEngine (tool gating)
        self.tools = tools              # control ToolRegistry (name -> ToolSpec, for risk)
        self.bus = bus or EventBus()

    def run(self, task: Task) -> Outcome:
        run = Run(run_id=new_id("run"), task_id=task.task_id)
        state = RunState(run=run)
        trace = Trace(run_id=run.run_id)
        # the executor is instrumented with THIS run's identity, so tool/model
        # events correlate to the same run/step (AD-009).
        instr = InstrumentedExecutor(
            self.executor, self.bus, run_id=run.run_id, task_id=task.task_id)

        def emit(event_type: str, status: str, payload: dict) -> None:
            self.bus.publish(Event(
                event_id=new_id("evt"), event_type=event_type, timestamp=utcnow(),
                run_id=run.run_id, task_id=task.task_id, component="orchestrator",
                status=status, payload=payload,
            ))

        def step(name: str, work):
            s = Step(step_id=new_id("step"), name=name)
            run.steps.append(s)
            state.step_status[s.step_id] = StepStatus.RUNNING
            emit(EventType.STEP_STARTED, "running", {"step_id": s.step_id, "name": name})
            result = work()
            s.status = StepStatus.PASS
            state.step_status[s.step_id] = StepStatus.PASS
            emit(EventType.STEP_COMPLETED, "success", {"step_id": s.step_id, "name": name})
            return result

        def build_request(extra_messages=None) -> ModelRequest:
            messages = [
                {"role": "system", "content": "\n".join(c.text for c in retrieved.chunks)},
                {"role": "user", "content": task.title},
            ]
            messages.extend(extra_messages or [])
            return ModelRequest(messages=messages)

        def run_tools(tool_calls) -> list:
            results = []
            for call in tool_calls:
                spec = self.tools.get(call.tool_name)  # unknown tool -> KeyError -> deny
                verdict = self.policy.decide_tool(spec)
                if verdict == PolicyVerdict.ALLOW:
                    result = instr.execute_tool(call)
                    trace.nodes.append({"type": "tool", "tool": call.tool_name,
                                        "verdict": "allow", "success": result.success})
                    results.append(result)
                elif verdict == PolicyVerdict.APPROVAL_REQUIRED:
                    emit(EventType.APPROVAL_REQUIRED, "pending", {"tool": call.tool_name})
                    trace.nodes.append({"type": "tool", "tool": call.tool_name,
                                        "verdict": "approval_required"})
                else:  # DENY
                    trace.nodes.append({"type": "tool", "tool": call.tool_name,
                                        "verdict": "deny"})
            return results

        emit(EventType.RUN_STARTED, "success", {})

        plan = ["retrieve", "model", "tool", "verify"]
        step("plan", lambda: trace.nodes.append({"type": "plan", "steps": list(plan)}))

        def do_retrieve():
            emit(EventType.RETRIEVAL_REQUESTED, "running", {"query": task.title})
            result = self.retriever.search(task.title, k=5)
            emit(EventType.RETRIEVAL_COMPLETED, "success", {"chunks": len(result.chunks)})
            trace.nodes.append({"type": "retrieve", "chunks": len(result.chunks)})
            return result

        retrieved = step("retrieve", do_retrieve)

        def do_model():
            resp = instr.run_model(build_request())
            trace.nodes.append({"type": "model", "tool_calls": len(resp.tool_calls)})
            return resp

        response = step("model", do_model)

        if response.tool_calls:
            def do_tools():
                results = run_tools(response.tool_calls)
                return results

            tool_results = step("tool", do_tools)

            def do_final_model():
                resp = instr.run_model(build_request([
                    {"role": "tool", "content": f"{len(tool_results)} tool result(s)"},
                ]))
                trace.nodes.append({"type": "model", "final": True})
                return resp

            response = step("model", do_final_model)

        step("verify", lambda: trace.nodes.append({"type": "verify", "passed": True}))

        answer = response.content
        trace.nodes.append({"type": "answer", "answer": answer})
        state.task_status = TaskStatus.DONE
        emit(EventType.RUN_COMPLETED, "success", {"answer": answer})
        return Outcome(answer=answer, run=run, trace=trace, live_state=state,
                       events=list(self.bus.history))
