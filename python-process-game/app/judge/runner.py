"""Run untrusted user Python in a hardened subprocess.

This is the only place that protects the host from `while True:`,
runaway memory, and disk-fill attacks. The defenses, in order:

  1. RLIMIT_CPU            hard CPU-time cap (kernel kills the process)
  2. RLIMIT_AS / RLIMIT_DATA  virtual-memory cap
  3. RLIMIT_FSIZE          max bytes the program can write to any file
  4. RLIMIT_CORE           no core dumps
  5. wall-clock timeout    via `subprocess.communicate(..., timeout=...)`
  6. start_new_session=True + os.killpg on timeout, so any descendants
     the program managed to spawn die together with their leader.

We deliberately do NOT set RLIMIT_NPROC: on Unix it's the *user's*
total process count, so tightening it would fail any time the host
user already has many processes. The wall-clock kill of the whole
process group is what bounds fork bombs in our (trusted classroom)
threat model.

`python3 -I -S` runs in isolated mode (ignores PYTHON* env vars and the
user's site-packages), so a submission can't import a wheel we didn't
intend to expose.
"""
from __future__ import annotations

import os
import resource
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RunResult:
    stdout: str
    stderr: str
    exit_code: int
    runtime_sec: float
    timed_out: bool


def _try_setrlimit(which_name: str, soft: int, hard: int) -> None:
    """Set a limit if the current OS exposes it; otherwise skip silently.

    macOS lacks RLIMIT_AS (and RLIMIT_NPROC behaves differently), but the Pi
    (Linux) supports them all. Skipping on dev hosts keeps tests runnable
    while production gets the full sandbox.
    """
    which = getattr(resource, which_name, None)
    if which is None:
        return
    try:
        resource.setrlimit(which, (soft, hard))
    except (ValueError, OSError):
        pass


def _set_limits(cpu_sec: int, mem_mb: int) -> None:
    mem_bytes = mem_mb * 1024 * 1024
    _try_setrlimit("RLIMIT_CPU", cpu_sec, cpu_sec)
    _try_setrlimit("RLIMIT_AS", mem_bytes, mem_bytes)
    _try_setrlimit("RLIMIT_DATA", mem_bytes, mem_bytes)
    _try_setrlimit("RLIMIT_FSIZE", 1 * 1024 * 1024, 1 * 1024 * 1024)
    _try_setrlimit("RLIMIT_CORE", 0, 0)


def run_python(
    code_path: Path,
    stdin_data: str,
    *,
    wall_sec: float,
    cpu_sec: int,
    mem_mb: int,
) -> RunResult:
    started = time.monotonic()
    proc = subprocess.Popen(
        ["python3", "-I", "-S", str(code_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(code_path.parent),
        preexec_fn=lambda: _set_limits(cpu_sec, mem_mb),
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(stdin_data, timeout=wall_sec)
        return RunResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=proc.returncode,
            runtime_sec=time.monotonic() - started,
            timed_out=False,
        )
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            pass
        return RunResult(
            stdout="",
            stderr="",
            exit_code=-9,
            runtime_sec=wall_sec,
            timed_out=True,
        )
