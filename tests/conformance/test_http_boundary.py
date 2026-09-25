"""Conformance: HTTP/framework types stop at the surface.

FastAPI, Pydantic, Starlette, uvicorn, httpx, and friends may appear ONLY in
apps/ (and tests). Nothing below apps/ — core, control, execution, knowledge,
memory, integrations, observability — may import them. Nexus has no knowledge
that HTTP exists.

Run:  py tests/conformance/test_http_boundary.py
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

FORBIDDEN = {"fastapi", "pydantic", "starlette", "uvicorn", "httpx", "aiohttp",
             "flask", "django", "websockets"}
BELOW = ("core", "control", "execution", "knowledge", "memory",
         "integrations", "observability")


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def main() -> None:
    print("Conformance: HTTP types stop at the surface")
    violations: list[str] = []
    for layer in BELOW:
        layer_dir = os.path.join(ROOT, layer)
        if not os.path.isdir(layer_dir):
            continue
        for py in sorted(os.listdir(layer_dir)):
            if not py.endswith(".py"):
                continue
            path = os.path.join(layer_dir, py)
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for a in node.names:
                        if a.name.split(".")[0] in FORBIDDEN:
                            violations.append(f"{layer}/{py}: imports '{a.name}'")
                elif isinstance(node, ast.ImportFrom) and node.module \
                        and node.module.split(".")[0] in FORBIDDEN:
                    violations.append(f"{layer}/{py}: imports '{node.module}'")

    check(not violations, "nothing below apps/ imports an HTTP/framework type")
    if violations:
        for v in violations:
            print("  VIOLATION:", v)
        raise SystemExit(1)

    print("\nPASS: HTTP is an edge concern; Nexus components are transport-agnostic.")


if __name__ == "__main__":
    main()
