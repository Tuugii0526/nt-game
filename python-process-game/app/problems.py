"""Load problems from disk into an in-memory registry at startup.

A problem is a directory under `problems/` with:
  meta.json       { "id": "...", "title": "...", "time_limit_sec": 2, "memory_limit_mb": 128 }
  statement.md    Markdown describing the problem (incl. sample I/O).
  tests/NN.in     Input fed on stdin to the user's program.
  tests/NN.out    Expected stdout.

Tests are read once; the grader iterates them on every submission.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import markdown as md


@dataclass(frozen=True)
class TestCase:
    name: str
    stdin: str
    expected: str


@dataclass(frozen=True)
class Problem:
    id: str
    title: str
    time_limit_sec: float
    memory_limit_mb: int
    statement_html: str
    tests: tuple[TestCase, ...]


_PROBLEMS: dict[str, Problem] = {}


def load_all(directory: Path) -> None:
    _PROBLEMS.clear()
    if not directory.exists():
        return
    for child in sorted(p for p in directory.iterdir() if p.is_dir()):
        problem = _load_one(child)
        _PROBLEMS[problem.id] = problem


def all_problems() -> list[Problem]:
    return list(_PROBLEMS.values())


def get(problem_id: str) -> Problem | None:
    return _PROBLEMS.get(problem_id)


def _load_one(directory: Path) -> Problem:
    meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
    statement_md = (directory / "statement.md").read_text(encoding="utf-8")
    statement_html = md.markdown(statement_md, extensions=["fenced_code", "tables"])

    tests: list[TestCase] = []
    tests_dir = directory / "tests"
    if tests_dir.exists():
        for in_path in sorted(tests_dir.glob("*.in")):
            out_path = in_path.with_suffix(".out")
            if not out_path.exists():
                raise FileNotFoundError(f"Missing expected output for {in_path}")
            tests.append(TestCase(
                name=in_path.stem,
                stdin=in_path.read_text(encoding="utf-8"),
                expected=out_path.read_text(encoding="utf-8"),
            ))

    return Problem(
        id=meta["id"],
        title=meta["title"],
        time_limit_sec=float(meta.get("time_limit_sec", 2.0)),
        memory_limit_mb=int(meta.get("memory_limit_mb", 128)),
        statement_html=statement_html,
        tests=tuple(tests),
    )
