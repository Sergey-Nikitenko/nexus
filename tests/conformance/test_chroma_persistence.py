"""Conformance: Chroma (implementation #3) persists across a real restart.

ingest (process 1) -> exit -> new process -> retrieve (process 2) -> RetrievalResult

Unlike the SQLite store's in-process close/reopen, Chroma holds an on-disk lock
while its client is alive, so the restart proof runs two REAL separate processes.
The assertions are the same boundary properties as the SQLite persistence test:
contract shape, provenance, identity, version, filtering, current-version.

Run:  py tests/conformance/test_chroma_persistence.py
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


INGEST = '''
import os
from knowledge.chroma import ChromaKnowledgeStore
from knowledge.inmemory import ComposedRetriever, HashingEmbedder

path = os.environ["CHROMA_PATH"]
r = ComposedRetriever(store=ChromaKnowledgeStore(path=path), embedder=HashingEmbedder())
r.ingest("filesystem", "docs/auth.md", "v1",
         "auth verifies tokens with a secret", metadata={"team": "platform"})
r.ingest("filesystem", "docs/auth.md", "v2",
         "auth verifies and refreshes tokens with a secret", metadata={"team": "platform"})
r.ingest("filesystem", "docs/billing.md", "v1",
         "billing calculates invoices", metadata={"team": "finance"})
'''

RETRIEVE = '''
import json
import os

from core.contracts import RetrievedChunk, RetrievalResult
from knowledge.chroma import ChromaKnowledgeStore
from knowledge.inmemory import ComposedRetriever, HashingEmbedder

path = os.environ["CHROMA_PATH"]
r = ComposedRetriever(store=ChromaKnowledgeStore(path=path), embedder=HashingEmbedder())
res = r.search("tokens", k=5)
out = {
    "is_retrieval": isinstance(res, RetrievalResult),
    "all_contract": bool(res.chunks) and all(isinstance(c, RetrievedChunk) for c in res.chunks),
    "auth_versions": sorted({c.version for c in res.chunks if c.document == "docs/auth.md"}),
    "finance_teams": [c.metadata.get("team")
                      for c in r.search("invoices", filters={"team": "finance"}, k=5).chunks],
}
with open(os.environ["RESULT_PATH"], "w", encoding="utf-8") as f:
    json.dump(out, f)
'''


def main() -> None:
    print("Conformance: Chroma persistence is observationally equivalent across a restart")
    tmp = tempfile.mkdtemp()
    chroma_path = os.path.join(tmp, "chroma")
    result_path = os.path.join(tmp, "result.json")
    env = {
        **os.environ,
        "PYTHONPATH": ROOT,
        "CHROMA_PATH": chroma_path,
        "RESULT_PATH": result_path,
    }

    # two REAL separate processes: ingest exits, then a fresh process retrieves
    for code in (INGEST, RETRIEVE):
        proc = subprocess.run([sys.executable, "-c", code], env=env)
        if proc.returncode != 0:
            raise AssertionError(f"subprocess exited {proc.returncode}")

    with open(result_path, encoding="utf-8") as f:
        out = json.load(f)

    check(out["is_retrieval"], "after restart: result is a RetrievalResult")
    check(out["all_contract"], "after restart: chunks are RetrievedChunk contracts")
    check(out["auth_versions"] == ["v2"],
          "after restart: current-version semantics survive (only v2)")
    check(out["finance_teams"] == ["finance"], "after restart: filters are honored")

    print("\nPASS: Chroma persists observationally equivalently across a restart.")


if __name__ == "__main__":
    main()
