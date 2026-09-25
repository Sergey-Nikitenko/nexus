"""SQLite connection policy — Nexus's deliberate concurrency contract (AD-027).

Nexus permits concurrent workers. Each worker runs on its own thread and gets its
OWN SQLite connection; no connection is ever shared across threads. The lifecycle
of those connections belongs to the STORE component (queue / approvals / event
bus), not the worker thread: a store opens one connection per thread on first use
and closes them all on `close()`. The worker never touches a connection — it only
calls the store's methods.

SQLite behavior is deliberate, not incidental. `sqlite_connect` configures:

- ``busy_timeout`` — a writer that cannot immediately acquire the write lock
  WAITS (bounded) instead of failing with "database is locked".
- ``journal_mode=WAL`` — one writer + many readers: a worker's write does not
  block another worker's read.
- ``synchronous=NORMAL`` — the WAL-mode durability sweet spot (crash-safe without
  a full fsync per transaction).
- ``foreign_keys=ON`` — referential integrity is enforced, not assumed.
- ``check_same_thread=False`` — set ONLY so the store's `close()` can close
  connections from the owning (non-worker) thread at teardown. It does NOT
  reintroduce the original bug: connections are thread-local (one per thread), so
  no connection is ever written by two threads. Sharing is prevented by
  construction, not by the flag.

**Atomicity comes from single conditional statements, never from Python timing.**
Every exclusive transition (claim, consume, recover) is ONE ``UPDATE ... WHERE
<current-status>`` (or ``UPDATE ... RETURNING``) executed as its own transaction.
The database arbitrates the race: exactly one caller's condition matches; the
loser gets an explicit, inspectable result (``None``), never an exception or a
silent overwrite. No transition does a Python-side SELECT, checks the result, then
issues an unprotected UPDATE.

**What SQLite guarantees here:** atomic single statements, serialized writers, and
(under WAL) non-blocking readers. **What it does NOT guarantee:** that two threads
sharing ONE connection are safe (hence one-connection-per-thread), or that a
multi-statement Python sequence is atomic (hence one-statement transitions).

**Provider neutrality.** This module is the REFERENCE persistence implementation.
What the rest of Nexus depends on is each store's *method surface* — atomic
transition, transaction boundary, durable result — not SQLite. A PostgreSQL
adapter later provides the same methods behind the same call sites; the execution
model (queue, worker, orchestrator, approvals) does not change.
"""
from __future__ import annotations

import sqlite3
import threading

from execution.schema import SCHEMA_VERSION, read_version, stamp_version


def sqlite_connect(path: str) -> sqlite3.Connection:
    """Open ONE connection with Nexus's deliberate SQLite policy.

    `check_same_thread=False` exists only so the store can close connections it
    opened in (now-exited) worker threads; each thread still gets its OWN
    connection (see `SqliteStore._conn`), so none is ever shared."""
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


class SqliteStore:
    """One SQLite connection per thread, owned by the store (AD-027).

    Subclasses implement ``_schema(conn)`` (an idempotent CREATE TABLE IF NOT
    EXISTS) and use ``self._conn()`` to obtain the current thread's connection.
    ``close()`` closes every connection the store ever opened, whichever thread
    opened it.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self._local = threading.local()
        self._connections: list[sqlite3.Connection] = []
        self._lock = threading.Lock()
        self._closed = False
        conn = self._conn()
        version = read_version(conn)
        if version > SCHEMA_VERSION:
            # data written by a newer build — never silently misread (schema.py).
            raise RuntimeError(
                f"{type(self).__name__}: on-disk schema version {version} is newer "
                f"than this build supports (up to {SCHEMA_VERSION}); refusing to read")
        self._schema(conn)  # CREATE TABLE IF NOT EXISTS (current shape)
        if version < SCHEMA_VERSION:
            self._migrate(conn, version)  # reconcile an older on-disk schema
        stamp_version(conn, SCHEMA_VERSION)
        conn.commit()

    def _schema(self, conn: sqlite3.Connection) -> None:
        raise NotImplementedError

    def _migrate(self, conn: sqlite3.Connection, from_version: int) -> None:
        """Reconcile an older on-disk schema to the current one. Default: none."""
        pass

    def _conn(self) -> sqlite3.Connection:
        if self._closed:
            raise RuntimeError(f"{type(self).__name__} is closed")
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite_connect(self.path)
            self._local.conn = conn
            with self._lock:
                self._connections.append(conn)
        return conn

    def connection_count(self) -> int:
        """Number of open connections (one per thread that has used this store).

        Exposed so a conformance test can assert the one-connection-per-thread
        invariant mechanically: after N threads touch a store, the count is N
        (plus the schema connection opened by the constructor)."""
        with self._lock:
            return len(self._connections)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with self._lock:
            conns, self._connections = self._connections, []
        for conn in conns:
            conn.close()
        self._local.conn = None
