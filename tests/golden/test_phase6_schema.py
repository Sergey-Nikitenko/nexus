"""Phase 6.2 golden task — persisted-schema versioning.

The persisted format now carries an explicit version, with a compatibility rule
and a real migration path. This test proves three things:

1. an ACTUAL older fixture (the pre-versioning v0 tasks table, WITHOUT the
   `claim_generation` column added in 5.9) is migrated at open — the column is
   added idempotently, the data survives untouched, and the store is functional;
2. a FUTURE version is rejected deterministically, never silently misread;
3. the schema version is recorded SEPARATELY from contracts and component
   identities — it lives in `PRAGMA user_version`, never in an event payload or a
   RunManifest field — so 6.1 fingerprints/manifests are semantically unchanged.

Run:  py tests/golden/test_phase6_schema.py
"""
import os
import sqlite3
import sys
import tempfile
from dataclasses import fields as dc_fields

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import RunManifest  # noqa: E402
from execution.queue import TaskQueue  # noqa: E402
from execution.schema import SCHEMA_VERSION, read_version  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def make_v0_tasks(path):
    """An ACTUAL pre-6.2 fixture: the 7-column tasks table (no claim_generation),
    one legacy row, and no user_version stamp (SQLite default 0)."""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE tasks (task_id TEXT PRIMARY KEY, title TEXT, "
                 "status TEXT, worker_id TEXT, claimed_at TEXT, answer TEXT, error TEXT)")
    conn.execute("INSERT INTO tasks VALUES ('task_legacy', 'legacy task', "
                 "'queued', NULL, NULL, NULL, NULL)")
    conn.commit()
    conn.close()


def make_future_tasks(path):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version = 99")
    conn.commit()
    conn.close()


def main():
    print("Phase 6.2 golden task: persisted-schema versioning")
    tmp = tempfile.mkdtemp()

    # --- 1. migration from an actual v0 fixture -----------------------------
    path = os.path.join(tmp, "v0.db")
    make_v0_tasks(path)
    q = TaskQueue(path)  # open -> detects v0 -> migrates -> stamps SCHEMA_VERSION

    task = q.get("task_legacy")
    check(task is not None and task.title == "legacy task" and task.status.value == "queued",
          "legacy data survives the migration untouched")
    check(read_version(q._conn()) == SCHEMA_VERSION,
          "the on-disk schema version is stamped to the current version")

    claim = q.claim("w1")  # would fail if claim_generation were missing
    check(claim is not None and claim.task.task_id == "task_legacy",
          "the migrated store is functional (claim_generation column added)")

    # re-open: idempotent (already current -> no migration, still readable)
    q.close()
    q2 = TaskQueue(path)
    check(q2.get("task_legacy") is not None, "re-opening a current-version store works")
    q2.close()

    # --- 2. a FUTURE version is rejected deterministically ------------------
    path2 = os.path.join(tmp, "future.db")
    make_future_tasks(path2)
    try:
        TaskQueue(path2)
        raise AssertionError("expected the future schema version to be rejected")
    except RuntimeError as exc:
        check("newer" in str(exc), "a future schema version is rejected (never silently misread)")

    # --- 3. the schema version is recorded separately -----------------------
    manifest_fields = {f.name for f in dc_fields(RunManifest)}
    check("schema_version" not in manifest_fields and "user_version" not in manifest_fields,
          "RunManifest has no schema-version field (schema version != contract/component identity)")
    check(isinstance(SCHEMA_VERSION, int) and SCHEMA_VERSION > 0,
          "SCHEMA_VERSION is a distinct on-disk format version (a positive int)")
    # the migration is column-additive and header-only: it never rewrites a data
    # row's values, so event payloads (and therefore 6.1 fingerprints) are unchanged.

    print("\nPASS: Phase 6.2 persisted-schema versioning holds.")


if __name__ == "__main__":
    main()
