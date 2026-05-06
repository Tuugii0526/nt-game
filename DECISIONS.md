# Design decisions - index

This repo has two implementations of the same offline coding judge.
Each folder is self-contained and has its own design-decision log:

- [`python-process-game/DECISIONS.md`](python-process-game/DECISIONS.md) -
  the lightweight variant. App runs as a single Python process on the
  Pi; submissions graded in a hardened `subprocess` with `setrlimit` and
  process groups.
- [`docker-container-game/DECISIONS.md`](docker-container-game/DECISIONS.md) -
  the defence-in-depth variant. App runs in one container; grading
  happens in a warm pool of long-lived worker containers, isolated by
  cgroups, namespaces, dropped capabilities, no network, and read-only
  filesystems. No `docker.sock` exposed to the app.

## Cross-variant comparison

| Concern | python-process-game | docker-container-game |
| --- | --- | --- |
| Setup steps on Pi | `pip install` | install Docker, `docker compose up` |
| Sandbox layer | `setrlimit` + `killpg` on host | container cgroups + namespaces |
| Network isolation | host network is reachable | `network_mode: none` |
| Capability isolation | same UID as the app server | `cap_drop ALL`, read-only fs, `nobody` user |
| Submission crash blast radius | host process group | one worker container |
| Cold-start latency | ~50 ms per test | ~50 ms per test (warm pool) |
| Operational complexity | single Python process | app + worker containers + named volumes |
| Right tool when... | trusted classroom, fast iteration | want kernel-enforced defence in depth |

## Threat model (shared)

Both variants assume the same setting: a 25-minute classroom-style
coding contest on an air-gapped Wi-Fi broadcast by the Pi (RaspAP).
Participants are trusted humans, not adversaries; the goal of the
sandbox is to stop *accidental* damage:

- An infinite loop must not hang the server.
- A runaway memory allocation must not OOM-kill the Pi.
- A misbehaving submission must not be able to scribble over the host,
  the database, or other people's submissions.
- AI tools and online searches must be unreachable, enforced by the
  network topology rather than the app.

## Pointers

- Subprocess variant runbook: [python-process-game/DEPLOY.md](python-process-game/DEPLOY.md)
- Docker variant runbook: [docker-container-game/DEPLOY.md](docker-container-game/DEPLOY.md)
