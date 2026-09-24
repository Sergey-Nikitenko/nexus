"""Knowledge stage contracts — each ingestion stage is independently replaceable.

    Source -> Loader -> Document -> Parser -> Chunker -> Chunk -> Embedder -> Vector -> Store

No stage knows the concrete provider (GitHub, HTML, PDF, Chroma, OpenAI, Ollama).
The reference implementation (knowledge/inmemory.py) is one boring way to satisfy
these; a production adapter is just another.
"""
from __future__ import annotations

from typing import Protocol

from .ingestion import Chunk, Document

# An embedding is an opaque vector. The reference uses bag-of-words
# (dict[str, float]); a production adapter could use list[float] from
# OpenAI/Ollama — the contracts don't care.
Embedding = dict[str, float]


class DocumentLoader(Protocol):
    """Source -> Document (reads a raw source: path, URL, repo ref)."""
    def load(self, source: str) -> Document: ...


class Parser(Protocol):
    """Document -> Document (extracts text from raw content)."""
    def parse(self, doc: Document) -> Document: ...


class Chunker(Protocol):
    """Document -> list[Chunk] (splits text into chunks with locations)."""
    def chunk(self, doc: Document) -> list[Chunk]: ...


class Embedder(Protocol):
    """text -> Embedding (the pipeline never knows whose SDK made the vector)."""
    def embed(self, text: str) -> Embedding: ...


class KnowledgeStore(Protocol):
    """Stores chunks + embeddings; retrieves the CURRENT version, never stale ones."""
    def add(self, chunk: Chunk, embedding: Embedding) -> None: ...

    def search(self, embedding: Embedding, k: int,
               filters: dict | None = None) -> list[tuple[Chunk, float]]: ...

    def versions_of(self, identity: tuple[str, str, str]) -> list[str]: ...

    def current(self, identity: tuple[str, str, str]) -> str | None: ...
