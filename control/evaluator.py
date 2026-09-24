"""Evaluator — deterministic verification, independent of the model's opinion.

The evaluator checks what ACTUALLY happened (did tests pass? did the tools
succeed? which files changed?) and returns an Evaluation (a Phase 0 contract).
It never asks the model "did it work" — that's the point: evidence over claims.
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
        return Evaluation(checks=checks)

    @staticmethod
    def passed(eval_: Evaluation) -> bool:
        """A run passes iff no hard check failed. Soft scores (groundedness) don't
        gate it — only deterministic evidence does."""
        return not (
            eval_.checks.get("tests") == "fail"
            or eval_.checks.get("tool_success") is False
        )
