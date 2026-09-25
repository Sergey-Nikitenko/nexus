"""Thin HTTP adapter — the surface over the runtime.

FastAPI/Pydantic stop here. Nexus contracts are serialized at the edge; no HTTP
type crosses into the runtime, and the runtime has no idea HTTP exists. The
endpoints delegate to NexusRuntime (ask / task / events) and project to JSON.
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Response, status
from pydantic import BaseModel

from core.state import TaskState
from apps.ws import register_websocket


class AskRequest(BaseModel):
    """The HTTP edge's request shape — it never propagates into Nexus."""
    title: str


def _serialize_event(e) -> dict:
    """Project a Nexus Event to JSON, preserving correlation fields."""
    return {
        "event_id": e.event_id,
        "event_type": e.event_type,
        "timestamp": e.timestamp.isoformat(),
        "run_id": e.run_id,
        "task_id": e.task_id,
        "component": e.component,
        "status": e.status,
        "parent_event_id": e.parent_event_id,
        "payload": e.payload,
    }


def create_app(runtime) -> FastAPI:
    app = FastAPI(title="Nexus")
    register_websocket(app, runtime)

    @app.post("/ask", status_code=status.HTTP_202_ACCEPTED)
    def ask(req: AskRequest, response: Response):
        task_id = runtime.ask(req.title)
        response.headers["X-Task-ID"] = task_id  # correlation preserved at the edge
        return {"task_id": task_id, "status": "queued"}

    @app.get("/tasks/{task_id}")
    def get_task(task_id: str):
        task = runtime.task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="unknown task")
        state = TaskState.reconstruct(task, runtime.events(task_id=task_id))
        return {
            "task_id": task.task_id,
            "title": task.title,
            "status": state.status.value,
            "worker_id": state.worker_id,
        }

    @app.get("/traces/{run_id}")
    def get_trace(run_id: str):
        events = runtime.events(run_id=run_id)
        if not events:
            raise HTTPException(status_code=404, detail="unknown trace")
        return {"run_id": run_id, "events": [_serialize_event(e) for e in events]}

    @app.post("/approvals/{approval_id}/approve")
    def approve(approval_id: str):
        approval = runtime.approve(approval_id)
        if approval is None:
            raise HTTPException(status_code=404, detail="unknown approval")
        return {"approval_id": approval_id, "status": "approved"}

    @app.post("/approvals/{approval_id}/deny")
    def deny(approval_id: str):
        approval = runtime.deny(approval_id)
        if approval is None:
            raise HTTPException(status_code=404, detail="unknown approval")
        return {"approval_id": approval_id, "status": "denied"}

    return app
