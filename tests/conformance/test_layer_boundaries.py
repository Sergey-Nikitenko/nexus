"""Layer boundary gate — the machine that holds the line on dependency direction.

Rule: infrastructure conforms to contracts, never the reverse.

- core/            imports ONLY the standard library (no project layers, no third-party).
- control/         may import core (and itself), never execution/knowledge/etc.
- execution/       may import core (and itself), never control.
- knowledge/ integrations/ observability/ memory/ — same: core only.
- apps/            the top layer, may import anything.

Run:  py tests/golden/test_layer_boundaries.py
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STDLIB = set(sys.stdlib_module_names)
LAYERS = ("core", "control", "execution", "knowledge", "memory", "integrations", "observability", "apps")

# What each layer may import beyond itself + the stdlib.
ALLOWED = {
    "core": set(),
    "control": {"core"},
    "execution": {"core"},
    "knowledge": {"core"},
    "memory": {"core"},
    "integrations": {"core"},
    "observability": {"core"},
    "apps": set(LAYERS),
}


def absolute_imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module


def main() -> None:
    violations: list[str] = []
    for py in sorted(ROOT.rglob("*.py")):
        rel = py.relative_to(ROOT)
        if rel.parts[0] == "tests":
            continue
        layer = rel.parts[0] if rel.parts[0] in LAYERS else None
        if layer is None:
            continue
        for imp in absolute_imports(py):
            root = imp.split(".")[0]
            if layer == "core":
                if root not in STDLIB:
                    violations.append(f"{rel}: core imports non-stdlib '{root}'")
            elif root in LAYERS and root != layer and root not in ALLOWED[layer]:
                violations.append(f"{rel}: layer '{layer}' must not import '{imp}' (reaches into '{root}')")

    if violations:
        for v in violations:
            print("  VIOLATION:", v)
        raise SystemExit(f"{len(violations)} layer-boundary violation(s)")
    print("PASS: layer boundaries hold — core is stdlib-only; nothing imports upward.")


if __name__ == "__main__":
    main()
