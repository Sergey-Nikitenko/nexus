"""Conformance: the Retriever boundary holds across implementations.

The same contract, the same RetrievalResult semantics, no matter the provider.
Assertions are boundary properties ONLY — not ranking, because two legitimate
implementations may rank differently.

Run against every Retriever implementation; adding a new one is one line here.

Run:  py tests/conformance/test_retriever_contract.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import RetrievedChunk, RetrievalResult  # noqa: E402
from knowledge.chroma import ChromaKnowledgeStore  # noqa: E402
from knowledge.hybrid import HybridRetriever  # noqa: E402
from knowledge.inmemory import ComposedRetriever, HashingEmbedder  # noqa: E402
from knowledge.keyword import KeywordRetriever  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def assert_retriever_contract(impl, name: str) -> None:
    """The boundary every Retriever must satisfy, independent of ranking."""
    impl.ingest("filesystem", "docs/auth.md", "v1",
                "auth verifies tokens with a secret", metadata={"team": "platform"})
    impl.ingest("filesystem", "docs/billing.md", "v1",
                "billing calculates invoices and emails receipts", metadata={"team": "finance"})

    r = impl.search("tokens", k=5)
    check(isinstance(r, RetrievalResult), f"{name}: result is a RetrievalResult (the contract)")
    check(r.chunks and all(isinstance(c, RetrievedChunk) for c in r.chunks),
          f"{name}: every chunk is a RetrievedChunk (no provider object escapes)")
    check(all(c.source and c.document and c.location and c.version for c in r.chunks),
          f"{name}: provenance + identity + version survive")

    r2 = impl.search("invoices", filters={"team": "finance"}, k=5)
    check(r2.chunks and all(c.metadata.get("team") == "finance" for c in r2.chunks),
          f"{name}: filters are honored")


def main() -> None:
    print("Conformance: Retriever boundary holds across implementations")
    assert_retriever_contract(ComposedRetriever(), "InMemory")
    assert_retriever_contract(KeywordRetriever(), "Keyword")
    assert_retriever_contract(HybridRetriever([ComposedRetriever(), KeywordRetriever()]), "Hybrid")
    # Chroma is implementation #3: same contract, dense embedder, ephemeral client.
    assert_retriever_contract(
        ComposedRetriever(store=ChromaKnowledgeStore(), embedder=HashingEmbedder()),
        "Chroma",
    )
    print("\nPASS: the Retriever contract is implementation-agnostic (replaceable).")


if __name__ == "__main__":
    main()
