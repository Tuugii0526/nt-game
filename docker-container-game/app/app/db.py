"""SQLite persistence: schema + thin query helpers.

One connection per request via `get_conn` (FastAPI dependency).
WAL mode lets the grader thread read while a request thread writes.
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Iterable

DB_PATH = Path(
    os.environ.get(
        "JUDGE_DB_PATH",
        str(Path(__file__).resolve().parent.parent / "judge.db"),
    )
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS participants (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT    NOT NULL UNIQUE,
    joined_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS submissions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    participant_id INTEGER NOT NULL REFERENCES participants(id),
    problem_id     TEXT    NOT NULL,
    code           TEXT    NOT NULL,
    verdict        TEXT    NOT NULL,
    tests_passed   INTEGER NOT NULL,
    tests_total    INTEGER NOT NULL,
    runtime_ms     INTEGER NOT NULL,
    created_at     INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_submissions_participant ON submissions(participant_id);
CREATE INDEX IF NOT EXISTS idx_submissions_problem     ON submissions(problem_id);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript(_SCHEMA)


def get_conn() -> Iterable[sqlite3.Connection]:
    """FastAPI dependency: yields a per-request connection, then closes."""
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


# ---- participants ---------------------------------------------------------

def get_or_create_participant(conn: sqlite3.Connection, name: str) -> sqlite3.Row:
    name = name.strip()
    row = conn.execute("SELECT * FROM participants WHERE name = ?", (name,)).fetchone()
    if row is not None:
        return row
    conn.execute(
        "INSERT INTO participants (name, joined_at) VALUES (?, ?)",
        (name, int(time.time())),
    )
    conn.commit()
    return conn.execute("SELECT * FROM participants WHERE name = ?", (name,)).fetchone()


def get_participant(conn: sqlite3.Connection, pid: int | None) -> sqlite3.Row | None:
    if pid is None:
        return None
    return conn.execute("SELECT * FROM participants WHERE id = ?", (pid,)).fetchone()


# ---- submissions ----------------------------------------------------------

def insert_submission(
    conn: sqlite3.Connection,
    participant_id: int,
    problem_id: str,
    code: str,
    verdict: str,
    tests_passed: int,
    tests_total: int,
    runtime_ms: int,
) -> None:
    conn.execute(
        """INSERT INTO submissions
              (participant_id, problem_id, code, verdict,
               tests_passed, tests_total, runtime_ms, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            participant_id, problem_id, code, verdict,
            tests_passed, tests_total, runtime_ms, int(time.time()),
        ),
    )
    conn.commit()


def submissions_for(
    conn: sqlite3.Connection,
    participant_id: int,
    problem_id: str | None = None,
) -> list[sqlite3.Row]:
    if problem_id is None:
        return list(conn.execute(
            "SELECT * FROM submissions WHERE participant_id = ? ORDER BY created_at DESC",
            (participant_id,),
        ))
    return list(conn.execute(
        """SELECT * FROM submissions
           WHERE participant_id = ? AND problem_id = ?
           ORDER BY created_at DESC""",
        (participant_id, problem_id),
    ))


def best_score_per_problem(conn: sqlite3.Connection, participant_id: int) -> dict[str, int]:
    return {
        row["problem_id"]: row["best"]
        for row in conn.execute(
            """SELECT problem_id, MAX(tests_passed) AS best
               FROM submissions WHERE participant_id = ?
               GROUP BY problem_id""",
            (participant_id,),
        )
    }


def leaderboard(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute(
        """WITH best_per_problem AS (
              SELECT participant_id, problem_id, MAX(tests_passed) AS best
              FROM submissions
              GROUP BY participant_id, problem_id
           ),
           totals AS (
              SELECT participant_id, SUM(best) AS total
              FROM best_per_problem
              GROUP BY participant_id
           ),
           last_ac AS (
              SELECT participant_id, MAX(created_at) AS last_ac_at
              FROM submissions WHERE verdict = 'AC'
              GROUP BY participant_id
           )
           SELECT p.id, p.name,
                  COALESCE(t.total, 0)      AS total,
                  COALESCE(la.last_ac_at, 0) AS last_ac_at,
                  p.joined_at
             FROM participants p
        LEFT JOIN totals  t  ON t.participant_id  = p.id
        LEFT JOIN last_ac la ON la.participant_id = p.id
         ORDER BY total DESC, last_ac_at ASC, p.joined_at ASC"""
    ))
