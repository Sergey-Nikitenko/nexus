"""Phase 2 golden tasks — real source ingestion (filesystem/Markdown).

Loads a real .md file, parses + chunks it by heading, and proves the provenance
(location = heading, version = content hash) crosses the boundary into
RetrievedChunk — all through the same frozen pipeline the reference uses.

Run:  py tests/golden/test_phase2_markdown.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from knowledge.inmemory import BagOfWordsEmbedder, ComposedRetriever, InMemoryKnowledgeStore  # noqa: E402
from knowledge.markdown import FilesystemMarkdownLoader, MarkdownChunker, MarkdownParser  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def main():
    print("Phase 2 golden tasks: filesystem/Markdown source ingestion")
    md = "# Auth\nTokens are signed with a secret.\n\n# Billing\nInvoices are calculated monthly.\n"
    path = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write(md)
            path = f.name

        ret = ComposedRetriever(
            loader=FilesystemMarkdownLoader(),
            parser=MarkdownParser(),
            chunker=MarkdownChunker(),
            embedder=BagOfWordsEmbedder(),
            store=InMemoryKnowledgeStore(),
        )
        n = ret.ingest_path(path)
        check(n >= 2, "markdown chunks by heading (>= 2 sections)")

        # provenance: locations are the headings
        locs = {c.location for c in ret.store._chunks.values()}
        check({"Auth", "Billing"} <= locs, "chunk location = the markdown heading")

        # version = content hash, preserved through to RetrievedChunk
        r = ret.search("tokens", k=5)
        auth = [c for c in r.chunks if c.location == "Auth"]
        check(bool(auth) and all(c.version for c in auth),
              "version (content hash) survives into RetrievedChunk")

        r2 = ret.search("invoices", k=5)
        check(any(c.location == "Billing" for c in r2.chunks),
              "retrieval finds the right section by content")
    finally:
        if path and os.path.exists(path):
            os.unlink(path)

    print("\nPASS: Phase 2 markdown ingestion holds.")


if __name__ == "__main__":
    main()
