"""Reference (stdlib-only) implementation of the retrieval capability.

This exists to prove the contracts end-to-end, not to be the production engine.
Bag-of-words embedding + cosine similarity over an in-memory dict — no provider
libraries, so the conformance gates stay green.
"""
from __future__ import annotations

import math
import re
from collections import Counter

from core.contracts import RetrievedChunk, RetrievalResult, new_id
from .ingestion import Chunk

_WORD = re.compile(r"[a-z0-9]+")


def chunk_text(text: str, size: int = 80, overlap: int = 8) -> list[str]:
    words = text.split()
    out: list[str] = []
    start = 0
    while start < len(words):
        out.append(" ".join(words[start:start + size]))
        start += size - overlap
    return out


def embed(text: str) -> dict[str, float]:
    counts = Counter(_WORD.findall(text.lower()))
    total = sum(counts.values()) or 1
    return {w: c / total for w, c in counts.items()}


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    common = a.keys() & b.keys()
    dot = sum(a[w] * b[w] for w in common)
    na = math.sqrt(sum(v * v for v in a.values())) or 1.0
    nb = math.sqrt(sum(v * v for v in b.values())) or 1.0
    return dot / (na * nb)


class InMemoryVectorStore:
    def __init__(self) -> None:
        self._chunks: dict[str, Chunk] = {}
        self._vectors: dict[str, dict[str, float]] = {}

    def add(self, chunk: Chunk) -> None:
        self._chunks[chunk.id] = chunk
        self._vectors[chunk.id] = embed(chunk.text)

    def search(self, qvec: dict[str, float], k: int, filters: dict | None = None) -> list[tuple[Chunk, float]]:
        scored: list[tuple[Chunk, float]] = []
        for cid, vec in self._vectors.items():
            chunk = self._chunks[cid]
            if filters and not all(chunk.metadata.get(key) == val for key, val in filters.items()):
                continue
            scored.append((chunk, cosine(qvec, vec)))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:k]

    def versions_of(self, identity: tuple[str, str, str]) -> list[str]:
        return sorted({c.version for c in self._chunks.values()
                       if (c.source, c.document, c.location) == identity})


class InMemoryRetriever:
    """A Retriever (satisfies the `Retriever` protocol) over the stdlib store."""

    def __init__(self, store: InMemoryVectorStore | None = None) -> None:
        self.store = store or InMemoryVectorStore()

    def ingest(self, source: str, document: str, version: str, text: str,
               metadata: dict | None = None, size: int = 80) -> int:
        """ingest -> parse -> chunk -> metadata -> embed -> store (composed)."""
        n = 0
        for i, piece in enumerate(chunk_text(text, size=size)):
            self.store.add(Chunk(
                id=new_id("chunk"), text=piece, source=source, document=document,
                location=f"chunk {i}", version=version, metadata=metadata or {},
            ))
            n += 1
        return n

    def search(self, query: str, filters: dict | None = None, k: int = 5) -> RetrievalResult:
        scored = self.store.search(embed(query), k, filters)
        chunks = [
            RetrievedChunk(text=c.text, source=c.source, document=c.document,
                           location=c.location, version=c.version,
                           relevance=round(s, 4), metadata=dict(c.metadata))
            for c, s in scored
        ]
        return RetrievalResult(query=query, chunks=chunks)
