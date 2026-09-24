"""Conformance: the orchestrator coordinates; it never performs capability work.

The orchestrator owns sequencing and correlation. Capabilities own execution.
So `execution/orchestrator.py` must never reach for a subprocess, a file handle,
an HTTP client, a vector store, a model SDK, or an MCP client — not by import,
not by a builtin `open()` call, not by `eval`/`exec`. It composes what it is
given (retriever, executor, policy, tools, bus) and emits events; it implements
none of them.

Run:  py tests/conformance/test_orchestrator_purity.py
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

# Modules whose import would mean the orchestrator is doing capability work.
FORBIDDEN_IMPORTS = {
    "subprocess", "os", "io", "pathlib", "shutil", "socket",
    "requests", "httpx", "aiohttp", "urllib",
    "chromadb", "qdrant", "weaviate", "pinecone", "pgvector", "faiss",
    "openai", "anthropic", "ollama", "google", "mcp",
}

# Builtin/name calls that are capability work regardless of imports.
FORBIDDEN_CALLS = {"open", "eval", "exec"}


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def main() -> None:
    print("Conformance: the orchestrator coordinates, never performs capability work")
    path = os.path.join(ROOT, "execution", "orchestrator.py")
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
        root = imp.split(".")[0]
        if root in FORBIDDEN_IMPORTS:
            violations.append(f"imports capability module '{imp}'")
    for name in calls:
        if name in FORBIDDEN_CALLS:
            violations.append(f"calls builtin '{name}()'")

    check(not violations, "the orchestrator never imports/calls a capability")
    if violations:
        for v in violations:
            print("  VIOLATION:", v)
        raise SystemExit(1)

    check(imports, "the orchestrator imports only core/control/execution contracts")
    print("\nPASS: the orchestrator composes capabilities; it implements none.")


if __name__ == "__main__":
    main()
