"""Durable run identity — the RunManifest + semantic fingerprint, first-class.

The manifest identifies the CONDITIONS of a run; the fingerprint identifies its
SEMANTIC OUTCOME. Neither is the run's identity — `run_id` remains the execution
identity, and neither is written into every event. These are derived evidence
ABOUT the execution, persisted durably so a fresh process can answer "what was
this run given, and what did it semantically produce?" from the run_id alone.

A partial/crashed run has a manifest but NO fingerprint: the fingerprint is only
written when the run reaches a terminal state AND its claim generation is still
current (AD-032) — so a stale run can never publish a terminal result.
"""
from __future__ import annotations

import json
from dataclasses import asdict

from core.contracts import RunManifest
from execution.sqlite import SqliteStore


class RunRecordStore(SqliteStore):
    """Persists (run_id, task_id, manifest, fingerprint, status) as first-class
    records, partitioned by run_id. `task_id` is denormalized so a task's runs
    can be listed to answer "which run is authoritative for this task?"."""

    def __init__(self, path: str) -> None:
        super().__init__(path)

    def _schema(self, conn) -> None:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS run_records ("
            "run_id TEXT PRIMARY KEY, task_id TEXT, manifest TEXT, "
            "fingerprint TEXT, status TEXT)")

    def record_manifest(self, run_id: str, task_id: str, manifest: RunManifest) -> None:
        conn = self._conn()
        conn.execute(
            "INSERT OR REPLACE INTO run_records (run_id, task_id, manifest, fingerprint, status) "
            "VALUES (?,?,?,NULL,NULL)",
            (run_id, task_id, json.dumps(asdict(manifest))))
        conn.commit()

    def record_terminal(self, run_id: str, fingerprint: str, status: str) -> None:
        conn = self._conn()
        conn.execute(
            "UPDATE run_records SET fingerprint=?, status=? WHERE run_id=?",
            (fingerprint, status, run_id))
        conn.commit()

    def get(self, run_id: str) -> dict | None:
        conn = self._conn()
        row = conn.execute(
            "SELECT run_id, task_id, manifest, fingerprint, status "
            "FROM run_records WHERE run_id=?",
            (run_id,)).fetchone()
        if row is None:
            return None
        return {
            "run_id": row[0],
            "task_id": row[1],
            "manifest": json.loads(row[2]) if row[2] else None,
            "fingerprint": row[3],
            "status": row[4],
        }

    def for_task(self, task_id: str) -> list[dict]:
        """All run records for a task, in insertion (rowid) order."""
        conn = self._conn()
        rows = conn.execute(
            "SELECT run_id, task_id, manifest, fingerprint, status "
            "FROM run_records WHERE task_id=? ORDER BY rowid",
            (task_id,)).fetchall()
        return [{
            "run_id": r[0], "task_id": r[1],
            "manifest": json.loads(r[2]) if r[2] else None,
            "fingerprint": r[3], "status": r[4],
        } for r in rows]
