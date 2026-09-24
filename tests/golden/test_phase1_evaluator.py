"""Phase 1 golden tasks — the evaluator (evidence over claims).

G010  detect failed verification (tests/tools), and pass on real evidence.

The key property: the evaluator reports what ACTUALLY happened, never what the
model claimed.

Run:  py tests/golden/test_phase1_evaluator.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import ToolCall, ToolResult  # noqa: E402
from control.evaluator import Evaluator  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def main():
    print("Phase 1 golden tasks: evaluator (evidence over claims)")
    ev = Evaluator()

    # Everything actually succeeded -> pass
    good = ev.evaluate(tests_passed=True,
                       tool_results=[ToolResult(tool_call=ToolCall("github.read_file"), success=True)],
                       files_changed=["middleware.py"])
    check(ev.passed(good), "G010: tests passed + tools succeeded -> pass")
    check(good.checks["tests"] == "pass", "G010: reports tests=pass from the test run")

    # Tests failed (even though the model said it worked) -> fail
    bad = ev.evaluate(tests_passed=False,
                      tool_results=[ToolResult(tool_call=ToolCall("github.read_file"), success=True)])
    check(not ev.passed(bad), "G010: tests failed -> fail (regardless of the model's claim)")
    check(bad.checks["tests"] == "fail", "G010: deterministic fail is recorded, not the model's word")

    # A tool failed -> fail, and names the failed tool
    tool_fail = ev.evaluate(tests_passed=True,
                            tool_results=[
                                ToolResult(tool_call=ToolCall("github.read_file"), success=True),
                                ToolResult(tool_call=ToolCall("shell.run_tests"), success=False, error="segfault"),
                            ])
    check(not ev.passed(tool_fail), "G010: a failed tool -> fail")
    check(tool_fail.checks["failed_tools"] == ["shell.run_tests"], "G010: names the failed tool")

    # Soft scores don't gate the pass/fail (only deterministic evidence does)
    soft = ev.evaluate(tests_passed=True, groundedness=0.5)
    check(ev.passed(soft), "G010: low groundedness alone does not fail a run (soft score, not evidence)")

    print("\nPASS: Phase 1 evaluator holds.")


if __name__ == "__main__":
    main()
