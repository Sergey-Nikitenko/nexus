"""The orchestrator — Nexus's central nervous system.

The orchestrator OWNS sequencing and correlation; the capabilities OWN
execution. It may call `retriever.search(...)`, `executor.run_model(...)`,
`executor.execute_tool(...)`, `policy.decide_tool(...)`, `evaluator.evaluate(...)`,
`state` and `emit(...)` — but it NEVER does capability work itself: no subprocess,
no file I/O, no network, no vector store, no model SDK, no MCP client. That
boundary is enforced by tests/conformance/test_orchestrator_purity.py.

The loop (3.5 — verification + bounded replanning):

    plan -> retrieve -> act -> verify
                            ├─ PASS ──────────────► answer
                            └─ FAIL (replan_required)
                                    └─► run.replanned ─► act again (bounded)

A model may SUGGEST a tool call (ModelResponse.tool_calls). The orchestrator
DECIDES whether and when to invoke it, and the Phase 1 policy stays
authoritative on every attempt — replanning changes the plan, never the authority.
The evaluator observes and judges; the orchestrator interprets the verdict.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from core.contracts import (
    AgentIdentity, ApprovalRequest, Event, ModelIdentity, ModelRequest,
    PolicyVerdict, Run, RunManifest, Step, StepStatus, Task, TaskStatus, Trace,
    UserIdentity, new_id, utcnow,
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
    waiting: bool = False
    manifest: RunManifest | None = None


class Orchestrator:
    """Composes injected capabilities behind one deterministic, bounded loop."""

    def __init__(self, *, retriever, executor, policy, tools, evaluator,
                 bus=None, approvals=None, run_records=None, max_replans: int = 2) -> None:
        self.retriever = retriever      # .search(query, filters, k) -> RetrievalResult
        self.executor = executor        # raw capability (FakeExecutor, subprocess, ...)
        self.policy = policy            # PolicyEngine (tool gating)
        self.tools = tools              # control ToolRegistry (name -> ToolSpec, for risk)
        self.evaluator = evaluator      # .evaluate(...) -> Evaluation (judges, never executes)
        self.bus = bus or EventBus()
        self.approvals = approvals      # ApprovalStore (optional; None = approvals not wired)
        self.run_records = run_records  # RunRecordStore (optional; None = not persisted)
        self.max_replans = max_replans

    def _model_identity(self) -> ModelIdentity:
        """Which logical model configuration this executor runs — a ModelIdentity
        contract, never the provider config (AD-035)."""
        mi = getattr(self.executor, "model_identity", None)
        if isinstance(mi, ModelIdentity):
            return mi
        if isinstance(mi, str) and mi:
            return ModelIdentity(model_id=mi)
        return ModelIdentity()

    def run(self, task: Task) -> Outcome:
        run = Run(run_id=new_id("run"), task_id=task.task_id)
        state = RunState(run=run)
        trace = Trace(run_id=run.run_id)
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
            try:
                result = work()
            except Exception:
                # a NORMAL exception reaches a terminal step state (step.failed);
                # a hard process death leaves step.started with NO terminal event —
                # that distinction is how an interrupted operation is detected.
                s.status = StepStatus.FAIL
                state.step_status[s.step_id] = StepStatus.FAIL
                emit(EventType.STEP_FAILED, "failed", {"step_id": s.step_id, "name": name})
                raise
            s.status = StepStatus.PASS
            state.step_status[s.step_id] = StepStatus.PASS
            emit(EventType.STEP_COMPLETED, "success", {"step_id": s.step_id, "name": name})
            return result

        def build_request(retrieved, extra_messages=None) -> ModelRequest:
            messages = [
                {"role": "system", "content": "\n".join(c.text for c in retrieved.chunks)},
                {"role": "user", "content": task.title},
            ]
            messages.extend(extra_messages or [])
            return ModelRequest(messages=messages)

        def run_tools(tool_calls):
            results = []
            waiting = False
            for call in tool_calls:
                # Nexus owns the execution identity (AD-012's twin for calls):
                # re-mint per ATTEMPT, so a replayed/recovered invocation can never
                # be conflated with the first one. The provider's (or model's) own
                # id is deliberately ignored — at-least-once means two physical side
                # effects must show as two distinct calls in the event log.
                call.call_id = new_id("call")
                spec = self.tools.get(call.tool_name)
                verdict = self.policy.decide_tool(spec)
                # the decision is observable (with its OWN reason — the UI renders
                # this, it never recomputes policy semantics)
                reason = {
                    PolicyVerdict.ALLOW: f"Policy allows a {spec.risk.value} operation",
                    PolicyVerdict.DENY: f"Policy rejects a {spec.risk.value} operation",
                    PolicyVerdict.APPROVAL_REQUIRED:
                        f"Policy requires approval for a {spec.risk.value} operation",
                }[verdict]
                emit(EventType.POLICY_DECISION, "success", {
                    "tool": call.tool_name,
                    "verdict": verdict.value,
                    "risk": spec.risk.value,
                    "executed": verdict == PolicyVerdict.ALLOW,
                    "reason": reason,
                    "call_id": call.call_id,
                })
                if verdict == PolicyVerdict.ALLOW:
                    result = instr.execute_tool(call)
                    trace.nodes.append({"type": "tool", "tool": call.tool_name,
                                        "verdict": "allow", "success": result.success})
                    results.append(result)
                elif verdict == PolicyVerdict.APPROVAL_REQUIRED:
                    if self.approvals is not None:
                        # policy is re-checked here: an approved action is still
                        # gated by the CURRENT policy, never magically authorized.
                        granted = self.approvals.find_approved(
                            task.task_id, call.tool_name, spec.risk)
                        if granted is not None:
                            # ATOMIC single-use consume: the database decides the
                            # winner; a loser (None) must not execute the tool.
                            consumed = self.approvals.consume_approved(granted.approval_id)
                            if consumed is not None:
                                result = instr.execute_tool(call)
                                trace.nodes.append({"type": "tool", "tool": call.tool_name,
                                                    "verdict": "approved", "success": result.success})
                                results.append(result)
                            else:
                                trace.nodes.append({"type": "tool", "tool": call.tool_name,
                                                    "verdict": "consumed_elsewhere"})
                        else:
                            approval = ApprovalRequest(
                                approval_id=new_id("appr"), task_id=task.task_id,
                                run_id=run.run_id, tool_name=call.tool_name, risk=spec.risk)
                            self.approvals.create(approval)  # emits approval.required
                            trace.nodes.append({"type": "tool", "tool": call.tool_name,
                                                "verdict": "approval_required",
                                                "approval_id": approval.approval_id})
                            waiting = True
                            break  # pause: the task waits for human approval
                    else:
                        emit(EventType.APPROVAL_REQUIRED, "pending", {"tool": call.tool_name})
                        trace.nodes.append({"type": "tool", "tool": call.tool_name,
                                            "verdict": "approval_required"})
                else:  # DENY
                    trace.nodes.append({"type": "tool", "tool": call.tool_name,
                                        "verdict": "deny"})
            return results, waiting

        def verify(tool_results, attempt):
            evaluation = self.evaluator.evaluate(tool_results=tool_results)
            payload = {"passed": evaluation.passed, "reason": evaluation.reason,
                       "replan_required": evaluation.replan_required}
            emit(EventType.EVALUATION_COMPLETED, "success", {**payload, "attempt": attempt})
            state.evaluations.append(payload)
            trace.nodes.append({"type": "verify", "passed": evaluation.passed,
                                "reason": evaluation.reason, "attempt": attempt})
            return evaluation

        def do_retrieve():
            emit(EventType.RETRIEVAL_REQUESTED, "running", {"query": task.title})
            result = self.retriever.search(task.title, k=5)
            emit(EventType.RETRIEVAL_COMPLETED, "success", {"chunks": len(result.chunks)})
            trace.nodes.append({"type": "retrieve", "chunks": len(result.chunks)})
            return result

        def do_model(retrieved, extra_messages=None, final=False):
            resp = instr.run_model(build_request(retrieved, extra_messages))
            trace.nodes.append({"type": "model", "tool_calls": len(resp.tool_calls),
                                "final": final})
            return resp

        # The manifest captures the INPUTS that define this run BEFORE the first
        # capability decision (AD-029): the events below record "what happened";
        # the manifest records "what configuration defined it".
        def _snapshot(component, fallback="unknown"):
            fn = getattr(component, "snapshot", None)
            return fn() if callable(fn) else fallback

        manifest = RunManifest(
            run_id=run.run_id,
            task_id=task.task_id,
            task_title=task.title,
            knowledge=_snapshot(self.retriever),
            policy=getattr(getattr(self.policy, "rules", None), "version", "policy@1"),
            model=self._model_identity(),
            tools=_snapshot(self.tools),
            router="",
            max_replans=self.max_replans,
            user=UserIdentity(user_id=task.user),
            agent=AgentIdentity(agent_id=task.agent, role=task.agent),
            parent_run_id=task.parent_run_id,
        )
        if self.run_records is not None:
            self.run_records.record_manifest(run.run_id, task.task_id, manifest)
        emit(EventType.RUN_MANIFEST, "success", asdict(manifest))
        emit(EventType.RUN_STARTED, "success", {})
        step("plan", lambda: trace.nodes.append({
            "type": "plan", "steps": ["retrieve", "act", "verify", "replan"],
            "max_replans": self.max_replans,
        }))

        answer = ""
        attempt = 0
        while True:
            attempt += 1
            retrieved = step("retrieve", do_retrieve)
            response = step("model", lambda: do_model(retrieved))
            tool_results = []
            if response.tool_calls:
                tool_results, waiting = step("tool", lambda: run_tools(response.tool_calls))
                if waiting:
                    # the task pauses durably for human approval
                    trace.nodes.append({"type": "waiting"})
                    return Outcome(answer="", run=run, trace=trace, live_state=state,
                                   events=list(self.bus.history), waiting=True,
                                   manifest=manifest)
                response = step("model", lambda: do_model(
                    retrieved,
                    [{"role": "tool", "content": f"{len(tool_results)} tool result(s)"}],
                    final=True))

            evaluation = step("verify", lambda: verify(tool_results, attempt))

            if evaluation.passed:
                answer = response.content
                break

            if evaluation.replan_required and state.replan_count < self.max_replans:
                state.replan_count += 1
                emit(EventType.RUN_REPLANNED, "running", {"attempt": attempt + 1})
                trace.nodes.append({"type": "replan", "attempt": attempt + 1})
                continue

            # terminal failure: replan not requested, or budget exhausted
            state.task_status = TaskStatus.FAILED
            emit(EventType.RUN_FAILED, "failed", {"reason": evaluation.reason})
            trace.nodes.append({"type": "answer", "answer": ""})
            return Outcome(answer="", run=run, trace=trace, live_state=state,
                           events=list(self.bus.history), manifest=manifest)

        trace.nodes.append({"type": "answer", "answer": answer})
        state.task_status = TaskStatus.DONE
        emit(EventType.RUN_COMPLETED, "success", {"answer": answer})
        return Outcome(answer=answer, run=run, trace=trace, live_state=state,
                       events=list(self.bus.history), manifest=manifest)
