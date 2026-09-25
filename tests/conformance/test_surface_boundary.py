"""Conformance: the surface observes and commands Nexus; execution never depends
on the surface.

The surface (apps/) composes Nexus and reads its durable events. Nothing below
apps/ — core, control, execution, knowledge, memory, integrations, observability —
may import `apps`. The execution plane must not know the dashboard/API exists.

Run:  py tests/conformance/test_surface_boundary.py
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

BELOW_SURFACE = ("core", "control", "execution", "knowledge", "memory",
                 "integrations", "observability")


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def main() -> None:
    print("Conformance: the surface is a leaf; execution never depends on it")
    violations: list[str] = []
    for layer in BELOW_SURFACE:
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
                        if a.name.split(".")[0] == "apps":
                            violations.append(f"{layer}/{py}: imports 'apps'")
                elif isinstance(node, ast.ImportFrom) and node.module \
                        and node.module.split(".")[0] == "apps":
                    violations.append(f"{layer}/{py}: imports '{node.module}'")

    check(not violations, "nothing below apps/ depends on the surface")
    if violations:
        for v in violations:
            print("  VIOLATION:", v)
        raise SystemExit(1)

    print("\nPASS: the surface observes/commands via contracts+events; execution is surface-agnostic.")


if __name__ == "__main__":
    main()
