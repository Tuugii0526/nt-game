# Deploying the Coding Judge on a Raspberry Pi

End-to-end guide to get this app running on a Raspberry Pi that is also
acting as an air-gapped Wi-Fi access point (RaspAP). It assumes:

- You already have **RaspAP installed** and the Pi is broadcasting an SSID
  like `Offline_Coding_Contest` with DHCP handing out addresses to clients.
- Your Pi gateway IP on the AP network is **`10.3.141.1`** (the RaspAP default).
- You can SSH into the Pi (or you have a keyboard + screen plugged in).

The Pi will need internet **briefly, once**, to install Python packages.
After that it can stay completely offline forever.

---

## 0. Hardware checklist

| Item | Notes |
| --- | --- |
| Raspberry Pi 4 or 5 | Pi 5 is noticeably faster for grading. |
| MicroSD ≥ 16 GB | Flashed with Raspberry Pi OS (Bookworm, 64-bit recommended). |
| Power | Wall adapter for setup; a USB power bank for the event. |
| Ethernet cable (temporary) | Plug into your home router for the *one-time* dependency install. |

---

## 1. Get internet on the Pi (temporarily)

Plug the Ethernet cable from your home router into the Pi.
**Do not** disable RaspAP — the Pi can route its own traffic via Ethernet
while still broadcasting the AP on Wi-Fi. The simplest check:

```bash
ping -c 3 1.1.1.1
```

If that works, you have internet. Move on.

> If your RaspAP is configured to firewall outbound traffic, temporarily
> disable that, or temporarily switch the Pi to use your home Wi-Fi via
> `sudo raspi-config` -> System Options -> Wireless LAN. Either is fine —
> the goal is just `pip install` access, once.

---

## 2. Install system packages

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git
```

Verify the Python version is **3.10 or newer**:

```bash
python3 --version
```

Pi OS Bookworm ships 3.11; Bullseye ships 3.9 (which won't work — upgrade to Bookworm).

---

## 3. Clone the repo

Pick a directory you'll remember. The home directory is fine:

```bash
cd ~
git clone <YOUR_REPO_URL> game
cd game
```

---

## 4. Create the virtualenv and install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

This pulls a small set of pure-Python packages (FastAPI, Uvicorn, Jinja2,
Markdown, etc.). It should take well under a minute on a Pi 4/5.

---

## 5. Smoke test it locally on the Pi

Still inside the venv:

```bash
python run.py
```

You should see:

```text
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
```

From the Pi itself, in another terminal (or via SSH session #2):

```bash
curl -i http://localhost:8000/
```

You should get a `303 See Other` redirect to `/join`. Stop the server with
`Ctrl-C` once that works.

---

## 6. Reach it from a laptop

1. On your laptop, **disconnect from your home Wi-Fi**.
2. Connect to the Pi's AP (e.g. `Offline_Coding_Contest`).
3. Open `http://10.3.141.1:8000` in any browser.

You should land on the "Enter your name" page. If yes — the app is
production-ready as-is. If you only see the page from `localhost` on the
Pi but not from your laptop, see [Troubleshooting](#troubleshooting).

---

## 7. Go air-gapped

Now disconnect the Ethernet cable (or turn off the upstream Wi-Fi route
in RaspAP). The AP keeps working, the judge keeps working, and AI tools
and Google are unreachable for participants. Verify by trying to load
`https://google.com` from a participant laptop — it should fail.

---

## 8. Auto-start on boot (recommended)

So you don't have to SSH in and run `python run.py` every event.

Create the unit file:

```bash
sudo nano /etc/systemd/system/judge.service
```

Paste this (adjust the `User` and paths if you used a different account
or location):

```ini
[Unit]
Description=Coding Judge
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/game
Environment="JUDGE_SECRET=change-me-anything-stable"
ExecStart=/home/pi/game/.venv/bin/python /home/pi/game/run.py
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
```

Enable + start it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now judge
sudo systemctl status judge
```

You should see `active (running)`. Now `sudo reboot` and confirm the app
comes back up by itself, reachable at `http://10.3.141.1:8000`.

Useful commands:

```bash
sudo systemctl restart judge        # after editing problems
sudo journalctl -u judge -f         # tail the logs
```

> **Why `JUDGE_SECRET`?** It signs the session cookie that remembers
> which participant you are. Setting it in the service file makes it
> survive reboots — otherwise everyone gets logged out on restart,
> which mid-contest is annoying. Use any random string.

---

## 9. Day-of-event checklist

Before the room fills up:

- [ ] Pi powered on and booted (give it ~30s).
- [ ] Connect a laptop to the AP and open `http://10.3.141.1:8000`. Page loads.
- [ ] Try to load `google.com` — it must fail.
- [ ] Submit a known-good Python solution to confirm grading still works.
- [ ] Wipe any test data: `sudo systemctl stop judge && rm /home/pi/game/judge.db && sudo systemctl start judge`.
- [ ] Confirm `/leaderboard` is empty.
- [ ] (Optional) Print or project the join URL and Wi-Fi SSID/password for the room.

When the contest starts, each participant joins, types a name, and their
**personal 25-minute timer** starts at that moment.

---

## 10. Adding more problems

Each problem is a folder under `problems/`. Minimum:

```text
problems/03-my-problem/
    meta.json
    statement.md
    tests/
        01.in
        01.out
        02.in
        02.out
        ...
```

`meta.json`:

```json
{
  "id": "03-my-problem",
  "title": "My Problem",
  "time_limit_sec": 2,
  "memory_limit_mb": 128
}
```

After adding files:

```bash
sudo systemctl restart judge
```

Output comparison ignores trailing whitespace per line and trailing blank
lines, so `print(x)` vs `sys.stdout.write(f"{x}\n")` won't accidentally fail.

---

## 11. Updating the code later

```bash
cd ~/game
git pull
source .venv/bin/activate
pip install -r requirements.txt   # only if requirements.txt changed
sudo systemctl restart judge
```

---

## Troubleshooting

### Laptop sees the Wi-Fi but can't load the page

- Wrong port: it's `8000`, not `80`. URL is `http://10.3.141.1:8000`.
- Wrong IP: on the Pi run `hostname -I` and use the `10.3.141.x` address.
- Firewall: RaspAP can be configured to block forwarding. The judge only
  needs LAN traffic to the Pi itself, which is allowed by default.

### `sudo systemctl status judge` shows "failed"

Get the real error:

```bash
sudo journalctl -u judge -n 100 --no-pager
```

Common causes:

| Symptom | Fix |
| --- | --- |
| `ModuleNotFoundError: No module named 'fastapi'` | The venv path in `ExecStart` is wrong. Use the absolute path `/home/pi/game/.venv/bin/python`. |
| `Address already in use` | Another process holds port 8000. `sudo lsof -i :8000` to find it; kill or change the port in `run.py`. |
| `Permission denied` on `judge.db` | The `User=` in the unit file doesn't own the working dir. `sudo chown -R pi:pi /home/pi/game`. |

### A submission hangs forever

It shouldn't — the runner has a wall-clock timeout that kills the whole
process group. If you ever see it hang, that's a bug; check
`journalctl -u judge -f` for stack traces and please report.

### `RE` for everything

The Pi is running Python with `-I -S` (isolated mode), so submissions
**cannot** import third-party packages. That's intentional: they should
only need the standard library. If a problem genuinely requires a
package, that's a problem-design issue, not a bug.

### Wrong time on the Pi (timer drifts)

Without internet, the Pi can lose its clock across reboots. The
**relative** timer still works (it uses the OS monotonic clock from the
moment a participant joins), so the 25-minute window is unaffected.
You only see absolute timestamps in the leaderboard's tiebreak ordering,
which doesn't matter functionally.

---

## Quick reference

| Action | Command |
| --- | --- |
| Start | `sudo systemctl start judge` |
| Stop | `sudo systemctl stop judge` |
| Restart (after editing problems) | `sudo systemctl restart judge` |
| Logs | `sudo journalctl -u judge -f` |
| Reset all submissions | `sudo systemctl stop judge && rm ~/game/judge.db && sudo systemctl start judge` |
| Pi's IP on the AP | `hostname -I` (look for `10.3.141.x`) |
