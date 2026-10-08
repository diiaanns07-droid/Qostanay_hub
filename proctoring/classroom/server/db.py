"""SQLite persistence + migration runner (owner: T01; the mechanism is shared, migrations are per owner).

* One database file `<data_dir>/classroom.sqlite3` (WAL). Thread-safe through one lock (small writes only).
* A Migration is (id, owner, sql). Ids are namespaced by owner: "t01_0001_core", "t03_0001_reviews", ...
  Applied ids + a checksum of their SQL are recorded in `schema_migrations`. Editing an applied migration is
  refused at start (checksum mismatch) instead of silently diverging; add a new migration instead.
* Feature tables must be prefixed with the owner id ("t03_", "t04_", "t05_"). Core tables have no prefix.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from ..contracts.models import utc_now

MIGRATION_ID = re.compile(r"^(t0[1-5])_[0-9]{4}_[a-z0-9_]{1,48}$")


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    id: str
    owner: str  # "T01".."T05"
    sql: str

    def __post_init__(self) -> None:
        m = MIGRATION_ID.match(self.id)
        if not m:
            raise MigrationError(f"migration id {self.id!r} must look like t0N_0001_name")
        if m.group(1) != self.owner.lower():
            raise MigrationError(f"migration {self.id} is namespaced {m.group(1)!r} but owner is {self.owner!r}")

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.strip().encode("utf-8")).hexdigest()


CORE_MIGRATIONS = [
    Migration(
        "t01_0001_core",
        "T01",
        """
        CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY, title TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL,
            closed_at TEXT, exam_json TEXT NOT NULL, join_code TEXT
        );
        CREATE TABLE students (
            student_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(session_id),
            token_hash TEXT NOT NULL UNIQUE, student_label TEXT NOT NULL, computer_name TEXT NOT NULL,
            app_version TEXT NOT NULL, origin TEXT NOT NULL, paired_at TEXT NOT NULL, paired_ip TEXT NOT NULL,
            last_seen_at TEXT, reconnects INTEGER NOT NULL DEFAULT 0, capabilities_json TEXT NOT NULL DEFAULT '[]',
            client_run_id TEXT
        );
        CREATE TABLE device_status (student_id TEXT PRIMARY KEY REFERENCES students(student_id), json TEXT NOT NULL);
        CREATE TABLE events (
            student_id TEXT NOT NULL REFERENCES students(student_id), event_id TEXT NOT NULL,
            session_id TEXT NOT NULL, kind TEXT NOT NULL, seq INTEGER, client_run_id TEXT NOT NULL DEFAULT '',
            received_at TEXT NOT NULL, json TEXT NOT NULL, PRIMARY KEY (student_id, event_id)
        );
        CREATE TABLE event_seq (
            student_id TEXT NOT NULL, client_run_id TEXT NOT NULL, seq INTEGER NOT NULL, event_id TEXT NOT NULL,
            PRIMARY KEY (student_id, client_run_id, seq)
        );
        CREATE TABLE incidents (
            student_id TEXT NOT NULL REFERENCES students(student_id), incident_id TEXT NOT NULL, json TEXT NOT NULL,
            PRIMARY KEY (student_id, incident_id)
        );
        CREATE TABLE commands (
            command_id TEXT PRIMARY KEY, student_id TEXT NOT NULL REFERENCES students(student_id),
            status TEXT NOT NULL, json TEXT NOT NULL
        );
        CREATE INDEX commands_by_student ON commands(student_id);
        CREATE TABLE audio_sessions (
            audio_session_id TEXT PRIMARY KEY, student_id TEXT NOT NULL REFERENCES students(student_id), json TEXT NOT NULL
        );
        """,
    ),
]


class Database:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (id TEXT PRIMARY KEY, owner TEXT NOT NULL, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)"
            )

    def migrate(self, migrations: Iterable[Migration]) -> list[str]:
        """Apply missing migrations in the given order; returns the ids applied now."""
        applied_now: list[str] = []
        with self._lock:
            done = {r["id"]: r["checksum"] for r in self._conn.execute("SELECT id, checksum FROM schema_migrations")}
            seen: set[str] = set()
            for mig in migrations:
                if mig.id in seen:
                    raise MigrationError(f"migration {mig.id} listed twice")
                seen.add(mig.id)
                if mig.id in done:
                    if done[mig.id] != mig.checksum:
                        raise MigrationError(f"applied migration {mig.id} was edited (checksum mismatch); add a new migration")
                    continue
                if mig.owner != "T01" and not _tables_prefixed(mig.sql, mig.owner.lower() + "_"):
                    raise MigrationError(f"migration {mig.id}: tables of {mig.owner} must be prefixed {mig.owner.lower()}_")
                try:
                    self._conn.execute("BEGIN")
                    for stmt in _split_sql(mig.sql):
                        self._conn.execute(stmt)
                    self._conn.execute(
                        "INSERT INTO schema_migrations(id, owner, checksum, applied_at) VALUES (?,?,?,?)",
                        (mig.id, mig.owner, mig.checksum, utc_now().isoformat()),
                    )
                    self._conn.execute("COMMIT")
                except Exception:
                    self._conn.execute("ROLLBACK")
                    raise
                applied_now.append(mig.id)
        return applied_now

    def applied(self) -> list[str]:
        with self._lock:
            return [r["id"] for r in self._conn.execute("SELECT id FROM schema_migrations ORDER BY applied_at, id")]

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        with self._lock:
            self._conn.execute(sql, params)

    def executemany(self, sql: str, rows: Iterable[tuple[Any, ...]]) -> None:
        with self._lock:
            self._conn.executemany(sql, rows)

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params))

    def transaction(self) -> "_Tx":
        return _Tx(self)

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class _Tx:
    def __init__(self, db: Database):
        self.db = db

    def __enter__(self) -> sqlite3.Connection:
        self.db._lock.acquire()
        self.db._conn.execute("BEGIN")
        return self.db._conn

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self.db._conn.execute("ROLLBACK" if exc_type else "COMMIT")
        finally:
            self.db._lock.release()


def _split_sql(sql: str) -> list[str]:
    return [s.strip() for s in sql.split(";") if s.strip()]


_TABLE_RE = re.compile(r"\b(?:CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?|ALTER\s+TABLE|CREATE\s+(?:UNIQUE\s+)?INDEX(?:\s+IF\s+NOT\s+EXISTS)?\s+\S+\s+ON)\s+([A-Za-z_][A-Za-z0-9_]*)", re.I)


def _tables_prefixed(sql: str, prefix: str) -> bool:
    names = _TABLE_RE.findall(sql)
    return all(n.lower().startswith(prefix) for n in names)
