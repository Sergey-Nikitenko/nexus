"""Trace projector — derives a `core.Trace` from the durable event stream.

A read-side projection: the events remain the source of truth, and the trace is
derived, deterministic, and never mutates the events. Pure — it consumes only
core.contracts and core.events, so it has no execution/provider/HTTP knowledge.

    Durable events -> TraceProjector -> core.Trace -> HTTP / WebSocket / CLI / dashboard
"""
from __future__ import annotations

from core.contracts import Trace
from core.events import EventType


class TraceProjector:
    """Projects an event sequence into the existing core.Trace contract."""

    def project(self, events) -> Trace:
        events = list(events)
        run_id = next((e.run_id for e in events if e.run_id), "")
        # pair tool.requested with tool.completed (FIFO per tool) so an
        # interrupted execution is explicit, never silently a success.
        completed_by_tool: dict[str, list] = {}
        for e in events:
            if e.event_type == EventType.TOOL_COMPLETED:
                completed_by_tool.setdefault(e.payload.get("tool"), []).append(e)

        nodes: list[dict] = []
        for e in events:
            node = self._project(e, completed_by_tool)
            if node is not None:
                nodes.append(node)
        return Trace(run_id=run_id, nodes=nodes)

    def _base(self, e) -> dict:
        return {
            "event": e.event_type,
            "event_id": e.event_id,
            "parent_event_id": e.parent_event_id,
            "run_id": e.run_id,
            "task_id": e.task_id,
            "timestamp": e.timestamp.isoformat(),
        }

    def _project(self, e, completed_by_tool) -> dict | None:
        p = e.payload or {}
        t = e.event_type
        base = self._base(e)

        if t == EventType.RUN_STARTED:
            return {**base, "type": "run", "status": "started"}
        if t == EventType.RUN_COMPLETED:
            return {**base, "type": "run", "status": "completed", "answer": p.get("answer")}
        if t == EventType.RUN_FAILED:
            return {**base, "type": "run", "status": "failed", "reason": p.get("reason")}
        if t == EventType.RUN_REPLANNED:
            return {**base, "type": "replan", "attempt": p.get("attempt")}

        if t == EventType.STEP_STARTED and p.get("name") == "plan":
            return {**base, "type": "plan"}

        if t == EventType.RETRIEVAL_COMPLETED:
            return {**base, "type": "retrieval", "chunks": p.get("chunks")}

        if t == EventType.MODEL_REQUESTED:
            return {**base, "type": "model_request", "request_id": p.get("request_id")}
        if t == EventType.MODEL_COMPLETED:
            return {**base, "type": "model_response", "request_id": p.get("request_id"),
                    "model": p.get("model"), "success": p.get("success")}

        if t == EventType.POLICY_DECISION:
            return {**base, "type": "decision", "tool": p.get("tool"),
                    "verdict": p.get("verdict"), "risk": p.get("risk"),
                    "executed": p.get("executed"), "reason": p.get("reason", "")}
        if t == EventType.APPROVAL_REQUIRED:
            return {**base, "type": "approval", "tool": p.get("tool")}
        if t == EventType.TOOL_REQUESTED:
            queue = completed_by_tool.get(p.get("tool"), [])
            node = {**base, "type": "tool", "tool": p.get("tool")}
            if queue:
                done = queue.pop(0)
                node["completed"] = True
                node["success"] = done.payload.get("success")
            else:
                node["completed"] = False
                node["interrupted"] = True
            return node
        # tool.completed is folded into its tool.requested node above

        if t == EventType.EVALUATION_COMPLETED:
            return {**base, "type": "evaluation", "passed": p.get("passed"),
                    "reason": p.get("reason"), "attempt": p.get("attempt")}

        if t == EventType.TASK_QUEUED:
            return {**base, "type": "task_queued"}
        if t == EventType.TASK_CLAIMED:
            return {**base, "type": "task_claimed", "worker_id": p.get("worker_id")}
        if t == EventType.TASK_REQUEUED:
            return {**base, "type": "task_requeued"}
        if t == EventType.TASK_COMPLETED:
            return {**base, "type": "task_completed", "answer": p.get("answer")}
        if t == EventType.TASK_FAILED:
            return {**base, "type": "task_failed", "error": p.get("error")}

        return None
