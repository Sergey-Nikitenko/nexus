"""Phase 2 golden task — hybrid retrieval: fuse sources behind one contract.

The orchestrator calls `retriever.search(query, filters)` and gets a
RetrievalResult — one contract, whether one source or many produced it. A hybrid
fans the query out to its candidate sources, merges by chunk identity, and
reranks. The reranker is an INTERNAL stage: it never appears in the contract.

Run:  py tests/golden/test_phase2_hybrid.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import RetrievedChunk, RetrievalResult  # noqa: E402
from knowledge.hybrid import HybridRetriever  # noqa: E402
from knowledge.inmemory import ComposedRetriever  # noqa: E402
from knowledge.keyword import KeywordRetriever  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def main():
    print("Phase 2 golden task: hybrid retrieval (vector + keyword, fused + reranked)")
    vector = ComposedRetriever()
    keyword = KeywordRetriever()
    hybrid = HybridRetriever([vector, keyword])

    corpus = [
        ("docs/auth.md", "auth verifies tokens with a secret", "platform"),
        ("docs/billing.md", "billing calculates invoices and emails receipts", "finance"),
        ("docs/security.md", "security encrypts with keys and tokens", "platform"),
    ]
    for doc, text, team in corpus:
        hybrid.ingest("filesystem", doc, "v1", text, metadata={"team": team})

    r = hybrid.search("tokens", k=10)
    check(isinstance(r, RetrievalResult), "hybrid returns a RetrievalResult (one contract)")
    check(r.chunks and all(isinstance(c, RetrievedChunk) for c in r.chunks),
          "every chunk is a RetrievedChunk (no provider object escapes)")
    auth = [c for c in r.chunks if c.document == "docs/auth.md"]
    check(len(auth) == 1, "a chunk found by both sources surfaces once (fused, not duplicated)")

    r2 = hybrid.search("invoices", filters={"team": "finance"}, k=10)
    check(r2.chunks and all(c.metadata.get("team") == "finance" for c in r2.chunks),
          "metadata filter survives fusion")

    # the reranker is an internal, swappable stage — the contract never exposes it
    class ReverseReranker:
        def rerank(self, candidates):
            return list(reversed(candidates))

    hybrid_rev = HybridRetriever([vector, keyword], reranker=ReverseReranker())
    default = [c.document for c in hybrid.search("tokens", k=10).chunks]
    reversed_ = [c.document for c in hybrid_rev.search("tokens", k=10).chunks]
    check(set(default) == set(reversed_) and default != reversed_,
          "the reranker is swappable: same candidates, different order")
    check(isinstance(hybrid_rev.search("tokens", k=10), RetrievalResult),
          "swapping the reranker does not change the contract")

    print("\nPASS: Phase 2 hybrid retrieval holds.")


if __name__ == "__main__":
    main()
