"""Build a job for the worker pool and translate the response back into a
`GradeResult`. Verdict semantics, hidden-test fairness, and partial-credit
scoring all live in `worker.py` now.
"""
from __future__ import annotations

from dataclasses import dataclass

from .. import problems
from . import queue_client


@dataclass(frozen=True)
class GradeResult:
    verdict: str
    tests_passed: int
    tests_total: int
    runtime_ms: int
    detail: str

    def as_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "tests_passed": self.tests_passed,
            "tests_total": self.tests_total,
            "runtime_ms": self.runtime_ms,
            "detail": self.detail,
        }


def grade(problem_id: str, code: str) -> GradeResult:
    problem = problems.get(problem_id)
    if problem is None or not problem.tests:
        return GradeResult("NF", 0, 0, 0, "Problem not found or has no tests")

    job = {
        "problem_id": problem_id,
        "code": code,
        "tests": [
            {
                "name": t.name,
                "stdin": t.stdin,
                "expected": t.expected,
                "time_limit_sec": problem.time_limit_sec,
                "memory_limit_mb": problem.memory_limit_mb,
            }
            for t in problem.tests
        ],
    }
    res = queue_client.submit(job)
    return GradeResult(
        verdict=res["verdict"],
        tests_passed=res["tests_passed"],
        tests_total=res["tests_total"],
        runtime_ms=res["runtime_ms"],
        detail=res["detail"],
    )
