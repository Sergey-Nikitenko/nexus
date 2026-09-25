"""Agent — a capability composition over a NexusRuntime, not a new substrate.

An agent is a STABLE LOGICAL IDENTITY (agent_id) + a runtime. It does not have its
own orchestration, event, or state model: it submits tasks through the SAME
runtime (queue -> worker -> orchestrator -> events -> run records) every other
caller uses. Delegation is explicit — `submit(title, parent_run_id)` records the
delegating run and this agent's role on the task, which flows into the child run's
RunManifest (AD-034).

Agent identity is NOT worker_id / task_id / run_id: it answers "which ROLE did
this work?", not "which worker ran it?", "which task?", or "which run?". A
supervisor that delegates to research/coding/review agents is just composing
these roles — no parallel execution model is introduced.
"""
from __future__ import annotations


class Agent:
    def __init__(self, *, agent_id: str, runtime, role: str = "", user: str = "") -> None:
        self.agent_id = agent_id
        self.role = role or agent_id
        self.user = user
        self.runtime = runtime  # duck-typed: .ask(title, user, agent, parent_run_id), .run_one()

    def submit(self, title: str, parent_run_id: str = "") -> str:
        """Submit a task FOR this agent, optionally recording the delegating run.
        Returns the durable task_id."""
        return self.runtime.ask(title, user=self.user, agent=self.agent_id,
                                parent_run_id=parent_run_id)

    def run_one(self):
        """Drain one queued task through this agent's runtime."""
        return self.runtime.run_one()
