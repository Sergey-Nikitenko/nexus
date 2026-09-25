"""CLI — a thin surface adapter over NexusRuntime.

The CLI commands Nexus (ask / task / trace / approve / deny); it has no
orchestration, policy, queue, or provider logic. It renders the SAME projected
information the dashboard uses, so REST, WebSocket, the dashboard, and the CLI
are four views of the same contracts and events.
"""
from __future__ import annotations

from core.state import TaskState
from apps.dashboard import build_view
from observability.trace import TraceProjector


def dispatch(runtime, argv) -> str:
    projector = TraceProjector()
    if not argv:
        return "usage: nexus ask <title> | task <id> | trace <run-id> | approve <id> | deny <id>"
    cmd, *rest = argv

    if cmd == "ask":
        task_id = runtime.ask(" ".join(rest))
        return f"Task: {task_id}\nStatus: queued"

    if cmd == "task":
        task_id = rest[0]
        task = runtime.task(task_id)
        if task is None:
            return f"Task: {task_id}\nStatus: unknown"
        state = TaskState.reconstruct(task, runtime.events(task_id=task_id))
        lines = [f"Task: {task_id}", f"Status: {state.status.value}"]
        if state.worker_id:
            lines.append(f"Worker: {state.worker_id}")
        lines.extend(_render_trace(projector.project(runtime.events(task_id=task_id))))
        return "\n".join(lines)

    if cmd == "trace":
        run_id = rest[0]
        events = runtime.events(run_id=run_id)
        if not events:
            return f"Trace: {run_id}\nStatus: unknown"
        lines = [f"Trace: {run_id}"]
        lines.extend(_render_trace(projector.project(events)))
        return "\n".join(lines)

    if cmd == "approve":
        approval = runtime.approve(rest[0])
        return f"Approval: {rest[0]}\nStatus: {'approved' if approval else 'unknown'}"

    if cmd == "deny":
        approval = runtime.deny(rest[0])
        return f"Approval: {rest[0]}\nStatus: {'denied' if approval else 'unknown'}"

    return f"unknown command: {cmd}"


def _render_trace(trace) -> list[str]:
    """Render the dashboard view model as text — no logic, just formatting."""
    view = build_view(trace)
    lines = [f"Run {view['run_id'] or '?'}"]
    for a in view["attempts"]:
        lines.append(f"  attempt {a['attempt']}: evaluation {'PASS' if a['passed'] else 'FAIL'}")
    for d in view["decisions"]:
        lines.append(f"  policy {d['tool']}: {d['risk']} -> {d['verdict']}")
    for i in view["interrupted"]:
        lines.append(f"  interrupted: {i['tool']}")
    return lines
