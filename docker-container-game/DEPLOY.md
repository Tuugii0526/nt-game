# Deploying the Docker variant on a Raspberry Pi

End-to-end guide for the Docker / `docker-compose` flavour of the judge.
Same UI and rules as the [`python-process-game`](../python-process-game)
variant, but submissions are graded in a warm pool of long-lived Docker
worker containers locked down with `network_mode: none`, `read_only: true`,
`cap_drop: ALL`, `pids_limit`, `mem_limit`, and `no-new-privileges`.

Assumes:

- RaspAP is already broadcasting an air-gapped Wi-Fi (default IP
  `10.3.141.1`).
- You can SSH into the Pi.

The Pi needs internet **once**, to install Docker and pull/build the two
images. After that it can stay completely offline forever.

---

## 0. Hardware checklist

| Item | Notes |
| --- | --- |
| Raspberry Pi 4 (4 GB+) or Pi 5 | Pi 5 starts containers ~2x faster. |
| MicroSD ≥ 16 GB | Raspberry Pi OS Bookworm 64-bit recommended. |
| Power | Wall adapter for setup; USB power bank for the event. |
| Ethernet cable (temporary) | Plug into your home router for the *one-time* install. |

---

## 1. Get internet on the Pi (temporarily)

Plug the Ethernet cable from your home router into the Pi. RaspAP keeps
broadcasting on Wi-Fi while the Pi routes its own traffic via Ethernet.

```bash
ping -c 3 1.1.1.1
```

If that works you're good.

---

## 2. Install Docker

The official one-liner works on Pi OS:

```bash
curl -sSL https://get.docker.com | sh
sudo usermod -aG docker $USER
```

Log out and back in (or reboot) so your shell picks up the `docker` group:

```bash
sudo reboot
```

After reboot:

```bash
docker version
docker compose version
```

Both must succeed without `sudo`.

---

## 3. Clone and build

```bash
cd ~
git clone <YOUR_REPO_URL> game
cd game/docker-container-game
docker compose build
```

The build pulls `python:3.11-slim` once (~120 MB) and produces two images:
`judge-app` and `judge-worker`. On a Pi 4 this takes ~2 minutes; on a Pi 5
under a minute. Both images are cached after that.

---

## 4. Start the stack

Set a stable session secret (any random string) and bring everything up:

```bash
echo "JUDGE_SECRET=$(openssl rand -hex 32)" > .env
docker compose up -d
```

Verify:

```bash
docker compose ps                 # 1 app + 3 workers, all "running"
docker compose logs -f app        # Uvicorn listening on 0.0.0.0:8000
docker compose logs -f worker     # "[worker] <id> ready, watching /queue"
curl -i http://localhost:8000/    # 303 redirect to /join
```

> The first `up` will take ~10 s while compose sets up the named volumes
> and the app initializes the queue subdirectories with the right
> permissions for the non-root workers.

---

## 5. Reach it from a laptop

1. On your laptop, disconnect from your home Wi-Fi.
2. Connect to the Pi's AP (e.g. `Offline_Coding_Contest`).
3. Open `http://10.3.141.1:8000`.

You should see the "Enter your name" page.

---

## 6. Go air-gapped

Disconnect Ethernet from the Pi (or disable upstream Wi-Fi in RaspAP).
The judge keeps working — workers have `network_mode: none` so they
weren't using the network anyway, and the app only needs LAN traffic.

Verify air-gap by trying to load `https://google.com` from a participant
laptop; it must fail.

---

## 7. Auto-start on boot

`docker compose` already configures `restart: unless-stopped`, so the
containers come back after a reboot **provided the Docker daemon
auto-starts**. On Pi OS that's the default after `apt install docker.io`
or the convenience script. Verify:

```bash
sudo systemctl is-enabled docker          # should print "enabled"
```

If not:

```bash
sudo systemctl enable --now docker
```

To make sure the stack is up after every reboot, use `docker compose up -d`
once with `restart: unless-stopped` set (already in our compose file). To
explicitly create a system unit, see the equivalent
[python-process-game DEPLOY.md](../python-process-game/DEPLOY.md) section —
the same pattern applies but `ExecStart` becomes
`/usr/bin/docker compose -f /home/pi/game/docker-container-game/docker-compose.yml up`.

---

## 8. Day-of-event checklist

- [ ] Pi powered on. Give it ~30 s to come up.
- [ ] `docker compose ps` shows app + 3 workers running.
- [ ] Connect a laptop to the AP, open `http://10.3.141.1:8000` — page loads.
- [ ] Try `https://google.com` from the laptop — must fail.
- [ ] Submit a known-good solution to confirm grading still works.
- [ ] Wipe test data (see "Reset" below).
- [ ] `/leaderboard` is empty.

When the contest starts, each participant joins, types a name, and their
**personal 25-minute timer** starts at that moment.

---

## 9. Reset all submissions

```bash
docker compose down
docker volume rm docker-container-game_data
docker compose up -d
```

(`data` is the named volume holding `judge.db`. The `queue` volume is
ephemeral; jobs and results are deleted as soon as they're consumed.)

---

## 10. Adding more problems

Problems live on the **host** (`./problems/`), bind-mounted read-only into
the app container at `/problems`. Add a folder:

```text
./problems/03-my-problem/
    meta.json
    statement.md
    tests/
        01.in
        01.out
        ...
```

Then reload the app (workers don't need a restart — they only see test
data inside each job payload):

```bash
docker compose restart app
```

---

## 11. Updating the code later

```bash
cd ~/game
git pull
cd docker-container-game
docker compose build
docker compose up -d
```

`docker compose up -d` is idempotent and recreates only changed containers.

---

## 12. Useful commands

| Action | Command |
| --- | --- |
| Status | `docker compose ps` |
| App logs | `docker compose logs -f app` |
| Worker logs | `docker compose logs -f worker` |
| Restart app | `docker compose restart app` |
| Stop everything | `docker compose down` |
| Reset DB | `docker compose down && docker volume rm docker-container-game_data && docker compose up -d` |
| Pi's IP on the AP | `hostname -I` (look for `10.3.141.x`) |
| Scale workers | `docker compose up -d --scale worker=5` |

---

## Troubleshooting

### `permission denied` writing to `/queue`

The app container should fix this on first start by `chmod 0o777`-ing the
queue subdirs. If it didn't (e.g. you mounted into a pre-existing rooted
volume):

```bash
docker compose exec app chmod -R 0777 /queue
```

### Workers stuck in `restarting`

```bash
docker compose logs worker
```

Most common cause is a missing `JUDGE_QUEUE_DIR` mount; check the compose
file's `volumes:` block.

### `TLE` on every submission

Either the wall-clock timeout is too aggressive, or the Pi is thermally
throttling. Check temperature:

```bash
vcgencmd measure_temp
```

If above 80 °C, give it some airflow. Time limits per problem live in
each `meta.json`.

### Submission code can't `import requests`

By design — the worker runs `python3 -I -S`, which ignores third-party
packages and PYTHON* env vars. Submissions should only need the standard
library.
