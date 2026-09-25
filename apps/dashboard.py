"""Dashboard — a disposable presentation layer over Nexus projections.

The dashboard consumes the projected trace (core.Trace) and the approval API. It
renders state; it never reconstructs authority. It has no business logic — the
"why" of every decision comes from the durable `policy.decision` event, never
from a UI-side `if risk == ...` check. Delete this file and the runtime, API,
WebSocket, workers, events, state, and recovery all keep working.
"""
from __future__ import annotations


def build_view(trace) -> dict:
    """The full dashboard state, derived from projected trace nodes."""
    nodes = trace.nodes
    return {
        "run_id": trace.run_id,
        "status": _status(nodes),
        "milestones": _milestones(nodes),      # live-run checklist
        "attempts": _attempts(nodes),          # replan history
        "decisions": _decisions(nodes),        # Why-panel source
        "recovery": _recovery(nodes),          # claim/requeue transitions
        "interrupted": _interrupted(nodes),    # incomplete tool executions
    }


def why(node) -> dict:
    """The Why panel for one projected decision node — renders, never computes."""
    return {
        "tool": node.get("tool"),
        "risk": node.get("risk"),
        "verdict": node.get("verdict"),
        "executed": node.get("executed"),
        "reason": node.get("reason", ""),
        "event_id": node.get("event_id"),
        "parent_event_id": node.get("parent_event_id"),
        "run_id": node.get("run_id"),
        "task_id": node.get("task_id"),
        "timestamp": node.get("timestamp"),
    }


def _status(nodes) -> str:
    if any(n.get("type") == "run" and n.get("status") == "completed" for n in nodes):
        return "completed"
    if any(n.get("type") == "run" and n.get("status") == "failed" for n in nodes):
        return "failed"
    if any(n.get("type") == "waiting" for n in nodes):
        return "waiting"
    return "running"


def _milestones(nodes) -> list[str]:
    seen = []
    for n in nodes:
        t = n.get("type")
        if t == "plan":
            seen.append("plan")
        elif t == "retrieval":
            seen.append("retrieve")
        elif t == "decision":
            seen.append(f"{n['tool']}: {n['risk']} -> {n['verdict']}")
        elif t == "tool":
            seen.append(f"{n['tool']} executed" if n.get("completed")
                        else f"{n['tool']} interrupted")
        elif t == "model_response":
            seen.append("model")
        elif t == "evaluation":
            seen.append(f"verify: {'PASS' if n.get('passed') else 'FAIL'}")
    return seen


def _attempts(nodes) -> list[dict]:
    attempts = {}
    for n in nodes:
        if n.get("type") == "evaluation":
            attempts[n["attempt"]] = {
                "attempt": n["attempt"], "passed": n["passed"], "reason": n.get("reason"),
            }
    return [attempts[k] for k in sorted(attempts)]


def _decisions(nodes) -> list[dict]:
    return [{
        "tool": n["tool"], "risk": n["risk"], "verdict": n["verdict"],
        "executed": n["executed"], "reason": n.get("reason", ""),
        "event_id": n["event_id"], "parent_event_id": n["parent_event_id"],
        "run_id": n["run_id"], "task_id": n["task_id"], "timestamp": n["timestamp"],
    } for n in nodes if n.get("type") == "decision"]


def _recovery(nodes) -> list[dict]:
    out = []
    for n in nodes:
        if n.get("type") == "task_claimed":
            out.append({"event": "claimed", "worker_id": n.get("worker_id"),
                        "event_id": n["event_id"]})
        elif n.get("type") == "task_requeued":
            out.append({"event": "requeued", "event_id": n["event_id"]})
        elif n.get("type") == "task_completed":
            out.append({"event": "completed", "answer": n.get("answer"),
                        "event_id": n["event_id"]})
    return out


def _interrupted(nodes) -> list[dict]:
    return [{"tool": n["tool"], "event_id": n["event_id"]} for n in nodes
            if n.get("type") == "tool" and n.get("interrupted")]
