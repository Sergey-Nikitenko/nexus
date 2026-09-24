"""Knowledge ingestion contracts — Document and Chunk, with identity semantics.

A chunk's IDENTITY is (source, document, location). Two chunks with the same
identity but a different version are the same knowledge at different points in
time — the key to stale-embedding detection, updates, and reproducible runs.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Document:
    """A parsed document. Identity = (source, document); version distinguishes
    the same document at different points in time."""
    source: str       # github | web | filesystem
    document: str     # repo/path | canonical URL | relative path
    version: str      # commit SHA | content hash | crawl timestamp
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Chunk:
    """One chunk, carrying its full provenance (denormalized) so it is
    self-describing without a join back to the store."""
    id: str
    text: str
    source: str
    document: str
    location: str     # line range | heading/section | page
    version: str
    metadata: dict[str, Any] = field(default_factory=dict)


def chunk_identity(c: Chunk) -> tuple[str, str, str]:
    """The stable identity of a chunk: (source, document, location).
    Same identity + different version = same knowledge, updated."""
    return (c.source, c.document, c.location)


def stable_chunk_id(source: str, document: str, location: str, version: str) -> str:
    """Deterministic chunk id from (identity, version). Re-ingesting the same
    chunk produces the same id, so `add` replaces instead of duplicating
    (idempotency: ingest(x) x3 == one logical chunk)."""
    key = f"{source}\x00{document}\x00{location}\x00{version}".encode()
    return f"chunk_{hashlib.sha1(key).hexdigest()[:16]}"
