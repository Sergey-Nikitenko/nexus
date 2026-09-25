"""Reference (stdlib-only) implementation of every knowledge stage.

Exists to prove the contracts, not to be the production engine. A production
adapter (Chroma, OpenAI embeddings, ...) is just another implementation of the
same protocols in knowledge/contracts.py.
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter

from core.contracts import RetrievedChunk, RetrievalResult
from .ingestion import Chunk, Document, stable_chunk_id

_WORD = re.compile(r"[a-z0-9]+")


# ---- reference stage implementations -------------------------------------

class FileSystemLoader:
    def load(self, path: str) -> Document:
        text = open(path, encoding="utf-8").read()
        version = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
        return Document(source="filesystem", document=path, version=version, text=text)


class IdentityParser:
    """Reference parser: input is already plain text, so parse is the identity."""
    def parse(self, doc: Document) -> Document:
        return doc


class WordChunker:
    def __init__(self, size: int = 80, overlap: int = 8):
        self.size = size
        self.overlap = overlap

    def chunk(self, doc: Document) -> list[Chunk]:
        words = doc.text.split()
        chunks: list[Chunk] = []
        start, i = 0, 0
        while start < len(words):
            text = " ".join(words[start:start + self.size])
            chunks.append(Chunk(
                id=stable_chunk_id(doc.source, doc.document, f"chunk {i}", doc.version),
                text=text, source=doc.source,
                document=doc.document, location=f"chunk {i}",
                version=doc.version, metadata=dict(doc.metadata),
            ))
            start += self.size - self.overlap
            i += 1
        return chunks


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


class BagOfWordsEmbedder:
    def embed(self, text: str) -> dict[str, float]:
        return _bow(text)


class HashingEmbedder:
    """Fixed-dimension dense embedder via the hashing trick.

    Stdlib-only and deterministic (md5 — never Python's salted `hash()`, which
    would break across restarts). Produces a unit-length vector of fixed `dim`,
    the shape a dense vector store (Chroma) needs. A real embedding model
    (OpenAI/Ollama) is a later adapter behind the same Embedder contract — the
    store never knows which produced the vector.
    """

    def __init__(self, dim: int = 128):
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for token in _WORD.findall(text.lower()):
            bucket = int.from_bytes(
                hashlib.md5(b"bucket:" + token.encode("utf-8")).digest()[:8], "little"
            ) % self.dim
            sign = 1.0 if int.from_bytes(
                hashlib.md5(b"sign:" + token.encode("utf-8")).digest()[:8], "little"
            ) & 1 else -1.0
            vec[bucket] += sign
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


class InMemoryKnowledgeStore:
    """Stores chunks + embeddings. Retrieval returns only the CURRENT version of
    each chunk identity — stale versions are retained for audit but never
    surfaced, so the model never gets v1 and v2 at once without knowing why."""

    def __init__(self) -> None:
        self._chunks: dict[str, Chunk] = {}
        self._vectors: dict[str, dict[str, float]] = {}
        self._by_identity: dict[tuple, dict[str, list[str]]] = {}
        self._current: dict[tuple, str] = {}

    def add(self, chunk: Chunk, embedding: dict[str, float]) -> None:
        identity = (chunk.source, chunk.document, chunk.location)
        self._chunks[chunk.id] = chunk
        self._vectors[chunk.id] = embedding
        ids = self._by_identity.setdefault(identity, {}).setdefault(chunk.version, [])
        if chunk.id not in ids:
            ids.append(chunk.id)  # idempotent: same (identity, version) -> no duplicate
        self._current[identity] = chunk.version  # last-add wins = current

    def search(self, embedding, k, filters=None):
        scored = []
        for cid, vec in self._vectors.items():
            chunk = self._chunks[cid]
            identity = (chunk.source, chunk.document, chunk.location)
            if self._current.get(identity) != chunk.version:
                continue  # stale chunk — never surfaced
            if filters and not all(chunk.metadata.get(key) == val for key, val in filters.items()):
                continue
            scored.append((chunk, _cosine(embedding, vec)))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:k]

    def versions_of(self, identity):
        return sorted(self._by_identity.get(identity, {}))

    def current(self, identity):
        return self._current.get(identity)

    def snapshot(self) -> str:
        """Deterministic identity of the CURRENT knowledge (identity -> version)."""
        items = sorted(f"{s}|{d}|{l}@{v}" for (s, d, l), v in self._current.items())
        return ";".join(items)


class ComposedRetriever:
    """Composes injectable stages (loader/parser/chunker/embedder/store) into a
    Retriever. The default stages are the stdlib reference; swap the store for
    persistence, the loader/parser/chunker for real files — the caller never
    knows which implementation sits behind each contract."""

    def __init__(self, loader=None, parser=None, chunker=None, embedder=None, store=None):
        self.loader = loader or FileSystemLoader()
        self.parser = parser or IdentityParser()
        self.chunker = chunker or WordChunker()
        self.embedder = embedder or BagOfWordsEmbedder()
        self.store = store or InMemoryKnowledgeStore()

    def ingest_from_document(self, doc: Document) -> int:
        doc = self.parser.parse(doc)
        n = 0
        for chunk in self.chunker.chunk(doc):
            self.store.add(chunk, self.embedder.embed(chunk.text))
            n += 1
        return n

    def ingest(self, source, document, version, text, metadata=None) -> int:
        return self.ingest_from_document(Document(
            source=source, document=document, version=version,
            text=text, metadata=metadata or {},
        ))

    def ingest_path(self, path: str) -> int:
        return self.ingest_from_document(self.loader.load(path))

    def search(self, query, filters=None, k=5) -> RetrievalResult:
        scored = self.store.search(self.embedder.embed(query), k, filters)
        chunks = [
            RetrievedChunk(text=c.text, source=c.source, document=c.document,
                           location=c.location, version=c.version,
                           relevance=round(s, 4), metadata=dict(c.metadata))
            for c, s in scored
        ]
        return RetrievalResult(query=query, chunks=chunks)

    def snapshot(self) -> str:
        """Deterministic identity of the current knowledge snapshot."""
        return self.store.snapshot()
