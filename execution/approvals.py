"""Durable approval store — human authorization of specific tool calls.

An approval is a durable record bound to ONE proposed action (task/run/tool/risk),
so approving one action can never authorize another. It is single-use:
pending -> approved/denied -> consumed. Each transition is persisted to SQLite
and emitted as an approval.* event, so the decision chain is reconstructible.
"""
from __future__ import annotations

import sqlite3

from core.contracts import ApprovalRequest, Event, Risk, new_id, utcnow
from core.events import EventType


class ApprovalStore:
    """A SQLite-backed store of approval records with durable lifecycle events."""

    def __init__(self, path: str, bus=None) -> None:
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS approvals ("
            "approval_id TEXT PRIMARY KEY, task_id TEXT, run_id TEXT, "
            "tool_name TEXT, risk TEXT, status TEXT)")
        self._conn.commit()
        self._bus = bus

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
        self._conn.execute("INSERT OR REPLACE INTO approvals VALUES (?,?,?,?,?,?)",
                           (approval.approval_id, approval.task_id, approval.run_id,
                            approval.tool_name, approval.risk.value, approval.status))
        self._conn.commit()
        self._emit(EventType.APPROVAL_REQUIRED, approval, {
            "approval_id": approval.approval_id,
            "tool": approval.tool_name,
            "risk": approval.risk.value,
        })

    def _transition(self, approval_id: str, status: str, event_type: str) -> ApprovalRequest:
        self._conn.execute("UPDATE approvals SET status=? WHERE approval_id=?",
                           (status, approval_id))
        self._conn.commit()
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

    def consume(self, approval_id: str) -> ApprovalRequest:
        return self._transition(approval_id, "consumed", EventType.APPROVAL_CONSUMED)

    def find_approved(self, task_id: str, tool_name: str, risk: Risk) -> ApprovalRequest | None:
        """The single approved (unconsumed) approval for this exact proposal."""
        row = self._conn.execute(
            "SELECT approval_id FROM approvals WHERE task_id=? AND tool_name=? "
            "AND risk=? AND status='approved' ORDER BY rowid LIMIT 1",
            (task_id, tool_name, risk.value),
        ).fetchone()
        return self.get(row[0]) if row else None

    def get(self, approval_id: str) -> ApprovalRequest | None:
        row = self._conn.execute(
            "SELECT approval_id, task_id, run_id, tool_name, risk, status "
            "FROM approvals WHERE approval_id=?", (approval_id,)).fetchone()
        if row is None:
            return None
        return ApprovalRequest(approval_id=row[0], task_id=row[1], run_id=row[2],
                               tool_name=row[3], risk=Risk(row[4]), status=row[5])

    def close(self) -> None:
        self._conn.close()
