"""Durable event bus — the event log that survives a worker crash.

A worker never owns execution solely in memory. Every event is appended to a
SQLite event log, so a crash leaves "last durable event = X", never "the worker
died and took its memory with it". Reconstruction reads the log back.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from core.contracts import Event
from core.events import EventBus


class DurableEventBus(EventBus):
    """An EventBus that also persists every published event to SQLite."""

    def __init__(self, path: str) -> None:
        super().__init__()
        self._conn = sqlite3.connect(path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS events ("
            "event_id TEXT PRIMARY KEY, event_type TEXT, timestamp TEXT, "
            "run_id TEXT, task_id TEXT, component TEXT, status TEXT, "
            "payload TEXT, parent_event_id TEXT)")
        self._conn.commit()

    def publish(self, event: Event) -> None:
        super().publish(event)  # in-memory history + synchronous handlers
        self._conn.execute(
            "INSERT OR REPLACE INTO events VALUES (?,?,?,?,?,?,?,?,?)",
            (event.event_id, event.event_type, event.timestamp.isoformat(),
             event.run_id, event.task_id, event.component, event.status,
             json.dumps(event.payload), event.parent_event_id),
        )
        self._conn.commit()

    def load_events(self, task_id: str | None = None,
                    run_id: str | None = None) -> list[Event]:
        """Reload events from the durable log, optionally scoped to a task/run."""
        query = ("SELECT event_id, event_type, timestamp, run_id, task_id, "
                 "component, status, payload, parent_event_id FROM events")
        where, params = [], []
        if task_id is not None:
            where.append("task_id=?")
            params.append(task_id)
        if run_id is not None:
            where.append("run_id=?")
            params.append(run_id)
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY rowid"

        events = []
        for row in self._conn.execute(query, params):
            events.append(Event(
                event_id=row[0], event_type=row[1],
                timestamp=datetime.fromisoformat(row[2]),
                run_id=row[3], task_id=row[4], component=row[5],
                status=row[6], payload=json.loads(row[7]), parent_event_id=row[8],
            ))
        return events

    def close(self) -> None:
        self._conn.close()
