"""Evaluator — deterministic verification, independent of the model's opinion.

The evaluator checks what ACTUALLY happened (did tests pass? did the tools
succeed? which files changed?) and returns an Evaluation (a Phase 0 contract).
It never asks the model "did it work" — that's the point: evidence over claims.

The evaluator OBSERVES and JUDGES; it never executes a capability or mutates
execution state (that is the orchestrator's job). It returns an Evaluation —
`passed` / `reason` / `replan_required` — and the orchestrator interprets it.
"""
from __future__ import annotations

from core.contracts import Evaluation, ToolResult


class Evaluator:
    def evaluate(
        self,
        *,
        tests_passed: bool | None = None,
        tool_results: list[ToolResult] | None = None,
        files_changed: list[str] | None = None,
        groundedness: float | None = None,
    ) -> Evaluation:
        checks: dict = {}
        if tests_passed is not None:
            checks["tests"] = "pass" if tests_passed else "fail"
        if tool_results is not None:
            checks["tool_success"] = all(r.success for r in tool_results)
            failed = [r.tool_call.tool_name for r in tool_results if not r.success]
            if failed:
                checks["failed_tools"] = failed
        if files_changed is not None:
            checks["files_changed"] = list(files_changed)
        if groundedness is not None:
            checks["groundedness"] = groundedness

        passed, reason = self._verdict(checks)
        return Evaluation(checks=checks, passed=passed, reason=reason)

    @staticmethod
    def _verdict(checks: dict) -> tuple[bool, str]:
        """A run passes iff no hard check failed. Soft scores (groundedness)
        don't gate it — only deterministic evidence does."""
        if checks.get("tests") == "fail":
            return False, "tests failed"
        if checks.get("tool_success") is False:
            return False, f"tool(s) failed: {checks.get('failed_tools')}"
        return True, "checks passed"

    @staticmethod
    def passed(eval_: Evaluation) -> bool:
        return eval_.passed


class FakeEvaluator:
    """Deterministic scripted evaluator — the reference double for the loop.

    Pops the next scripted Evaluation per call (mirrors FakeExecutor's
    model_script), so a test can prove FAIL -> replan -> PASS without any real
    verification or an LLM judge.
    """

    def __init__(self, script: list[Evaluation] | None = None,
                 default: Evaluation | None = None) -> None:
        self._script = list(script or [])
        self._default = default

    def evaluate(self, **kwargs) -> Evaluation:
        if self._script:
            return self._script.pop(0)
        if self._default is not None:
            return self._default
        return Evaluation(passed=True, reason="script exhausted")
