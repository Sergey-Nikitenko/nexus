"""Persisted-schema version — the on-disk format's compatibility contract.

This is a DIFFERENT number from contract versions (`core/contracts.py`) and from
component identities (`policy@1`, `fake/deterministic`, a knowledge snapshot).
Those answer "what shape is this code contract / this component?"; this answers
"what version is the data on disk, and can this build read it?" Mixing the three
is a category error: a policy version can change without touching the on-disk
format, and vice versa.

Compatibility rule (deliberately boring):

- A build READS schema version == SCHEMA_VERSION.
- A build MIGRATES version 0 (the pre-versioning legacy format) to SCHEMA_VERSION
  at open, before any read/write, via each store's `_migrate` hook.
- A build REJECTS version > SCHEMA_VERSION (data written by a newer build must
  never be silently misread) and any 0 < version < SCHEMA_VERSION with no
  migration path — with a deterministic error, never a guess.

The version lives in SQLite's built-in `PRAGMA user_version`: a per-database
integer in the file header — no extra table, no magic string, and it does not
touch event payloads or RunManifests (so 6.1 fingerprints are unchanged).
"""
from __future__ import annotations

import sqlite3

# The on-disk format version this build writes and understands.
# v2: the tasks table gained `agent` + `parent_run_id` (multi-agent, 6.6).
# v3: the tasks table gained `user` (user identity, 7.1).
SCHEMA_VERSION = 3


def read_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def stamp_version(conn: sqlite3.Connection, version: int = SCHEMA_VERSION) -> None:
    conn.execute(f"PRAGMA user_version = {version}")
