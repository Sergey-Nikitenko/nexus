"""Durable run identity — the RunManifest + semantic fingerprint, first-class.

The manifest identifies the CONDITIONS of a run; the fingerprint identifies its
SEMANTIC OUTCOME. Neither is the run's identity — `run_id` remains the execution
identity, and neither is written into every event. These are derived evidence
ABOUT the execution, persisted durably so a fresh process can answer "what was
this run given, and what did it semantically produce?" from the run_id alone.

A partial/crashed run has a manifest but NO fingerprint: the fingerprint is only
written when the run reaches a terminal state (`run.completed` / `run.failed`).
"""
from __future__ import annotations

import json
from dataclasses import asdict

from core.contracts import RunManifest
from execution.sqlite import SqliteStore


class RunRecordStore(SqliteStore):
    """Persists (run_id, manifest, fingerprint, status) as first-class records."""

    def __init__(self, path: str) -> None:
        super().__init__(path)

    def _schema(self, conn) -> None:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS run_records ("
            "run_id TEXT PRIMARY KEY, manifest TEXT, fingerprint TEXT, status TEXT)")

    def record_manifest(self, run_id: str, manifest: RunManifest) -> None:
        conn = self._conn()
        conn.execute(
            "INSERT OR REPLACE INTO run_records (run_id, manifest, fingerprint, status) "
            "VALUES (?,?,NULL,NULL)",
            (run_id, json.dumps(asdict(manifest))))
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
            "SELECT run_id, manifest, fingerprint, status FROM run_records WHERE run_id=?",
            (run_id,)).fetchone()
        if row is None:
            return None
        return {
            "run_id": row[0],
            "manifest": json.loads(row[1]) if row[1] else None,
            "fingerprint": row[2],
            "status": row[3],
        }
