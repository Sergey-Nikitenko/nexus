"""A second, deliberately different Retriever implementation.

Keyword-overlap ranking (no embeddings, naive single-chunk splitting) — the point
is that callers never notice the difference. It satisfies the same Retriever
interface and returns the same RetrievalResult shape. Used by the conformance
test to prove "knowledge can be replaced without changing the orchestrator."
"""
from __future__ import annotations

import re

from core.contracts import RetrievedChunk, RetrievalResult, new_id
from .ingestion import Chunk

_WORD = re.compile(r"[a-z0-9]+")


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


class KeywordRetriever:
    def __init__(self) -> None:
        self._chunks: list[Chunk] = []

    def ingest(self, source, document, version, text, metadata=None) -> int:
        self._chunks.append(Chunk(
            id=new_id("chunk"), text=text, source=source, document=document,
            location="chunk 0", version=version, metadata=metadata or {},
        ))
        return 1

    def search(self, query, filters=None, k=5) -> RetrievalResult:
        qw = _words(query)
        scored: list[tuple[Chunk, float]] = []
        for c in self._chunks:
            if filters and not all(c.metadata.get(key) == val for key, val in filters.items()):
                continue
            overlap = len(qw & _words(c.text)) / (len(qw) or 1)
            if overlap > 0:
                scored.append((c, overlap))
        scored.sort(key=lambda x: x[1], reverse=True)
        scored = scored[:k]
        chunks = [
            RetrievedChunk(text=c.text, source=c.source, document=c.document,
                           location=c.location, version=c.version,
                           relevance=round(s, 4), metadata=dict(c.metadata))
            for c, s in scored
        ]
        return RetrievalResult(query=query, chunks=chunks)
