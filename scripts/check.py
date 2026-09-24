"""Run every golden task (behavioral) + every conformance test (architectural)
in one shot. Discovers tests/golden/test_*.py and tests/conformance/test_*.py
automatically, so new tests are picked up with no edit here.

Usage:  py scripts/check.py
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST_DIRS = (ROOT / "tests" / "golden", ROOT / "tests" / "conformance")
TESTS = sorted(p for d in TEST_DIRS for p in d.glob("test_*.py"))


def main() -> int:
    if not TESTS:
        print("no golden tasks found under tests/golden/")
        return 1

    failures: list[str] = []
    for t in TESTS:
        print(f"--- {t.relative_to(ROOT)} ---")
        r = subprocess.run([sys.executable, str(t)], capture_output=True, text=True)
        if r.stdout:
            print(r.stdout, end="")
        if r.returncode != 0:
            if r.stderr:
                print(r.stderr, end="")
            failures.append(t.name)

    print("\n" + "=" * 52)
    if failures:
        print(f"FAIL: {len(failures)}/{len(TESTS)} failed -> {', '.join(failures)}")
        return 1
    print(f"PASS: all {len(TESTS)} tests (golden + conformance) hold.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
