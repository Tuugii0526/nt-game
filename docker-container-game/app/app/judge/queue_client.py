"""Job submission + result polling over a shared filesystem volume.

The contract with the worker:
  /queue/jobs/<id>.json     written by the app (atomic via tmp+rename),
                            read+removed by the winning worker.
  /queue/claims/<id>        symlink atomically created by the worker that
                            won the job (we don't touch this from the app).
  /queue/results/<id>.json  written by the worker (atomic via tmp+rename),
                            read+removed by the app.

Atomicity rests on POSIX `rename` and `symlink(... target_existing)`
returning EEXIST without partial state.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

QUEUE_DIR = Path(os.environ.get("JUDGE_QUEUE_DIR", "/queue"))
JOBS_DIR = QUEUE_DIR / "jobs"
RESULTS_DIR = QUEUE_DIR / "results"
CLAIMS_DIR = QUEUE_DIR / "claims"

# Per-submission deadline. Workers run each test up to its own time limit;
# this is the upper bound the app waits for *any* worker to come back.
JOB_TIMEOUT_SEC = float(os.environ.get("JUDGE_JOB_TIMEOUT_SEC", "60"))
POLL_INTERVAL_SEC = 0.02


def ensure_dirs() -> None:
    """Create the three queue subdirs and make them world-writable.

    The app container runs as root and initializes the volume; the worker
    containers run as `nobody` (uid 65534) and need to write claims and
    results into the same dirs. 0o777 (no sticky bit) is fine here because
    the queue is private intra-container IPC, not a multi-user shared dir.
    """
    for d in (JOBS_DIR, RESULTS_DIR, CLAIMS_DIR):
        d.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(d, 0o777)
        except PermissionError:
            pass  # already wide enough or set by another container


def submit(job: dict) -> dict:
    """Drop a job into the queue and block until a worker writes a result."""
    ensure_dirs()
    job_id = uuid.uuid4().hex
    payload = json.dumps(job)
    tmp = JOBS_DIR / f"{job_id}.json.tmp"
    final = JOBS_DIR / f"{job_id}.json"
    tmp.write_text(payload)
    os.rename(tmp, final)
    try:
        return _await_result(job_id, total=len(job.get("tests", [])))
    except TimeoutError:
        # Best-effort cleanup: yank the job back if no worker grabbed it.
        final.unlink(missing_ok=True)
        return {
            "verdict": "RE",
            "tests_passed": 0,
            "tests_total": len(job.get("tests", [])),
            "runtime_ms": 0,
            "detail": "Worker pool overloaded; try again",
        }


def _await_result(job_id: str, total: int) -> dict:
    result_path = RESULTS_DIR / f"{job_id}.json"
    deadline = time.monotonic() + JOB_TIMEOUT_SEC
    while time.monotonic() < deadline:
        if result_path.exists():
            data = json.loads(result_path.read_text())
            result_path.unlink(missing_ok=True)
            return data
        time.sleep(POLL_INTERVAL_SEC)
    raise TimeoutError(f"no worker result for {job_id} within {JOB_TIMEOUT_SEC}s")
