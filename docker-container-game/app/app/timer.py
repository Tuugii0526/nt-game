"""Per-participant rolling 25-minute window from first login."""
from __future__ import annotations

import time

CONTEST_DURATION_SEC = 25 * 60


def seconds_left(joined_at: int, now: int | None = None) -> int:
    now_ts = int(time.time()) if now is None else now
    return max(0, CONTEST_DURATION_SEC - (now_ts - joined_at))
