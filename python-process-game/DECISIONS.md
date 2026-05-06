# Design decisions - `python-process-game`

This is the lightweight variant of the offline coding judge. The app
runs as a single Python process on the Raspberry Pi and grades each
submission in a hardened `subprocess` with `setrlimit` and process
groups. No Docker, no extra services.

This document captures **why** we made the choices in this folder, what
alternatives were considered, and what we discovered along the way that
changed the plan.

## Threat model

The intended setting is a 25-minute classroom-style coding contest on an
**air-gapped Wi-Fi** broadcast by the same Pi (RaspAP). Participants are
trusted humans, not adversaries; the goal of the sandbox is to stop
*accidental* damage:

- An infinite loop in a submission must not hang the server.
- A runaway memory allocation must not OOM-kill the Pi itself.
- A misbehaving submission must not be able to scribble over the host
  filesystem, the database, or other people's submissions.
- AI tools and online searches must be unreachable (this is enforced by
  the network topology, not by the app).

This threat model is the foundation for every other decision below.

## Web framework: FastAPI over Flask

The original RaspAP project proposal mentioned Flask. We started with
that and switched to FastAPI mid-build at the user's request.

Endpoints stay **sync `def`**. FastAPI dispatches sync routes to its
threadpool, so the blocking grader (which spawns a subprocess and waits)
runs off the event loop and concurrent submissions don't serialize. We
keep the mental model simple - no `async` / `await` discipline required
for whoever next touches the code.

Files: [routes.py](app/routes.py), [app/\_\_init\_\_.py](app/__init__.py).

## Templates: Jinja2 + tiny vanilla `fetch()`

We considered three frontend shapes:

1. Jinja2Templates only, full page reloads.
2. Jinja2Templates + HTMX for inline submit verdicts.
3. FastAPI as a JSON API + a separate React/Vite frontend.

We picked **Jinja + a ~30-line `submit.js`** that does one `fetch()` to
`/submit` and updates the DOM. Reasons:

- Zero build step, zero `npm`, zero vendored JS files. The whole repo is
  `git clone` + `pip install` and run.
- The submit flow is the only interactive bit. HTMX would still need a
  vendored JS file (~14 KB) for one form. Vanilla fetch is smaller in
  total moving parts.
- Three pages total (join, problem, leaderboard). A SPA is overkill.

Files: [templates/](templates), [static/submit.js](static/submit.js).

## Storage: SQLite + WAL

One file (`judge.db`), no external service, no migrations. WAL mode lets
the grader thread read while a request thread writes. Two tables:
`participants` and `submissions`.

The leaderboard is a single CTE that aggregates the best `tests_passed`
per `(participant, problem)` and sums them.

File: [app/db.py](app/db.py).

## Auth: name-only via signed session cookie

Participants type a name, the app upserts a row, signs the
`participant_id` into a Starlette `SessionMiddleware` cookie. No
password, no email, no PII. This is the right shape for a 25-minute
event with humans in the same room.

The 25-minute rolling timer keys off `joined_at` written on first
upsert. Logic: [timer.py](app/timer.py).

## Hidden-test fairness

Failure messages name the failing test (`Test 03: wrong answer`) but
**never** reveal hidden inputs or expected outputs. Sample I/O is shown
in `statement.md` for participants to debug against; everything else is
hidden.

Implementation: [grader.py](app/judge/grader.py).

## Output normalization

Both verdicts compare actual vs expected output after:

1. Trimming trailing whitespace per line.
2. Dropping trailing blank lines.

This keeps newline-fiddling (e.g. `print(x)` vs `sys.stdout.write(...)`)
from accidentally failing otherwise-correct solutions.

## Partial credit + leaderboard

A submission's score is `tests_passed / tests_total`. The leaderboard
ranks participants by `SUM(best tests_passed per problem)` desc,
tiebroken by earliest last-AC time. Partial credit is more motivating
than binary AC/WA for an introductory event.

## Stable `JUDGE_SECRET` via environment

The session cookie is signed by a secret key. By default the app
generates a random one at startup (which means everyone gets logged out
on restart). For deployed setups we document setting `JUDGE_SECRET` to a
stable value via `systemd` `Environment=`, so sessions survive a
mid-contest restart.

## Two starter problems

`01-sum-two` and `02-fizzbuzz`, five hidden tests each. They cover the
full verdict matrix (AC, WA, RE on parsing) without being so easy that
participants finish in 30 seconds. FizzBuzz tests were generated
programmatically to ensure the expected outputs are exactly correct.

---

## Sandbox: `subprocess` + `setrlimit` + `start_new_session`

This is the **single most important file** in this variant:
[runner.py](app/judge/runner.py).

Each test runs as a fresh `python3 -I -S submission.py` subprocess
started with `preexec_fn` that calls `resource.setrlimit` for CPU,
virtual memory (and data on macOS), file size, and core dumps. The
subprocess starts in a new session (`start_new_session=True`); on the
host's wall-clock timeout we `os.killpg(SIGKILL)` the entire session, so
any descendants the program managed to spawn die with their leader.

Why this shape and not Docker:

- The threat model is trusted classroom, not hostile internet. Kernel
  resource limits + process-group kill are sufficient.
- It's a single `pip install` away on a Pi, no extra daemon to keep
  alive, no images to build.
- Latency: ~50 ms per test. No container startup overhead.

## Cross-platform `setrlimit`: `_try_setrlimit`

macOS does not honor `RLIMIT_AS` the same way Linux does. We wrap each
limit in a try/except so dev on Mac runs (with reduced sandboxing - the
limit is silently skipped) while Linux/Pi gets the full set. We also
attempt `RLIMIT_DATA` as a fallback for `RLIMIT_AS`.

Code: `_try_setrlimit` in [runner.py](app/judge/runner.py).

## **Dropped** `RLIMIT_NPROC`

We initially set `RLIMIT_NPROC = 64` to block fork bombs. It broke
**every** test because:

- `RLIMIT_NPROC` on Linux is the **user's** total process count, not the
  fork descendant count.
- The host user (the Pi `pi` account, or a Mac dev account) routinely
  already has more than 64 processes running.
- Setting the soft+hard limit to a lower-than-current value made
  `subprocess.Popen`'s `fork()` return `EAGAIN` immediately, causing
  every submission to TLE at the spawn step.

We dropped it. The combination still in place (`RLIMIT_CPU` per process,
wall-clock timeout, and `killpg`) bounds runaway compute and contains
fork bombs at the cost of letting a fork bomb briefly stress the system
before the wall-clock kill. That's acceptable for the trusted-classroom
threat model.

## `uvicorn` (not `uvicorn[standard]`)

`uvicorn[standard]` pulls in `httptools`, `watchfiles`, `websockets`,
`uvloop` - several of which want C extensions. Pre-built wheels for
ARM64 are usually available, but not always; on stock Pi OS we want a
clean `pip install` to never fail. Plain `uvicorn` is pure Python,
ships h11 + asyncio default loop, and is fast enough for ~10
concurrent participants.

## `python3 -I -S`

Isolated mode. Ignores `PYTHON*` env vars and the user's site-packages.
Submissions can only import the standard library. This is intentional
for fairness - everyone has the same toolset.

---

## What we deliberately did **not** build (YAGNI)

- **HTTPS.** The whole network is air-gapped LAN. Cookies are signed
  but not encrypted in transit.
- **Authentication beyond name.** Same room, trusted humans.
- **Multi-language support.** Python only, by design.
- **A real code editor (Monaco/CodeMirror).** A `<textarea>` is enough
  for 25 minutes of small problems.
- **Database migrations.** Single CREATE-IF-NOT-EXISTS run on startup.
  If the schema changes, delete the DB.
- **Test-case generators / problem authoring CLI.** Drop folders into
  `problems/`, restart. Done.
- **Docker.** That's the [sibling variant](../docker-container-game).

---

## Things we discovered while building (debugging notes)

These are the bits that surprised us during development. Recording them
so the next person doesn't relearn the same lessons.

### `RLIMIT_NPROC` is user-wide, not process-tree-wide

Documented above. Cost us a 13-second wall TLE on every submission
until we narrowed it down by setting one rlimit at a time and seeing
which one made the subprocess hang.

### `TestClient` does not run lifespan by default

Starlette's `TestClient(app)` only fires FastAPI's lifespan startup
(where we init the DB and load problems) when used as a context
manager. Outside `with`, the DB tables don't exist and the first request
crashes with `no such table: participants`. Smoke tests use
`cm.__enter__()` / `cm.__exit__()` to trigger startup explicitly.

### macOS does not have `RLIMIT_AS` enforcement parity with Linux

`setrlimit(RLIMIT_AS, ...)` returns success on macOS but the limit is
not enforced the same way. We wrapped every limit in `_try_setrlimit`
which catches `(ValueError, OSError)` and skips on failure. Mac dev
keeps working with reduced sandboxing; Linux/Pi gets the full set.

### Cursor's command sandbox prevents fork+exec for some commands

Some shell commands run inside Cursor's sandbox can't `subprocess.Popen`
arbitrary children. When debugging the runner, we re-ran with elevated
permissions to escape the sandbox.

---

## How to extend

- **Add a problem**: drop a folder into `problems/`. Restart the app.
  Test ordering follows filename sort. Keep sample I/O in
  `statement.md`; hidden tests in `tests/`.
- **Tighten time/memory limits**: edit the problem's `meta.json`.
- **Change the contest length**: edit `CONTEST_DURATION_SEC` in
  [timer.py](app/timer.py).
- **Tighten the name length cap**: edit `MAX_NAME_LEN` in
  [routes.py](app/routes.py).

## Pointers

- Pi runbook: [DEPLOY.md](DEPLOY.md)
- Sibling variant: [../docker-container-game](../docker-container-game)
