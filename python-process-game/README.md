# Coding Judge

A self-contained, offline coding-contest judge designed to run on a
Raspberry Pi acting as a Wi-Fi access point (see [project context](#project-context)).
Participants connect their laptop to the Pi's Wi-Fi, open a local URL,
and submit Python solutions. Because the network is air-gapped, AI tools
and online search engines are unreachable.

This repo is the **server** half. The Wi-Fi/AP setup (RaspAP) is done
separately on the Pi itself.

## Features

- 25-minute rolling timer per participant, starting on first login.
- 2 starter problems (Sum of Two, FizzBuzz). Add more by dropping a folder into `problems/`.
- Sandboxed grader: each submission runs in a hardened Python subprocess with
  CPU, memory, file-size, and process-count limits, plus a wall-clock timeout
  enforced via `os.killpg`. Infinite loops cannot hang the server.
- Partial credit: every test counts; the leaderboard sums best-per-problem.
- Single-file SQLite database (`judge.db`); zero external services.

## Requirements

- Python 3.10+ (Raspberry Pi OS Bookworm ships 3.11 — works out of the box).
- Linux host (uses `resource.setrlimit` and process groups). macOS works for development.

## Run it (on the Raspberry Pi)

For a full step-by-step guide (including auto-start on boot, going
air-gapped, and a day-of-event checklist) see **[DEPLOY.md](DEPLOY.md)**.

Quick version:

```bash
git clone <this-repo>
cd game
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

The server binds to `0.0.0.0:8000`. Participants on the AP network reach it
at `http://<pi-ip>:8000` (the RaspAP default is `http://10.3.141.1:8000`).

## Adding problems

Each problem is a directory under `problems/`:

```text
problems/03-my-problem/
  meta.json         # {"id": "03-my-problem", "title": "...", "time_limit_sec": 2, "memory_limit_mb": 128}
  statement.md      # markdown shown to participants (include sample I/O here)
  tests/
    01.in           # stdin fed to the program
    01.out          # expected stdout
    02.in / 02.out
    ...
```

Test ordering follows the filename sort. Output comparison ignores trailing
whitespace per line and trailing blank lines, so newline-fiddling won't fail
otherwise correct solutions.

Problems are loaded once at startup; restart the server after editing.

## Architecture

- **Web**: FastAPI + Jinja2Templates, served by `uvicorn`. Routes are sync `def`
  so the blocking grader runs in FastAPI's threadpool — concurrent submissions
  don't block the event loop.
- **Auth**: a name-only join, persisted in a signed session cookie (Starlette `SessionMiddleware`).
- **Storage**: SQLite via stdlib `sqlite3`. Two tables: `participants`, `submissions`.
- **Grader** (`app/judge/runner.py`): each submission runs in a `subprocess.Popen`
  started with `preexec_fn` setting `RLIMIT_CPU`, `RLIMIT_AS` / `RLIMIT_DATA`,
  `RLIMIT_FSIZE`, `RLIMIT_CORE`, plus `start_new_session=True`. On
  `subprocess.TimeoutExpired` we `os.killpg` the whole session, so any
  descendants the program managed to spawn die with it.
- **Verdicts**: `AC` (all tests pass), `WA`, `TLE`, `MLE`, `RE`, `NF`.

## Security notes / trade-offs

- **No Docker.** The `subprocess + setrlimit` sandbox is deliberately simpler
  and runs the same Python interpreter as the host. It's appropriate for
  trusted classroom-style use on an air-gapped LAN. It is **not** sufficient
  for adversarial untrusted code from the internet.
- **No HTTPS.** The whole network is air-gapped; cookies are signed but not
  encrypted in transit.
- **Hardened interpreter.** Submissions run with `python3 -I -S`, which ignores
  `PYTHON*` env vars and the user's site-packages.
- **Fairness.** Failure messages name the failing test (e.g. "Test 03: wrong
  answer") but never reveal hidden inputs or expected outputs.

## Project context

This is the server half of the "Air-Gapped Raspberry Pi Coding Judge" project:
the Pi runs RaspAP to broadcast a local Wi-Fi network with no upstream internet,
and runs this app to serve problems and grade submissions.
