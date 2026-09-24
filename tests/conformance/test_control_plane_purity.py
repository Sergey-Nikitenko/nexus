"""Conformance: the control plane decides, it never executes.

Static (AST) check that control/*.py is pure:
  - it does not import core.events (the EventBus) — control DECIDES, execution EMITS
  - it does not import I/O modules (subprocess, requests, urllib, socket, http)
  - it does not call I/O primitives (open, *_text, *_bytes, publish, ...)

Run:  py tests/conformance/test_control_plane_purity.py
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTROL = ROOT / "control"

BANNED_IMPORTS = {"subprocess", "requests", "urllib", "socket", "http", "aiohttp"}
BANNED_CORE = {"core.events"}  # the control plane never publishes
BANNED_CALLS = {"open", "publish", "write_text", "read_text", "write_bytes", "read_bytes", "system"}


def main() -> None:
    violations: list[str] = []
    for py in sorted(CONTROL.rglob("*.py")):
        rel = py.relative_to(ROOT)
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.split(".")[0] in BANNED_IMPORTS:
                        violations.append(f"{rel}: imports I/O module '{a.name}'")
                    if a.name in BANNED_CORE:
                        violations.append(f"{rel}: imports '{a.name}' (control must not emit)")
            elif isinstance(node, ast.ImportFrom):
                if node.module and (node.module.split(".")[0] in BANNED_IMPORTS or node.module in BANNED_CORE):
                    violations.append(f"{rel}: imports '{node.module}'")
            elif isinstance(node, ast.Call):
                name = (node.func.id if isinstance(node.func, ast.Name)
                        else node.func.attr if isinstance(node.func, ast.Attribute)
                        else None)
                if name in BANNED_CALLS:
                    violations.append(f"{rel}: calls '{name}' (control must not execute)")

    if violations:
        for v in violations:
            print("  VIOLATION:", v)
        raise SystemExit(f"{len(violations)} control-plane purity violation(s)")
    print("PASS: control plane is pure - it decides, never executes or emits.")


if __name__ == "__main__":
    main()
