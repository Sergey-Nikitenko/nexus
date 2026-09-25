"""Conformance: trace projection is pure — zero execution/provider/HTTP deps.

The projector (observability/trace.py) must consume only core.contracts +
core.events. It must not import execution, knowledge, memory, control,
integrations, apps, or any provider/HTTP framework.

Run:  py tests/conformance/test_trace_projection_purity.py
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

FORBIDDEN = {
    "execution", "knowledge", "memory", "control", "integrations", "apps",
    "observability",  # only the projector file itself, not its siblings
    "fastapi", "pydantic", "starlette", "uvicorn", "httpx", "aiohttp", "requests",
    "subprocess", "chromadb", "openai", "anthropic", "ollama", "mcp",
}


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def main() -> None:
    print("Conformance: trace projection is pure (read-side, no execution/provider deps)")
    path = os.path.join(ROOT, "observability", "trace.py")
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())

    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] in FORBIDDEN:
                    violations.append(f"imports '{a.name}'")
        elif isinstance(node, ast.ImportFrom) and node.module \
                and node.module.split(".")[0] in FORBIDDEN:
            violations.append(f"imports '{node.module}'")

    check(not violations, "the projector imports only core.contracts + core.events")
    if violations:
        for v in violations:
            print("  VIOLATION:", v)
        raise SystemExit(1)

    print("\nPASS: the projector has no execution/provider/HTTP knowledge.")


if __name__ == "__main__":
    main()
