"""Durable approval store — human authorization of specific tool calls.

An approval is a durable record bound to ONE proposed action (task/run/tool/risk),
so approving one action can never authorize another. It is single-use:
pending -> approved/denied -> consumed. Each transition is persisted to SQLite
and emitted as an approval.* event, so the decision chain is reconstructible.

**Concurrency (AD-027).** The store is safe under concurrent workers: each worker
thread gets its OWN connection (see `execution/sqlite.SqliteStore`), and the one
exclusive transition — `consume_approved` — is ONE conditional UPDATE. The
database arbitrates the race; the loser gets `None` (explicit), never an
exception or a silent overwrite. There is no Python-side check-then-act.
"""
from __future__ import annotations

from core.contracts import ApprovalRequest, Event, Risk, new_id, utcnow
from core.events import EventType
from execution.sqlite import SqliteStore


class ApprovalStore(SqliteStore):
    """A SQLite-backed store of approval records with durable lifecycle events."""

    def __init__(self, path: str, bus=None) -> None:
        self._bus = bus
        super().__init__(path)

    def _schema(self, conn) -> None:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS approvals ("
            "approval_id TEXT PRIMARY KEY, task_id TEXT, run_id TEXT, "
            "tool_name TEXT, risk TEXT, status TEXT)")

    def _emit(self, event_type: str, approval: ApprovalRequest, payload: dict) -> None:
        if self._bus is None:
            return
        self._bus.publish(Event(
            event_id=new_id("evt"), event_type=event_type, timestamp=utcnow(),
            run_id=approval.run_id, task_id=approval.task_id, component="approvals",
            status="success", payload=payload,
        ))

    def create(self, approval: ApprovalRequest) -> None:
        """Record a pending approval and emit approval.required."""
        conn = self._conn()
        conn.execute("INSERT OR REPLACE INTO approvals VALUES (?,?,?,?,?,?)",
                     (approval.approval_id, approval.task_id, approval.run_id,
                      approval.tool_name, approval.risk.value, approval.status))
        conn.commit()
        self._emit(EventType.APPROVAL_REQUIRED, approval, {
            "approval_id": approval.approval_id,
            "tool": approval.tool_name,
            "risk": approval.risk.value,
        })

    def _transition(self, approval_id: str, status: str, event_type: str) -> ApprovalRequest:
        conn = self._conn()
        conn.execute("UPDATE approvals SET status=? WHERE approval_id=?",
                     (status, approval_id))
        conn.commit()
        approval = self.get(approval_id)
        if approval is not None:
            self._emit(event_type, approval, {
                "approval_id": approval_id, "tool": approval.tool_name,
            })
        return approval

    def approve(self, approval_id: str) -> ApprovalRequest:
        return self._transition(approval_id, "approved", EventType.APPROVAL_GRANTED)

    def deny(self, approval_id: str) -> ApprovalRequest:
        return self._transition(approval_id, "denied", EventType.APPROVAL_DENIED)

    def consume_approved(self, approval_id: str) -> ApprovalRequest | None:
        """Atomically consume an APPROVED approval (single-use).

        The transition is ONE conditional UPDATE, so the database — not Python
        timing — decides the winner: exactly one caller transitions APPROVED ->
        CONSUMED (rowcount 1); every other caller gets 0 and MUST NOT execute."""
        conn = self._conn()
        cur = conn.execute(
            "UPDATE approvals SET status='consumed' WHERE approval_id=? AND status='approved'",
            (approval_id,))
        conn.commit()
        if cur.rowcount != 1:
            return None  # already consumed (or never approved) -> lost the race
        approval = self.get(approval_id)
        self._emit(EventType.APPROVAL_CONSUMED, approval, {
            "approval_id": approval_id, "tool": approval.tool_name,
        })
        return approval

    def find_approved(self, task_id: str, tool_name: str, risk: Risk) -> ApprovalRequest | None:
        """The single approved (unconsumed) approval for this exact proposal."""
        conn = self._conn()
        row = conn.execute(
            "SELECT approval_id FROM approvals WHERE task_id=? AND tool_name=? "
            "AND risk=? AND status='approved' ORDER BY rowid LIMIT 1",
            (task_id, tool_name, risk.value),
        ).fetchone()
        return self.get(row[0]) if row else None

    def get(self, approval_id: str) -> ApprovalRequest | None:
        conn = self._conn()
        row = conn.execute(
            "SELECT approval_id, task_id, run_id, tool_name, risk, status "
            "FROM approvals WHERE approval_id=?", (approval_id,)).fetchone()
        if row is None:
            return None
        return ApprovalRequest(approval_id=row[0], task_id=row[1], run_id=row[2],
                               tool_name=row[3], risk=Risk(row[4]), status=row[5])
