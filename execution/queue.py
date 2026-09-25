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

**Concurrency (AD-027 + AD-028).** The queue is safe under concurrent workers:
each worker thread gets its OWN connection (`execution/sqlite.SqliteStore`), and
every exclusive transition is ONE conditional UPDATE. `claim` is atomic and mints
a NEW claim-generation; terminal transitions (`complete`/`fail`) are conditional
on the EXACT (worker_id, generation) the worker acquired — so a stale worker that
lost its lease can never overwrite the re-claimed task (A2-1). The loser of any
transition gets an explicit result (`None`/`False`), never an exception or a
silent overwrite.
"""
from __future__ import annotations

from datetime import timedelta

from core.contracts import Claim, Event, Task, TaskStatus, new_id, utcnow
from core.events import EventType
from execution.sqlite import SqliteStore


class TaskQueue(SqliteStore):
    """A SQLite-backed task queue with durable lifecycle events."""

    def __init__(self, path: str, bus=None) -> None:
        self._bus = bus
        super().__init__(path)

    def _schema(self, conn) -> None:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS tasks ("
            "task_id TEXT PRIMARY KEY, title TEXT, status TEXT, worker_id TEXT, "
            "claimed_at TEXT, claim_generation INTEGER DEFAULT 0, "
            "user TEXT, agent TEXT, parent_run_id TEXT, answer TEXT, error TEXT)")

    def _migrate(self, conn, from_version: int) -> None:
        # v0 lacked claim_generation; v1 lacked agent/parent_run_id; v2 lacked
        # user. Reconcile each column idempotently, whichever version we came from.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
        if "claim_generation" not in cols:
            conn.execute("ALTER TABLE tasks ADD COLUMN claim_generation INTEGER DEFAULT 0")
        if "user" not in cols:
            conn.execute("ALTER TABLE tasks ADD COLUMN user TEXT")
        if "agent" not in cols:
            conn.execute("ALTER TABLE tasks ADD COLUMN agent TEXT")
        if "parent_run_id" not in cols:
            conn.execute("ALTER TABLE tasks ADD COLUMN parent_run_id TEXT")

    def _emit(self, event_type: str, task: Task, payload: dict | None = None) -> None:
        if self._bus is None:
            return
        self._bus.publish(Event(
            event_id=new_id("evt"), event_type=event_type, timestamp=utcnow(),
            run_id="", task_id=task.task_id, component="queue", status="success",
            payload=payload or {},
        ))

    def enqueue(self, task: Task) -> None:
        conn = self._conn()
        conn.execute(
            "INSERT OR IGNORE INTO tasks (task_id, title, status, worker_id, "
            "claimed_at, claim_generation, user, agent, parent_run_id, answer, error) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (task.task_id, task.title, TaskStatus.QUEUED.value, None, None, 0,
             task.user, task.agent, task.parent_run_id, None, None))
        conn.commit()
        self._emit(EventType.TASK_QUEUED, task)

    def claim(self, worker_id: str) -> Claim | None:
        """Atomically claim the oldest QUEUED task, minting a fresh
        claim-generation and stamping the lease (`claimed_at`).

        Returns the `Claim` the worker must present to a terminal transition; a
        concurrent loser gets an explicit None."""
        claimed_at = utcnow().isoformat()
        conn = self._conn()
        row = conn.execute(
            "UPDATE tasks SET status=?, worker_id=?, claimed_at=?, "
            "claim_generation=claim_generation+1 "
            "WHERE task_id = (SELECT task_id FROM tasks WHERE status=? ORDER BY rowid LIMIT 1) "
            "RETURNING task_id, title, claim_generation, user, agent, parent_run_id",
            (TaskStatus.CLAIMED.value, worker_id, claimed_at, TaskStatus.QUEUED.value),
        ).fetchone()
        conn.commit()
        if row is None:
            return None
        task = Task(task_id=row[0], title=row[1], status=TaskStatus.CLAIMED,
                    user=row[3] or "", agent=row[4] or "", parent_run_id=row[5] or "")
        claim = Claim(task=task, worker_id=worker_id, generation=row[2])
        self._emit(EventType.TASK_CLAIMED, task, {
            "worker_id": worker_id, "claimed_at": claimed_at,
            "claim_generation": row[2]})
        return claim

    def recover_abandoned(self, lease_seconds: float, now=None) -> list[str]:
        """Requeue CLAIMED tasks whose lease has expired — atomically.

        One conditional UPDATE ... RETURNING decides ownership: a task is requeued
        only if it is still CLAIMED, so two competing recoverers can never both
        requeue (and double-emit task.requeued for) the same task. `now` is
        injectable so tests can control the clock."""
        now = now or utcnow()
        cutoff = (now - timedelta(seconds=lease_seconds)).isoformat()
        conn = self._conn()
        rows = conn.execute(
            "UPDATE tasks SET status=?, worker_id=NULL, claimed_at=NULL "
            "WHERE status=? AND claimed_at IS NOT NULL AND claimed_at < ? "
            "RETURNING task_id",
            (TaskStatus.QUEUED.value, TaskStatus.CLAIMED.value, cutoff),
        ).fetchall()
        conn.commit()
        recovered = [r[0] for r in rows]
        for task_id in recovered:
            self._emit(EventType.TASK_REQUEUED, self.get(task_id))
        return recovered

    def complete(self, claim: Claim, answer: str) -> bool:
        """Terminal transition, guarded by the EXACT claim the worker acquired
        (A2-1): only the (worker_id, generation) that currently owns the task may
        mark it DONE. A stale owner's completion is a no-op (returns False)."""
        conn = self._conn()
        cur = conn.execute(
            "UPDATE tasks SET status=?, answer=? "
            "WHERE task_id=? AND status=? AND worker_id=? AND claim_generation=?",
            (TaskStatus.DONE.value, answer, claim.task.task_id, TaskStatus.CLAIMED.value,
             claim.worker_id, claim.generation))
        conn.commit()
        if cur.rowcount != 1:
            return False  # stale owner: this generation no longer holds the task
        self._emit(EventType.TASK_COMPLETED, self.get(claim.task.task_id),
                   {"answer": answer})
        return True

    def fail(self, claim: Claim, error: str) -> bool:
        """Terminal transition, guarded like `complete` (A2-1)."""
        conn = self._conn()
        cur = conn.execute(
            "UPDATE tasks SET status=?, error=? "
            "WHERE task_id=? AND status=? AND worker_id=? AND claim_generation=?",
            (TaskStatus.FAILED.value, error, claim.task.task_id, TaskStatus.CLAIMED.value,
             claim.worker_id, claim.generation))
        conn.commit()
        if cur.rowcount != 1:
            return False
        self._emit(EventType.TASK_FAILED, self.get(claim.task.task_id), {"error": error})
        return True

    def wait(self, task_id: str) -> None:
        """A task pauses for human approval (durable state transition)."""
        conn = self._conn()
        conn.execute("UPDATE tasks SET status=? WHERE task_id=?",
                     (TaskStatus.AWAITING_APPROVAL.value, task_id))
        conn.commit()
        self._emit(EventType.TASK_WAITING, self.get(task_id))

    def requeue(self, task_id: str) -> None:
        """An approved task becomes runnable again (surface command)."""
        conn = self._conn()
        conn.execute(
            "UPDATE tasks SET status=?, worker_id=NULL, claimed_at=NULL WHERE task_id=?",
            (TaskStatus.QUEUED.value, task_id))
        conn.commit()
        self._emit(EventType.TASK_REQUEUED, self.get(task_id))

    def fail_waiting(self, task_id: str, error: str) -> None:
        """A surface command (deny): fail a task durably WAITING for approval.
        There is no owner to guard — the task was released to await a human."""
        conn = self._conn()
        conn.execute(
            "UPDATE tasks SET status=?, error=? WHERE task_id=? AND status=?",
            (TaskStatus.FAILED.value, error, task_id, TaskStatus.AWAITING_APPROVAL.value))
        conn.commit()
        self._emit(EventType.TASK_FAILED, self.get(task_id), {"error": error})

    def get(self, task_id: str) -> Task | None:
        conn = self._conn()
        row = conn.execute(
            "SELECT task_id, title, status, user, agent, parent_run_id FROM tasks WHERE task_id=?",
            (task_id,)).fetchone()
        if row is None:
            return None
        return Task(task_id=row[0], title=row[1], status=TaskStatus(row[2]),
                    user=row[3] or "", agent=row[4] or "", parent_run_id=row[5] or "")

    def owner(self, task_id: str) -> str | None:
        """The worker_id currently owning a task (None if not claimed)."""
        conn = self._conn()
        row = conn.execute(
            "SELECT worker_id FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        return None if row is None else row[0]

    def generation(self, task_id: str) -> int | None:
        """The current claim-generation (None if the task doesn't exist)."""
        conn = self._conn()
        row = conn.execute(
            "SELECT claim_generation FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        return None if row is None else row[0]
