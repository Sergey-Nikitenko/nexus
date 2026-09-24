"""Phase 2 golden task — idempotent ingestion.

ingest(x); ingest(x); ingest(x) must be ONE logical chunk, never three duplicate
retrieval results. Idempotency is the property that makes a retrying backend safe.

Run:  py tests/golden/test_phase2_idempotency.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from knowledge.inmemory import ComposedRetriever  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def main():
    print("Phase 2 golden task: idempotent ingestion")
    ret = ComposedRetriever()
    text = "auth verifies tokens with a secret"
    for _ in range(3):
        ret.ingest("filesystem", "docs/auth.md", "v1", text, metadata={"team": "platform"})

    r = ret.search("tokens", k=10)
    auth = [c for c in r.chunks if c.document == "docs/auth.md"]
    check(len(auth) == 1, "ingest x3 -> exactly one logical chunk (no duplicates)")

    # idempotency must not disturb current-version semantics: re-ingest a NEW version once
    ret.ingest("filesystem", "docs/auth.md", "v2", "auth verifies and refreshes tokens")
    ret.ingest("filesystem", "docs/auth.md", "v2", "auth verifies and refreshes tokens")
    r2 = ret.search("tokens", k=10)
    v2 = [c for c in r2.chunks if c.document == "docs/auth.md"]
    check(len(v2) == 1 and v2[0].version == "v2",
          "re-ingesting the current version is still one chunk (v2)")

    print("\nPASS: Phase 2 idempotency holds.")


if __name__ == "__main__":
    main()
