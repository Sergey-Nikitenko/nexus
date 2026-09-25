"""WebSocket adapter — subscribes to the event bus, never the orchestrator.

The browser tails a run's projected trace: the durable history is replayed, then
live events are pushed through a bounded per-client queue. The orchestrator has
no idea a browser exists, and the event bus has no idea a browser exists — the
WebSocket filters by run_id at the edge and projects through the TraceProjector.

Backpressure policy: each client gets a bounded queue (MAX_QUEUE); if a client
can't consume fast enough, it is disconnected rather than stalling the event bus.
A slow or absent browser can never block event production.
"""
from __future__ import annotations

import asyncio
import queue as _queue

from fastapi import WebSocket, WebSocketDisconnect

from observability.trace import TraceProjector

MAX_QUEUE = 1024


def register_websocket(app, runtime) -> None:
    projector = TraceProjector()

    @app.websocket("/ws/runs/{run_id}")
    async def ws_run(websocket: WebSocket, run_id: str):
        await websocket.accept()
        bus = runtime.event_bus

        # replay the durable history (projected, so no provider vocabulary)
        events = runtime.events(run_id=run_id)
        completed = any(e.event_type == "run.completed" for e in events)
        for node in projector.project(events).nodes:
            await websocket.send_json(node)
        if completed:
            await websocket.send_json({"type": "complete", "run_id": run_id})
            await websocket.close()
            return

        # live tail: a bounded thread-safe queue bridges the sync bus -> async ws
        q: _queue.Queue = _queue.Queue(maxsize=MAX_QUEUE)
        overflowed: list[bool] = []

        def on_event(event):
            if event.run_id != run_id:  # filter at the edge
                return
            node = projector.project([event]).nodes
            if not node:
                return
            try:
                q.put_nowait(node[0])
            except _queue.Full:
                overflowed.append(True)  # slow consumer -> disconnect-on-overflow

        bus.subscribe_all(on_event)
        try:
            while True:
                node = await asyncio.to_thread(q.get)
                if overflowed:
                    await websocket.send_json({"type": "error", "detail": "slow consumer"})
                    break
                await websocket.send_json(node)
                if node.get("event") in ("run.completed", "run.failed"):
                    await websocket.send_json({"type": "complete", "run_id": run_id})
                    break
        except WebSocketDisconnect:
            pass
        finally:
            bus.unsubscribe_all(on_event)
            try:
                await websocket.close()
            except Exception:
                pass
