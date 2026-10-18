"""SQLite storage in WAL mode. Small, synchronous calls behind one lock.

Tables: jobs (snapshot JSON), events (append-only log, the replay source),
decisions (decide() log, the Intuit evaluation data), cache (Jev/LLM answers),
webhook_events (dedupe).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from app.contracts import Event, JobView

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  job_id TEXT PRIMARY KEY,
  idempotency_key TEXT UNIQUE,
  state TEXT NOT NULL,
  snapshot TEXT NOT NULL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  job_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  type TEXT NOT NULL,
  ts TEXT NOT NULL,
  lane_id TEXT,
  data TEXT NOT NULL,
  PRIMARY KEY (job_id, seq)
);
CREATE TABLE IF NOT EXISTS decisions (
  decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT,
  j_id TEXT NOT NULL,
  state_hash TEXT NOT NULL,
  questions TEXT NOT NULL,
  jev_answer TEXT,
  jev_source TEXT,
  jev_latency_ms REAL,
  jev_tokens INTEGER,
  band TEXT NOT NULL,
  escalated INTEGER NOT NULL,
  step5_answer TEXT,
  step5_latency_ms REAL,
  final TEXT,
  label TEXT,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS cache (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS webhook_events (
  provider TEXT NOT NULL,
  event_id TEXT NOT NULL,
  received_at REAL NOT NULL,
  PRIMARY KEY (provider, event_id)
);
"""


def stable_hash(*parts: Any) -> str:
    raw = json.dumps(parts, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------ jobs

    def job_for_idempotency_key(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT job_id FROM jobs WHERE idempotency_key = ?", (key,)
            ).fetchone()
        return row["job_id"] if row else None

    def save_job(self, view: JobView, idempotency_key: str | None = None) -> None:
        now = time.time()
        snapshot = view.model_dump_json(by_alias=True)
        with self._lock:
            self._conn.execute(
                """INSERT INTO jobs (job_id, idempotency_key, state, snapshot, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(job_id) DO UPDATE SET state = excluded.state,
                     snapshot = excluded.snapshot, updated_at = excluded.updated_at""",
                (view.job_id, idempotency_key, view.state.value, snapshot, now, now),
            )

    def release_idempotency_key(self, job_id: str) -> None:
        """Free the key of a job that no process runs, so a new job can take it."""
        with self._lock:
            self._conn.execute("UPDATE jobs SET idempotency_key = NULL WHERE job_id = ?", (job_id,))

    def load_job(self, job_id: str) -> JobView | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT snapshot FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return JobView.model_validate_json(row["snapshot"]) if row else None

    def list_jobs(self, limit: int = 50) -> list[JobView]:
        """The newest jobs first, for the Orders screen."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT snapshot FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [JobView.model_validate_json(r["snapshot"]) for r in rows]

    # ------------------------------------------------------------ events

    def append_event(self, job_id: str, type_: str, ts: str, lane_id: str | None, data: dict) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS next FROM events WHERE job_id = ?", (job_id,)
            ).fetchone()
            seq = int(row["next"])
            self._conn.execute(
                "INSERT INTO events (job_id, seq, type, ts, lane_id, data) VALUES (?, ?, ?, ?, ?, ?)",
                (job_id, seq, type_, ts, lane_id, json.dumps(data, default=str)),
            )
        return seq

    def events_after(self, job_id: str, after_seq: int, limit: int = 500) -> list[Event]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE job_id = ? AND seq > ? ORDER BY seq LIMIT ?",
                (job_id, after_seq, limit),
            ).fetchall()
        return [
            Event(
                seq=r["seq"],
                job_id=r["job_id"],
                type=r["type"],
                ts=r["ts"],
                lane_id=r["lane_id"],
                data=json.loads(r["data"]),
            )
            for r in rows
        ]

    # ------------------------------------------------------------ cache

    def cache_get(self, key: str) -> Any | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM cache WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else None

    def cache_set(self, key: str, value: Any) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO cache (key, value, created_at) VALUES (?, ?, ?)",
                (key, json.dumps(value, default=str), time.time()),
            )

    def cache_age_s(self, key: str) -> float | None:
        with self._lock:
            row = self._conn.execute("SELECT created_at FROM cache WHERE key = ?", (key,)).fetchone()
        return time.time() - row["created_at"] if row else None

    # ------------------------------------------------------------ decisions

    def log_decision(self, **fields: Any) -> None:
        cols = list(fields)
        values = [
            json.dumps(v, default=str) if isinstance(v, dict | list) else v for v in fields.values()
        ]
        with self._lock:
            self._conn.execute(
                f"INSERT INTO decisions ({', '.join(cols)}, created_at) "
                f"VALUES ({', '.join('?' for _ in cols)}, ?)",
                (*values, time.time()),
            )

    def decisions(self, j_id: str | None = None) -> list[dict]:
        sql = "SELECT * FROM decisions"
        args: tuple = ()
        if j_id:
            sql += " WHERE j_id = ?"
            args = (j_id,)
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, args).fetchall()]

    # ------------------------------------------------------------ webhooks

    def first_time_webhook(self, provider: str, event_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO webhook_events (provider, event_id, received_at) VALUES (?, ?, ?)",
                (provider, event_id, time.time()),
            )
        return cur.rowcount == 1
