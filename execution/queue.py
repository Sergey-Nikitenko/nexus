"""Durable task queue — a task's lifecycle, recorded durably.

    enqueue -> claim -> (run) -> complete/fail

Each transition is persisted to SQLite AND emitted as a `task.*` event
(`task.queued` / `task.claimed` / `task.requeued` / `task.completed` /
`task.failed`). Ownership is a durable event, so a task can never be silently
lost: it is QUEUED, CLAIMED (by worker_id, with a lease), DONE, or FAILED —
never "in some worker's memory".

A CLAIMED task whose lease has expired is RECOVERABLE: `recover_abandoned`
requeues it based purely on durable evidence (status + `claimed_at`), with no
knowledge of why the worker disappeared.

Delivery is AT-LEAST-ONCE, by design: a task claimed by a worker that dies
before completing stays CLAIMED (recoverable), and re-running may execute a tool
twice. That is explicit and observable, never silently promised away.
"""
from __future__ import annotations

import sqlite3
from datetime import timedelta

from core.contracts import Event, Task, TaskStatus, new_id, utcnow
from core.events import EventType


class TaskQueue:
    """A SQLite-backed task queue with durable lifecycle events."""

    def __init__(self, path: str, bus=None) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS tasks ("
            "task_id TEXT PRIMARY KEY, title TEXT, status TEXT, worker_id TEXT, "
            "claimed_at TEXT, answer TEXT, error TEXT)")
        self._conn.commit()
        self._bus = bus

    def _emit(self, event_type: str, task: Task, payload: dict | None = None) -> None:
        if self._bus is None:
            return
        self._bus.publish(Event(
            event_id=new_id("evt"), event_type=event_type, timestamp=utcnow(),
            run_id="", task_id=task.task_id, component="queue", status="success",
            payload=payload or {},
        ))

    def enqueue(self, task: Task) -> None:
        self._conn.execute("INSERT OR IGNORE INTO tasks VALUES (?,?,?,?,?,?,?)",
                           (task.task_id, task.title, TaskStatus.QUEUED.value,
                            None, None, None, None))
        self._conn.commit()
        self._emit(EventType.TASK_QUEUED, task)

    def claim(self, worker_id: str) -> Task | None:
        """Atomically claim the oldest QUEUED task (single UPDATE ... RETURNING),
        stamping the lease (`claimed_at`)."""
        claimed_at = utcnow().isoformat()
        row = self._conn.execute(
            "UPDATE tasks SET status=?, worker_id=?, claimed_at=? WHERE task_id = "
            "(SELECT task_id FROM tasks WHERE status=? ORDER BY rowid LIMIT 1) "
            "RETURNING task_id, title",
            (TaskStatus.CLAIMED.value, worker_id, claimed_at, TaskStatus.QUEUED.value),
        ).fetchone()
        self._conn.commit()
        if row is None:
            return None
        task = Task(task_id=row[0], title=row[1], status=TaskStatus.CLAIMED)
        self._emit(EventType.TASK_CLAIMED, task,
                   {"worker_id": worker_id, "claimed_at": claimed_at})
        return task

    def recover_abandoned(self, lease_seconds: float, now=None) -> list[str]:
        """Requeue CLAIMED tasks whose lease has expired.

        Recovery is based on durable evidence (status=CLAIMED + claimed_at older
        than the lease) — never on knowing which worker died. `now` is injectable
        so tests can control the clock."""
        now = now or utcnow()
        cutoff = (now - timedelta(seconds=lease_seconds)).isoformat()
        rows = self._conn.execute(
            "SELECT task_id FROM tasks WHERE status=? AND claimed_at IS NOT NULL "
            "AND claimed_at < ?",
            (TaskStatus.CLAIMED.value, cutoff),
        ).fetchall()
        recovered = []
        for (task_id,) in rows:
            self._conn.execute(
                "UPDATE tasks SET status=?, worker_id=NULL, claimed_at=NULL "
                "WHERE task_id=?", (TaskStatus.QUEUED.value, task_id))
            recovered.append(task_id)
        self._conn.commit()
        for task_id in recovered:
            self._emit(EventType.TASK_REQUEUED, self.get(task_id))
        return recovered

    def complete(self, task_id: str, answer: str) -> None:
        self._conn.execute("UPDATE tasks SET status=?, answer=? WHERE task_id=?",
                           (TaskStatus.DONE.value, answer, task_id))
        self._conn.commit()
        self._emit(EventType.TASK_COMPLETED, self.get(task_id), {"answer": answer})

    def fail(self, task_id: str, error: str) -> None:
        self._conn.execute("UPDATE tasks SET status=?, error=? WHERE task_id=?",
                           (TaskStatus.FAILED.value, error, task_id))
        self._conn.commit()
        self._emit(EventType.TASK_FAILED, self.get(task_id), {"error": error})

    def get(self, task_id: str) -> Task | None:
        row = self._conn.execute(
            "SELECT task_id, title, status FROM tasks WHERE task_id=?",
            (task_id,)).fetchone()
        if row is None:
            return None
        return Task(task_id=row[0], title=row[1], status=TaskStatus(row[2]))

    def close(self) -> None:
        self._conn.close()
