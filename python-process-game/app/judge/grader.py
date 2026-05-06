"""Grade a submission against every test case for a problem.

Verdicts:
  AC   accepted (every test passed)
  WA   wrong answer  (output mismatch on at least one test)
  TLE  time-limit exceeded
  MLE  memory-limit exceeded (RLIMIT_AS killed the process: SIGKILL)
  RE   runtime error (uncaught exception, non-zero exit)
  NF   problem not found / no tests configured

Partial credit: the response also carries `tests_passed / tests_total`,
so the leaderboard ranks by total passed across all problems.

Hidden-test fairness: the failure detail names which test failed but
NEVER reveals the input or the expected output.
"""
from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from .. import problems
from . import runner


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

    with tempfile.TemporaryDirectory() as tmp:
        code_path = Path(tmp) / "submission.py"
        code_path.write_text(code, encoding="utf-8")

        passed = 0
        total_runtime = 0.0
        first_failure_verdict = "AC"
        first_failure_detail = ""

        for test in problem.tests:
            res = runner.run_python(
                code_path,
                test.stdin,
                wall_sec=problem.time_limit_sec + 0.5,
                cpu_sec=int(problem.time_limit_sec) + 1,
                mem_mb=problem.memory_limit_mb,
            )
            total_runtime += res.runtime_sec

            verdict = _verdict_for(res, test.expected)
            if verdict == "AC":
                passed += 1
            elif first_failure_verdict == "AC":
                first_failure_verdict = verdict
                first_failure_detail = _failure_detail(test.name, verdict, res)

        all_pass = passed == len(problem.tests)
        return GradeResult(
            verdict="AC" if all_pass else first_failure_verdict,
            tests_passed=passed,
            tests_total=len(problem.tests),
            runtime_ms=int(total_runtime * 1000),
            detail="All tests passed" if all_pass else first_failure_detail,
        )


def _verdict_for(res: runner.RunResult, expected: str) -> str:
    if res.timed_out:
        return "TLE"
    if res.exit_code == -9:
        return "MLE"
    if res.exit_code != 0:
        return "RE"
    if _normalize(res.stdout) != _normalize(expected):
        return "WA"
    return "AC"


def _normalize(text: str) -> str:
    """Trailing whitespace per line + trailing blank lines don't matter."""
    lines = [line.rstrip() for line in text.splitlines()]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def _failure_detail(test_name: str, verdict: str, res: runner.RunResult) -> str:
    if verdict == "TLE":
        return f"Test {test_name}: time limit exceeded"
    if verdict == "MLE":
        return f"Test {test_name}: memory limit exceeded"
    if verdict == "RE":
        err_lines = (res.stderr or "").strip().splitlines()
        msg = err_lines[-1] if err_lines else f"exit code {res.exit_code}"
        return f"Test {test_name}: runtime error ({msg})"
    if verdict == "WA":
        return f"Test {test_name}: wrong answer"
    return f"Test {test_name}: {verdict}"
