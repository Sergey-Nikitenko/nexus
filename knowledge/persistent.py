"""SQLite-backed persistence — the first store that survives a restart.

Stdlib-only (sqlite3). Proves the persistence contract: ingest in one process,
retrieve in another, with the same RetrievalResult semantics. Chroma is the
production adapter that follows and must pass the same conformance test.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
from collections import Counter

from .ingestion import Chunk

_WORD = re.compile(r"[a-z0-9]+")


def _bow(text: str) -> dict[str, float]:
    counts = Counter(_WORD.findall(text.lower()))
    total = sum(counts.values()) or 1
    return {w: c / total for w, c in counts.items()}


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    common = a.keys() & b.keys()
    dot = sum(a[w] * b[w] for w in common)
    na = math.sqrt(sum(v * v for v in a.values())) or 1.0
    nb = math.sqrt(sum(v * v for v in b.values())) or 1.0
    return dot / (na * nb)


class SqliteKnowledgeStore:
    """A KnowledgeStore whose state lives in a SQLite file, so it survives a
    process restart. Same contract as InMemoryKnowledgeStore."""

    def __init__(self, path: str) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS chunks "
            "(id TEXT PRIMARY KEY, source TEXT, document TEXT, location TEXT, "
            "version TEXT, text TEXT, metadata TEXT)")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS vectors (chunk_id TEXT PRIMARY KEY, vector TEXT)")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS current_version (identity TEXT PRIMARY KEY, version TEXT)")

    def add(self, chunk: Chunk, embedding: dict[str, float]) -> None:
        identity = json.dumps([chunk.source, chunk.document, chunk.location])
        self._conn.execute(
            "INSERT OR REPLACE INTO chunks VALUES (?,?,?,?,?,?,?)",
            (chunk.id, chunk.source, chunk.document, chunk.location,
             chunk.version, chunk.text, json.dumps(chunk.metadata)),
        )
        self._conn.execute("INSERT OR REPLACE INTO vectors VALUES (?,?)",
                           (chunk.id, json.dumps(embedding)))
        self._conn.execute("INSERT OR REPLACE INTO current_version VALUES (?,?)",
                           (identity, chunk.version))
        self._conn.commit()

    def _current(self) -> dict:
        return {tuple(json.loads(i)): v
                for i, v in self._conn.execute("SELECT identity, version FROM current_version")}

    def search(self, embedding, k, filters=None):
        current = self._current()
        scored = []
        for cid, source, document, location, version, text, metadata in \
                self._conn.execute("SELECT * FROM chunks"):
            if current.get((source, document, location)) != version:
                continue  # stale version
            meta = json.loads(metadata)
            if filters and not all(meta.get(key) == val for key, val in filters.items()):
                continue
            vec = json.loads(self._conn.execute(
                "SELECT vector FROM vectors WHERE chunk_id=?", (cid,)).fetchone()[0])
            chunk = Chunk(id=cid, text=text, source=source, document=document,
                          location=location, version=version, metadata=meta)
            scored.append((chunk, _cosine(embedding, vec)))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:k]

    def versions_of(self, identity):
        return sorted({version for _cid, source, document, _loc, version, _text, _meta
                       in self._conn.execute("SELECT * FROM chunks")
                       if (source, document, _loc) == identity})

    def current(self, identity):
        row = self._conn.execute(
            "SELECT version FROM current_version WHERE identity=?",
            (json.dumps(list(identity)),)).fetchone()
        return row[0] if row else None

    def close(self) -> None:
        self._conn.close()
