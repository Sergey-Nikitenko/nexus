"""Phase 2 golden tasks — retrieval through the Retriever contract.

Proves the six properties, not just "we got results":
  provenance survives · metadata survives · filters honored ·
  version preserved · k honored · result is a contract (no provider escape).

Plus the identity semantics: same (source, document, location) + different
version = the same knowledge at different points in time.

Run:  py tests/golden/test_phase2_retrieval.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import RetrievedChunk, RetrievalResult  # noqa: E402
from knowledge.inmemory import ComposedRetriever  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def main():
    print("Phase 2 golden tasks: retrieval through the Retriever contract")
    ret = ComposedRetriever()
    ret.ingest("filesystem", "docs/auth.md", "v1",
               "the auth middleware verifies tokens; tokens are signed with a secret.",
               metadata={"team": "platform"})
    ret.ingest("filesystem", "docs/billing.md", "v1",
               "billing calculates invoices monthly and emails receipts.",
               metadata={"team": "finance"})

    r = ret.search("how are tokens verified", k=3)
    check(isinstance(r, RetrievalResult), "result is a RetrievalResult (no provider object escapes)")
    check(len(r.chunks) >= 1 and len(r.chunks) <= 3, "k is honored")
    c = r.chunks[0]
    check(isinstance(c, RetrievedChunk), "chunk is a RetrievedChunk (a contract)")
    check(c.source == "filesystem" and c.document == "docs/auth.md", "provenance survives (source + document)")
    check(c.version == "v1", "version is preserved")
    check(bool(c.location), "location is preserved")
    check(c.metadata == {"team": "platform"}, "metadata survives")

    # filters are honored
    r2 = ret.search("invoices", filters={"team": "finance"}, k=5)
    check(r2.chunks and all(ch.metadata.get("team") == "finance" for ch in r2.chunks),
          "filters are honored")

    # identity semantics: same location, new version -> same identity, two versions
    ret.ingest("filesystem", "docs/auth.md", "v2",
               "the auth middleware verifies and refreshes tokens with a secret.",
               metadata={"team": "platform"})
    identity = ("filesystem", "docs/auth.md", "chunk 0")
    check(ret.store.current(identity) == "v2", "current version is v2")
    check(set(ret.store.versions_of(identity)) == {"v1", "v2"},
          "same identity + different version = same knowledge, updated")
    # update semantics: retrieval surfaces ONLY the current version of a given
    # identity (stale v1 excluded) — other documents (billing, still v1) are unaffected
    r3 = ret.search("tokens", k=10)
    auth_versions = {ch.version for ch in r3.chunks if ch.document == "docs/auth.md"}
    check("v1" not in auth_versions and "v2" in auth_versions,
          "retrieval surfaces only the current version of auth (no stale v1 + v2 contradiction)")

    print("\nPASS: Phase 2 retrieval holds.")


if __name__ == "__main__":
    main()
