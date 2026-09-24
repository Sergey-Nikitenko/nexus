"""Conformance: a persisted knowledge store is observationally equivalent to an
in-memory store at the contract boundary — across a restart.

ingest (process 1) -> persist -> restart -> retrieve (process 2) -> RetrievalResult

Not identical internals. Not identical ranking. Just the required fields intact:
contract shape, provenance, identity, version, metadata, filtering, and
current-version semantics.

Run:  py tests/conformance/test_persistence_contract.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import RetrievedChunk, RetrievalResult  # noqa: E402
from knowledge.inmemory import ComposedRetriever  # noqa: E402
from knowledge.persistent import SqliteKnowledgeStore  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def main() -> None:
    print("Conformance: persistence is observationally equivalent across a restart")
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        # process 1: ingest + persist
        r1 = ComposedRetriever(store=SqliteKnowledgeStore(path))
        r1.ingest("filesystem", "docs/auth.md", "v1",
                  "auth verifies tokens with a secret", metadata={"team": "platform"})
        r1.ingest("filesystem", "docs/auth.md", "v2",
                  "auth verifies and refreshes tokens with a secret", metadata={"team": "platform"})
        r1.ingest("filesystem", "docs/billing.md", "v1",
                  "billing calculates invoices", metadata={"team": "finance"})
        r1.store.close()

        # process 2: restart — a fresh connection to the same file
        r2 = ComposedRetriever(store=SqliteKnowledgeStore(path))

        r = r2.search("tokens", k=5)
        check(isinstance(r, RetrievalResult), "after restart: result is a RetrievalResult")
        check(r.chunks and all(isinstance(c, RetrievedChunk) for c in r.chunks),
              "after restart: chunks are RetrievedChunk contracts")
        check(all(c.source and c.document and c.location and c.version for c in r.chunks),
              "after restart: provenance + identity + version survive")
        auth_versions = {c.version for c in r.chunks if c.document == "docs/auth.md"}
        check(auth_versions == {"v2"}, "after restart: current-version semantics survive (only v2)")

        r3 = r2.search("invoices", filters={"team": "finance"}, k=5)
        check(r3.chunks and all(c.metadata.get("team") == "finance" for c in r3.chunks),
              "after restart: filters are honored")

        r2.store.close()
    finally:
        if os.path.exists(path):
            os.unlink(path)

    print("\nPASS: persistence is observationally equivalent to in-memory across a restart.")


if __name__ == "__main__":
    main()
