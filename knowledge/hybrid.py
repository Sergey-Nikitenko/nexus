"""Hybrid retrieval: fuse N candidate sources behind the one Retriever contract.

The orchestrator calls `retriever.search(query, filters)` and gets a
RetrievalResult — it never knows (and must not need to know) whether one source
or many produced it. A hybrid retriever fans the query out to its candidate
sources (vector + keyword + ...), merges their results by chunk identity, and
reranks the union.

The reranker is an INTERNAL stage of this implementation. It is deliberately NOT
part of the Retriever contract (which stays `search(query, filters) ->
RetrievalResult`): callers never touch it, and swapping it changes only ranking,
never the boundary. When a real semantic source (Chroma + embeddings) lands, it
drops in as one more entry in `sources` — nothing else moves.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from core.contracts import RetrievedChunk, RetrievalResult


class Reranker(Protocol):
    """Internal stage (not a public contract): candidates -> ranked candidates."""
    def rerank(self, candidates: list["HybridCandidate"]) -> list["HybridCandidate"]: ...


@dataclass
class HybridCandidate:
    """One merged chunk plus the ranks it earned from each candidate source."""
    chunk: RetrievedChunk
    ranks: list[int] = field(default_factory=list)


def _identity(chunk: RetrievedChunk) -> tuple:
    """Chunk identity across sources: (source, document, location, version)."""
    return (chunk.source, chunk.document, chunk.location, chunk.version)


class ReciprocalRankFusion:
    """Rank-based fusion: score = sum over sources of 1/(rank + k).

    Robust to sources whose scores live on incomparable scales (a cosine
    similarity and a keyword overlap are different units). Deterministic and
    cheap — no score normalization, no cross-source assumptions."""

    def __init__(self, k: int = 60):
        self.k = k

    def rerank(self, candidates: list[HybridCandidate]) -> list[HybridCandidate]:
        scored: list[tuple[HybridCandidate, float]] = []
        for c in candidates:
            s = sum(1.0 / (r + self.k) for r in c.ranks) if c.ranks else 0.0
            c.chunk.relevance = round(s, 6)
            scored.append((c, s))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return [c for c, _ in scored]


class HybridRetriever:
    """A Retriever composed of N candidate sources, fused and reranked.

    Same duck-typed contract as every other retriever: ingest + search. The
    orchestrator cannot tell this from a single-source retriever — that is the
    point: hybrid retrieval is an implementation detail, not an interface."""

    def __init__(self, sources, reranker=None):
        self.sources = list(sources)
        self.reranker = reranker or ReciprocalRankFusion()

    def ingest(self, source, document, version, text, metadata=None) -> int:
        n = 0
        for s in self.sources:
            n = max(n, s.ingest(source, document, version, text, metadata))
        return n

    def search(self, query, filters=None, k=5) -> RetrievalResult:
        # 1. fan out to every candidate source
        merged: dict[tuple, HybridCandidate] = {}
        for source in self.sources:
            for rank, chunk in enumerate(source.search(query, filters, k).chunks, start=1):
                # metadata filter is an explicit stage of the hybrid, applied here
                # even if a source forgot to (defense at the boundary).
                if filters and not all(
                    chunk.metadata.get(key) == value for key, value in filters.items()
                ):
                    continue
                key = _identity(chunk)
                candidate = merged.get(key)
                if candidate is None:
                    candidate = HybridCandidate(chunk=chunk)
                    merged[key] = candidate
                candidate.ranks.append(rank)

        # 2. rerank (internal stage — not part of the contract)
        ranked = self.reranker.rerank(list(merged.values()))
        return RetrievalResult(query=query, chunks=[c.chunk for c in ranked[:k]])
