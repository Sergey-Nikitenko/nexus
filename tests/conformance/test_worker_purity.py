"""Conformance: the worker claims and acks; it never implements a capability.

The worker composes the injected queue and orchestrator. It must never reach for
a provider (subprocess/file/network/vector-store/model-SDK/MCP) nor for the
knowledge layer (retrieval) — the orchestrator does that behind its own boundary.

Run:  py tests/conformance/test_worker_purity.py
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

FORBIDDEN_IMPORTS = {
    "subprocess", "os", "io", "pathlib", "shutil", "socket",
    "requests", "httpx", "aiohttp", "urllib",
    "chromadb", "qdrant", "weaviate", "pinecone", "pgvector", "faiss",
    "openai", "anthropic", "ollama", "google", "mcp",
    "knowledge",  # the worker must not know how retrieval works
}
FORBIDDEN_CALLS = {"open", "eval", "exec"}


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def main() -> None:
    print("Conformance: the worker composes; it never implements a capability")
    path = os.path.join(ROOT, "execution", "worker.py")
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())

    imports: list[str] = []
    calls: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            calls.append(node.func.id)

    violations: list[str] = []
    for imp in imports:
        if imp.split(".")[0] in FORBIDDEN_IMPORTS:
            violations.append(f"imports capability/layer module '{imp}'")
    for name in calls:
        if name in FORBIDDEN_CALLS:
            violations.append(f"calls builtin '{name}()'")

    check(not violations, "the worker never imports/calls a capability or the knowledge layer")
    if violations:
        for v in violations:
            print("  VIOLATION:", v)
        raise SystemExit(1)

    print("\nPASS: the worker has no provider/retrieval knowledge; it only coordinates.")


if __name__ == "__main__":
    main()
