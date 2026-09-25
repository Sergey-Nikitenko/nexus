"""Conformance: the core system consumes contracts, never provider objects.

Provider/implementation libraries (vector DBs, model SDKs, vendor APIs, HTTP
clients) may appear ONLY in the adapters — knowledge/ and integrations/.
core/, control/, execution/, memory/, observability/, apps/ must never import
them, because they consume RetrievalResult / ModelResponse / ToolResult /
Episode, not Chroma / OpenAI / GitHub objects.

Run:  py tests/conformance/test_no_provider_leakage.py
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORE_LAYERS = ("core", "control", "execution", "memory", "observability", "apps")

PROVIDERS = {
    "chromadb", "qdrant", "weaviate", "pinecone", "pgvector", "faiss",
    "openai", "anthropic", "ollama", "github", "gitlab", "slack", "google",
    "requests", "httpx", "aiohttp", "mcp",
}


def main() -> None:
    violations: list[str] = []
    for py in sorted(ROOT.rglob("*.py")):
        rel = py.relative_to(ROOT)
        if rel.parts[0] == "tests" or rel.parts[0] not in CORE_LAYERS:
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.split(".")[0] in PROVIDERS:
                        violations.append(f"{rel}: core layer imports provider '{a.name}'")
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.split(".")[0] in PROVIDERS:
                    violations.append(f"{rel}: core layer imports provider '{node.module}'")

    if violations:
        for v in violations:
            print("  VIOLATION:", v)
        raise SystemExit(f"{len(violations)} provider-leakage violation(s)")
    print("PASS: no provider leakage - the core consumes contracts; only adapters touch providers.")


if __name__ == "__main__":
    main()
