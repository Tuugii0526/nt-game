"""Long-lived grader.

One worker per container. The worker polls the shared queue volume for new
jobs, atomically claims one (via `symlink` which is atomic on POSIX), runs
each test as a fresh `python3 -I -S` subprocess with a wall-clock timeout,
writes a result file atomically, and loops.

Outer-layer isolation lives in docker-compose:
  network_mode: none, read_only fs, cap_drop ALL, pids_limit, mem_limit,
  no-new-privileges, runs as nobody (uid 65534), tmpfs /tmp.

Inner-layer isolation per test:
  python3 -I -S          isolated mode, no site-packages, no PYTHON* envvars
  subprocess.run timeout per-test wall-clock kill
  fresh subprocess         no state leak between tests
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

QUEUE_DIR = Path(os.environ.get("JUDGE_QUEUE_DIR", "/queue"))
JOBS_DIR = QUEUE_DIR / "jobs"
RESULTS_DIR = QUEUE_DIR / "results"
CLAIMS_DIR = QUEUE_DIR / "claims"

WORKER_ID = f"{socket.gethostname()}-{os.getpid()}"
IDLE_SLEEP_SEC = 0.02


# --------------------------------------------------------------------------
# Queue ops
# --------------------------------------------------------------------------

def _ensure_dirs() -> None:
    for d in (JOBS_DIR, RESULTS_DIR, CLAIMS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def _sweep_stale_claims() -> None:
    """If the previous run died holding claims, drop them. Safe because we
    are the only worker process inside this container and the container has
    just started."""
    if not CLAIMS_DIR.exists():
        return
    for entry in CLAIMS_DIR.iterdir():
        try:
            target = os.readlink(entry)
        except OSError:
            continue
        if target == WORKER_ID:
            entry.unlink(missing_ok=True)


def _claim_one() -> str | None:
    if not JOBS_DIR.exists():
        return None
    for entry in sorted(JOBS_DIR.glob("*.json")):
        job_id = entry.stem
        claim = CLAIMS_DIR / job_id
        try:
            os.symlink(WORKER_ID, claim)
            return job_id
        except FileExistsError:
            continue
    return None


def _write_atomic(path: Path, payload: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload)
    os.rename(tmp, path)


# --------------------------------------------------------------------------
# Grading
# --------------------------------------------------------------------------

def _normalize(text: str) -> str:
    lines = [line.rstrip() for line in text.splitlines()]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def _run_test(code_path: Path, test: dict) -> tuple[str, float, str]:
    """Run one test. Returns (verdict, runtime_sec, stderr_tail)."""
    started = time.monotonic()
    try:
        proc = subprocess.run(
            ["python3", "-I", "-S", str(code_path)],
            input=test["stdin"],
            capture_output=True,
            text=True,
            timeout=float(test["time_limit_sec"]) + 0.5,
            cwd=str(code_path.parent),
        )
    except subprocess.TimeoutExpired:
        return "TLE", time.monotonic() - started, ""
    runtime = time.monotonic() - started

    if proc.returncode == -9:
        return "MLE", runtime, ""
    if proc.returncode != 0:
        err = (proc.stderr or "").strip().splitlines()
        return "RE", runtime, err[-1] if err else f"exit code {proc.returncode}"
    if _normalize(proc.stdout) != _normalize(test["expected"]):
        return "WA", runtime, ""
    return "AC", runtime, ""


def _human(verdict: str, stderr_tail: str) -> str:
    if verdict == "TLE":
        return "time limit exceeded"
    if verdict == "MLE":
        return "memory limit exceeded"
    if verdict == "RE":
        return f"runtime error ({stderr_tail})" if stderr_tail else "runtime error"
    if verdict == "WA":
        return "wrong answer"
    return verdict


def _grade(job: dict) -> dict:
    tests = job.get("tests") or []
    total = len(tests)
    passed = 0
    runtime_total = 0.0
    fail_verdict = "AC"
    fail_detail = ""

    with tempfile.TemporaryDirectory(prefix="sub-") as tmp:
        code_path = Path(tmp) / "submission.py"
        code_path.write_text(job.get("code", ""))

        for test in tests:
            verdict, dt, stderr_tail = _run_test(code_path, test)
            runtime_total += dt
            if verdict == "AC":
                passed += 1
            elif fail_verdict == "AC":
                fail_verdict = verdict
                fail_detail = f"Test {test['name']}: {_human(verdict, stderr_tail)}"

    all_pass = total > 0 and passed == total
    return {
        "verdict": "AC" if all_pass else fail_verdict if total else "NF",
        "tests_passed": passed,
        "tests_total": total,
        "runtime_ms": int(runtime_total * 1000),
        "detail": "All tests passed" if all_pass else (fail_detail or "No tests"),
    }


# --------------------------------------------------------------------------
# Main loop
# --------------------------------------------------------------------------

def _process(job_id: str) -> None:
    job_path = JOBS_DIR / f"{job_id}.json"
    claim_path = CLAIMS_DIR / job_id
    result_path = RESULTS_DIR / f"{job_id}.json"
    try:
        job = json.loads(job_path.read_text())
        result = _grade(job)
    except Exception as e:
        result = {
            "verdict": "RE",
            "tests_passed": 0,
            "tests_total": 0,
            "runtime_ms": 0,
            "detail": f"Worker crashed: {type(e).__name__}: {e}",
        }
    finally:
        try:
            _write_atomic(result_path, json.dumps(result))
        finally:
            job_path.unlink(missing_ok=True)
            claim_path.unlink(missing_ok=True)


def main() -> None:
    _ensure_dirs()
    _sweep_stale_claims()
    print(f"[worker] {WORKER_ID} ready, watching {QUEUE_DIR}", flush=True)
    while True:
        job_id = _claim_one()
        if job_id is None:
            time.sleep(IDLE_SLEEP_SEC)
            continue
        print(f"[worker] {WORKER_ID} grading {job_id}", flush=True)
        _process(job_id)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
