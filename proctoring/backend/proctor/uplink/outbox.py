"""Persistent outbox for `incident` and `ack` messages (qorgau.class.v1 §3.1, owner: C2).

SQLite in ``<data_dir>/class_uplink/outbox.sqlite``. Every message gets a client ``seq`` (from 1, never
reused, survives restarts). Messages are removed only after a successful ``send`` on a live connection;
after a reconnect they are re-sent in ``seq`` order (the server drops duplicates by (student_id, seq)).
At most ``max_items`` are kept: the oldest are dropped first and counted (``dropped``).
Also stores the resume token (``meta``) — never logged.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any


class Outbox:
    def __init__(self, path: Path, max_items: int = 1000):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.max_items = max_items
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("CREATE TABLE IF NOT EXISTS outbox (seq INTEGER PRIMARY KEY, body TEXT NOT NULL)")
        self._db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self.dropped = 0

    # ------------------------------------------------------------------ meta
    def get_meta(self, key: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str | None) -> None:
        with self._lock:
            if value is None:
                self._db.execute("DELETE FROM meta WHERE key=?", (key,))
            else:
                self._db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (key, value))

    # ------------------------------------------------------------------ queue
    def put(self, message: dict[str, Any]) -> int:
        """Assign the next seq, persist, return seq. ``message`` gets the ``seq`` field."""
        with self._lock:
            last = int(self._get_meta_locked("last_seq") or 0)
            seq = last + 1
            body = dict(message, seq=seq)
            self._db.execute("BEGIN")
            try:
                self._db.execute("INSERT INTO outbox(seq, body) VALUES(?, ?)", (seq, json.dumps(body, ensure_ascii=False)))
                self._db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('last_seq', ?)", (str(seq),))
                n = self._db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
                if n > self.max_items:
                    cut = n - self.max_items
                    self._db.execute("DELETE FROM outbox WHERE seq IN (SELECT seq FROM outbox ORDER BY seq LIMIT ?)", (cut,))
                    self.dropped += cut
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise
            return seq

    def pending(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute("SELECT body FROM outbox ORDER BY seq LIMIT ?", (limit,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def done(self, seq: int) -> None:
        with self._lock:
            self._db.execute("DELETE FROM outbox WHERE seq=?", (seq,))

    def __len__(self) -> int:
        with self._lock:
            return int(self._db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0])

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _get_meta_locked(self, key: str) -> str | None:
        row = self._db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None
