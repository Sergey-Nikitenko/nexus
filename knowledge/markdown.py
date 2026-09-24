"""Real Markdown source adapters — parsing and heading-aware chunking.

These satisfy the same contracts (Loader, Parser, Chunker) as the reference
implementations; they are just *real* instead of placeholder. The orchestrator
is unaware of the difference.
"""
from __future__ import annotations

import hashlib
import re

from .ingestion import Chunk, Document, stable_chunk_id

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)


class FilesystemMarkdownLoader:
    """Loads a .md file -> Document (version = content hash)."""
    def load(self, path: str) -> Document:
        text = open(path, encoding="utf-8").read()
        version = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
        return Document(source="filesystem", document=path, version=version, text=text)


class MarkdownParser:
    """Markdown is already plain text, so parse is a pass-through. A heavier
    parser (front-matter strip, HTML extraction) is a different implementation
    of the same Parser contract."""
    def parse(self, doc: Document) -> Document:
        return doc


class MarkdownChunker:
    """Chunks a Markdown document by heading — each section becomes a chunk whose
    location is the heading. Heading-less text falls back to one chunk."""

    def chunk(self, doc: Document) -> list[Chunk]:
        chunks: list[Chunk] = []
        for i, (heading, body) in enumerate(self._sections(doc.text)):
            text = f"{heading}\n{body}".strip() if heading else body.strip()
            if not text:
                continue
            chunks.append(Chunk(
                id=stable_chunk_id(doc.source, doc.document, heading or f"section {i}", doc.version),
                text=text, source=doc.source,
                document=doc.document, location=heading or f"section {i}",
                version=doc.version, metadata=dict(doc.metadata),
            ))
        return chunks

    @staticmethod
    def _sections(text: str) -> list[tuple[str, str]]:
        positions = [(m.start(), m.group(2).strip()) for m in _HEADING.finditer(text)]
        if not positions:
            return [("", text)]
        out: list[tuple[str, str]] = []
        for i, (start, title) in enumerate(positions):
            end = positions[i + 1][0] if i + 1 < len(positions) else len(text)
            out.append((title, text[start:end]))
        return out
