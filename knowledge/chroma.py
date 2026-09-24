"""Chroma — implementation #3: the production adapter, anticlimactic by design.

Satisfies the KnowledgeStore protocol (add / search / versions_of / current)
using chromadb underneath. The orchestrator never knows: ComposedRetriever
composes it behind the same contract as the stdlib reference and the SQLite
store — swap the store, nothing else moves.

Two collections per client:

- `chunks` — audit: every version ever added (idempotent by stable id).
- `live`   — only the CURRENT version of each identity; search reads this.

Current-version semantics are enforced HERE (AD-001), not by Chroma: Chroma has
no notion of "current", so the adapter layers it on with the `live` collection.
This is exactly what makes Chroma an implementation, not an architectural event.
"""
from __future__ import annotations

import json

import chromadb

from .ingestion import Chunk

_RESERVED = {"source", "document", "location", "version", "_meta"}


def _where(**pairs) -> dict | None:
    """Build a chromadb `where` clause. Chroma 1.x requires explicit operators
    (no implicit multi-key AND), so equality is `{"k": {"$eq": v}}` joined by
    `$and`."""
    if not pairs:
        return None
    clauses = [{key: {"$eq": value}} for key, value in pairs.items()]
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


def _identity_where(chunk: Chunk) -> dict:
    return _where(source=chunk.source, document=chunk.document, location=chunk.location)


def _metadata(chunk: Chunk) -> dict:
    """Flatten chunk metadata into Chroma-typed fields, keeping a faithful
    `_meta` JSON for round-tripping arbitrary (non-primitive) values."""
    meta = {
        "source": chunk.source,
        "document": chunk.document,
        "location": chunk.location,
        "version": chunk.version,
        "_meta": json.dumps(chunk.metadata),
    }
    for key, value in chunk.metadata.items():
        if key not in _RESERVED and isinstance(value, (str, int, float, bool)):
            meta[key] = value
    return meta


def _chunk_from(id_: str, text: str, meta: dict) -> Chunk:
    return Chunk(
        id=id_, text=text, source=meta["source"], document=meta["document"],
        location=meta["location"], version=meta["version"],
        metadata=json.loads(meta.get("_meta", "{}")),
    )


class ChromaKnowledgeStore:
    """A KnowledgeStore backed by chromadb (persistent when `path` is given,
    ephemeral otherwise). Requires a dense embedder (list[float]) — pair it with
    HashingEmbedder."""

    def __init__(self, path: str | None = None) -> None:
        self._client = chromadb.PersistentClient(path=path) if path else chromadb.Client()
        self._chunks = self._client.get_or_create_collection(
            "chunks", metadata={"hnsw:space": "cosine"})
        self._live = self._client.get_or_create_collection(
            "live", metadata={"hnsw:space": "cosine"})

    def add(self, chunk: Chunk, embedding) -> None:
        emb = list(embedding)
        meta = _metadata(chunk)

        # audit trail: every version, idempotent by stable id (upsert)
        self._chunks.upsert(ids=[chunk.id], documents=[chunk.text],
                            metadatas=[meta], embeddings=[emb])

        # current-version (AD-001): drop the prior current chunk for this identity
        prev = self._live.get(where=_identity_where(chunk))["ids"]
        if prev:
            self._live.delete(ids=prev)
        self._live.upsert(ids=[chunk.id], documents=[chunk.text],
                          metadatas=[meta], embeddings=[emb])

    def search(self, embedding, k, filters=None):
        emb = list(embedding)
        where = None
        if filters and all(isinstance(v, (str, int, float, bool)) for v in filters.values()):
            where = _where(**filters)  # primitive filters -> native Chroma `where`

        res = self._live.query(
            query_embeddings=[emb], n_results=k, where=where,
            include=["documents", "metadatas", "distances"],
        )
        ids = res["ids"][0]
        docs = res["documents"][0]
        metas = res["metadatas"][0]
        dists = res["distances"][0]

        out = []
        for i, id_ in enumerate(ids):
            if filters and where is None:
                # non-primitive filters fall back to a Python post-filter
                full = json.loads(metas[i].get("_meta", "{}"))
                if not all(full.get(key) == val for key, val in filters.items()):
                    continue
            # cosine space: distance = 1 - similarity, so flip it back
            out.append((_chunk_from(id_, docs[i], metas[i]), round(1.0 - dists[i], 4)))
        return out

    def versions_of(self, identity):
        source, document, location = identity
        res = self._chunks.get(
            where=_where(source=source, document=document, location=location),
            include=["metadatas"],
        )
        return sorted({m["version"] for m in res["metadatas"]})

    def current(self, identity):
        source, document, location = identity
        res = self._live.get(
            where=_where(source=source, document=document, location=location),
            include=["metadatas"],
        )
        return res["metadatas"][0]["version"] if res["ids"] else None

    def close(self) -> None:
        # chromadb has no explicit close(); dropping the reference releases it.
        self._client = None
