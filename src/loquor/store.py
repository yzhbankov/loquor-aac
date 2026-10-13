"""Persistent task state in SQLite.

Both sides of a collaboration persist task state so that an interrupted
exchange can resume after reconnection without losing progress or producing
duplicate results:

- The *requesting* agent keeps its outbound tasks and marks them
  ``interrupted`` when the peer becomes unreachable, so it can retry later
  with the same ``task_id``.
- The *serving* agent keeps per-task progress (collected evidence, final
  result). A retried ``task_request`` with a known ``task_id`` is answered
  idempotently from the store instead of redoing the work.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any

from loquor.messages import TaskState

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id    TEXT PRIMARY KEY,
    role       TEXT NOT NULL,            -- 'requester' or 'server'
    peer_id    TEXT,
    capability TEXT,
    state      TEXT NOT NULL,
    data       TEXT NOT NULL DEFAULT '{}',  -- JSON: observations, evidence, result
    updated_at REAL NOT NULL
);
"""


class TaskStore:
    """Thread-safe SQLite-backed task state. Use ':memory:' for tests."""

    def __init__(self, path: str):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def upsert(
        self,
        task_id: str,
        *,
        role: str,
        state: TaskState,
        peer_id: str | None = None,
        capability: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        existing = self.get(task_id)
        merged = dict(existing["data"]) if existing else {}
        if data:
            merged.update(data)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO tasks (task_id, role, peer_id, capability, state, data, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    state = excluded.state,
                    peer_id = COALESCE(excluded.peer_id, tasks.peer_id),
                    capability = COALESCE(excluded.capability, tasks.capability),
                    data = excluded.data,
                    updated_at = excluded.updated_at
                """,
                (
                    task_id,
                    role,
                    peer_id,
                    capability,
                    state.value,
                    json.dumps(merged),
                    time.time(),
                ),
            )
            self._conn.commit()

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        if row is None:
            return None
        record = dict(row)
        record["data"] = json.loads(record["data"])
        record["state"] = TaskState(record["state"])
        return record

    def by_state(self, role: str, *states: TaskState) -> list[dict[str, Any]]:
        placeholders = ",".join("?" for _ in states)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM tasks WHERE role = ? AND state IN ({placeholders})"
                " ORDER BY updated_at",
                (role, *[s.value for s in states]),
            ).fetchall()
        out = []
        for row in rows:
            record = dict(row)
            record["data"] = json.loads(record["data"])
            record["state"] = TaskState(record["state"])
            out.append(record)
        return out
