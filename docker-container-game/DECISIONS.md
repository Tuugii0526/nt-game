# Design decisions - `docker-container-game`

This is the defence-in-depth variant of the offline coding judge. The
app runs in one container; submissions are graded by a **warm pool** of
long-lived worker containers. The two halves communicate over a shared
filesystem volume using atomic file drops; **no `docker.sock` is
exposed to the app**.

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

We pick this variant over the [sibling subprocess
variant](../python-process-game) when we want kernel-enforced isolation
(cgroups, namespaces, capability dropping) on top of the language-level
guarantees, even though it costs an extra moving part (Docker).

## Web framework: FastAPI over Flask

The original project proposal mentioned Flask. We started with that and
switched to FastAPI mid-build at the user's request.

Endpoints stay **sync `def`**. FastAPI dispatches sync routes to its
threadpool, so the blocking grader (which submits a job and waits for a
result file to appear) runs off the event loop and concurrent
submissions don't serialize.

Files: [app/app/routes.py](app/app/routes.py),
[app/app/\_\_init\_\_.py](app/app/__init__.py).

## Templates: Jinja2 + tiny vanilla `fetch()`

Same call as the sibling variant. We considered three frontend shapes:

1. Jinja2Templates only, full page reloads.
2. Jinja2Templates + HTMX for inline submit verdicts.
3. FastAPI as a JSON API + a separate React/Vite frontend.

We picked **Jinja + a ~30-line `submit.js`** that does one `fetch()` to
`/submit` and updates the DOM. Reasons:

- Zero build step, zero `npm`, zero vendored JS files.
- The submit flow is the only interactive bit. HTMX would still need a
  vendored JS file (~14 KB) for one form. Vanilla fetch is smaller in
  total moving parts.
- Three pages total (join, problem, leaderboard). A SPA is overkill.

Files: [app/templates/](app/templates), [app/static/submit.js](app/static/submit.js).

## Storage: SQLite + WAL on a named volume

One file (`/data/judge.db`), persisted in the `data` named Docker
volume. WAL mode lets the grader thread read while a request thread
writes. Two tables: `participants` and `submissions`.

The leaderboard is a single CTE that aggregates the best `tests_passed`
per `(participant, problem)` and sums them.

File: [app/app/db.py](app/app/db.py).

## Auth: name-only via signed session cookie

Participants type a name, the app upserts a row, signs the
`participant_id` into a Starlette `SessionMiddleware` cookie. No
password, no email, no PII. The 25-minute rolling timer keys off
`joined_at` written on first upsert.

## Hidden-test fairness, output normalization, partial credit

Same as the sibling variant. Failure messages name the failing test
(`Test 03: wrong answer`) but never reveal hidden inputs or expected
outputs. Output comparison ignores trailing whitespace per line and
trailing blank lines. Score is `tests_passed / tests_total`.

Implementation moved into the worker:
[worker/worker.py](worker/worker.py).

## Stable `JUDGE_SECRET` via environment

The session cookie is signed by a secret key. By default the app
generates a random one at startup (which means everyone gets logged out
on restart). For deployed setups we document writing
`JUDGE_SECRET=<random>` to `.env` next to `docker-compose.yml`, so
sessions survive a mid-contest container restart.

## Two starter problems

`01-sum-two` and `02-fizzbuzz`, five hidden tests each. Same as the
sibling variant - the problem set is portable between the two folders.

---

## Container strategy: warm worker pool

We considered three shapes:

1. **One container per test.** Simplest. ~2.3 s of pure Docker overhead
   per submission on Pi 4 (5 tests × ~400 ms startup).
2. **One container per submission**, with an in-container harness that
   runs the tests. ~0.7 s overhead. ~70 lines of code.
3. **Warm pool of long-lived workers** that poll a queue. ~0.1 s
   overhead. ~250 lines of code.

We picked **option 3 (warm pool)**. The runtime win over option 2 is
modest in absolute terms (~0.5 s), but the model generalizes naturally
if we ever scale workers, and a real queue protocol is more
operationally observable than nested `docker run` calls. The cost is
the protocol described next.

## IPC: filesystem dropbox over `os.rename` and `os.symlink`

We considered Redis, Unix sockets, and a filesystem-based dropbox. We
picked the dropbox because:

- It needs no extra dependency (no Redis container, no socket library).
- POSIX `rename(2)` is atomic on the same filesystem.
- POSIX `symlink(2)` returns `EEXIST` atomically when the target name
  is taken, which gives us a free atomic-claim primitive: workers race
  to `os.symlink(WORKER_ID, /queue/claims/<id>)` and exactly one wins.
- The "queue volume" is just a Docker named volume; both the app and
  the workers mount it.

The protocol uses three subdirs:

```text
/queue/jobs/<id>.json     written by app via tmp+rename
/queue/claims/<id>        symlink, atomically created by winning worker
/queue/results/<id>.json  written by worker via tmp+rename
```

The app polls `/queue/results/<id>.json` every 20 ms with a 60 s
ceiling. If the worker pool is overloaded and nothing arrives, the app
returns a synthetic "Worker pool overloaded" verdict instead of hanging
the request.

Files: [app/app/judge/queue_client.py](app/app/judge/queue_client.py),
[worker/worker.py](worker/worker.py).

## App **never** has `docker.sock`

This was a hard requirement. If the app gets compromised, it must not
be able to spawn new privileged containers. Because the worker pool is
already running and IPC is purely file-based, the app has no need for
the Docker socket and we never mount it. The blast radius of an app
compromise is the app container itself.

## App as root, workers as `nobody (65534)`

A named Docker volume mounts as root-owned `0755` by default. The
worker container runs as `nobody` for defence in depth, which means it
cannot write to a fresh volume.

We solve this in the **app's** lifespan startup (`ensure_dirs()`):
the app is the first container to touch `/queue`, runs as root, and
chmods each subdir to `0o777` so the workers can write claims and
results. The queue is private intra-container IPC, so worldwide write
permission inside the container is fine - nothing outside the named
volume can reach it.

## Worker isolation flags

Configured in [docker-compose.yml](docker-compose.yml):

| Flag | Purpose |
| --- | --- |
| `network_mode: none` | Worker has no network. Period. |
| `read_only: true` | Root filesystem is immutable. |
| `tmpfs: /tmp:size=8m,exec` | The only writable spot, gone on restart. `exec` because we drop `submission.py` there and run it. |
| `user: 65534:65534` | Runs as `nobody`. |
| `cap_drop: [ALL]` | No Linux capabilities at all. |
| `security_opt: no-new-privileges:true` | Cannot setuid to escalate. |
| `pids_limit: 64` | Blocks fork bombs at the kernel level. This works because it's per-container, not per-user as it would be on the host. |
| `mem_limit: 128m` | Kernel OOM-kills the user's process if it exceeds. Returncode comes back as `-9` and the worker maps it to MLE. |

## Per-test fresh subprocess inside the worker

Even though the worker container is long-lived, each test runs as a
**fresh** `python3 -I -S` subprocess inside the worker. State cannot
leak between tests of the same submission, and certainly not between
submissions. The worker is just an executor; the actual sandboxing
boundary is the *container*, not any process inside it.

`python3 -I -S` is isolated mode: ignores `PYTHON*` env vars and the
user's site-packages. Submissions can only import the standard library.
Intentional for fairness.

## Env-driven paths

`JUDGE_QUEUE_DIR`, `JUDGE_DB_PATH`, `JUDGE_PROBLEMS_DIR`, `JUDGE_SECRET`,
`JUDGE_JOB_TIMEOUT_SEC`. Defaults match the in-container layout
(`/queue`, `/data/judge.db`, `/problems`) but can be overridden so the
same code runs in a host venv during dev (with relative paths) and in
the container during prod. The smoke test exploits this by spawning the
worker as a plain subprocess with `JUDGE_QUEUE_DIR` pointing at a temp
dir.

---

## What we deliberately did **not** build (YAGNI)

- **HTTPS.** The whole network is air-gapped LAN.
- **Authentication beyond name.**
- **Multi-language support.** Python only.
- **A real code editor.**
- **Database migrations.**
- **No swap accounting tweaks.** `mem_limit` alone works on stock Pi OS.
- **No worker autoscaling.** Set `replicas:` in compose; scale with
  `docker compose up -d --scale worker=N` if needed.
- **No persistent claim recovery.** If a worker process is killed
  mid-job, its claim symlink is left behind. The next time the
  container starts, the worker's `_sweep_stale_claims()` cleans up any
  claim whose target equals its own `WORKER_ID`. We do **not** try to
  re-grade orphaned jobs from a different process; the user can
  re-submit. Simpler than a full lease/expiry protocol.
- **No `docker.sock` mount in the app.** See above.

---

## Things we discovered while building (debugging notes)

### `TestClient` does not run lifespan by default

Starlette's `TestClient(app)` only fires FastAPI's lifespan startup
(where we init the DB, load problems, and `chmod 0o777` the queue
dirs) when used as a context manager. Outside `with`, the queue dirs
don't exist and submit times out. The smoke test uses
`cm.__enter__()` / `cm.__exit__()` to trigger startup explicitly.

### Docker named volumes default to root-owned `0755`

That's what forced the `chmod 0o777` in `ensure_dirs()`. If we'd run
the worker as root, this would have been moot, but we wanted the extra
layer of "even if the worker code is exploited, it's running as
`nobody` with no caps".

### `tmpfs` defaults to `noexec`

Docker mounts `tmpfs` with `noexec` by default, but our worker unpacks
`submission.py` to `/tmp` and runs it. The compose flag is
`tmpfs: /tmp:size=8m,exec`. Forgetting `exec` was a 5-minute confused
detour during early testing.

### `deploy.replicas` works under `docker compose up`

Older docs imply `deploy:` is swarm-only, but Docker Compose v2 honors
`deploy.replicas` for non-swarm `docker compose up`. We use it for the
worker pool size.

---

## How to extend

- **Add a problem**: drop a folder into `problems/`. The host bind-mount
  picks it up; restart the app to reload (`docker compose restart app`).
  Workers re-read tests inside each job payload, so they don't need a
  restart.
- **Tighten time/memory limits per problem**: edit the problem's
  `meta.json`.
- **Change the contest length**: edit `CONTEST_DURATION_SEC` in
  [app/app/timer.py](app/app/timer.py).
- **Scale the worker pool**: `docker compose up -d --scale worker=N` or
  edit `replicas:` in [docker-compose.yml](docker-compose.yml).
- **Tighten the worker memory cap**: edit `mem_limit:` in
  [docker-compose.yml](docker-compose.yml).

## Pointers

- Pi runbook: [DEPLOY.md](DEPLOY.md)
- Sibling variant: [../python-process-game](../python-process-game)
