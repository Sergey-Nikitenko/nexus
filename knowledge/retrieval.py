"""Knowledge contracts — the shapes the rest of Nexus consumes.

An implementation hides its provider (vector DB, BM25, reranker) behind this
interface. Callers get a RetrievalResult with full provenance — never a Chroma
collection, a distance matrix, or any provider-specific object.
"""
from __future__ import annotations

from typing import Protocol

from core.contracts import RetrievalResult


class Retriever(Protocol):
    """The retrieval capability. `search` is the ONLY thing callers see."""

    def search(self, query: str, filters: dict | None = None, k: int = 5) -> RetrievalResult:
        ...
