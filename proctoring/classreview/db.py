"""SQLite schema of the T03 review store (own database file; not a shared model).

WAL + synchronous=FULL; versioned by PRAGMA user_version; each migration in one transaction.
Teacher decisions are append-only (UPDATE/DELETE rejected by triggers); stored clips are immutable.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA_VERSION = 1

_V1 = """
CREATE TABLE class_sessions (
    class_session_id TEXT PRIMARY KEY,
    started_at       TEXT NOT NULL,
    started_at_us    INTEGER NOT NULL
);

CREATE TABLE incidents (
    student_id       TEXT NOT NULL,
    incident_id      TEXT NOT NULL,
    class_session_id TEXT NOT NULL,
    last_seq         INTEGER NOT NULL,
    rule_id          TEXT NOT NULL,
    category         TEXT NOT NULL,
    priority         TEXT NOT NULL,
    state            TEXT NOT NULL,
    t_start_wall     TEXT NOT NULL,
    t_start_us       INTEGER NOT NULL,
    duration_ms      REAL NOT NULL,
    explanation_ru   TEXT NOT NULL,
    clip_available   INTEGER NOT NULL,
    snapshot_file    TEXT,
    first_seen_at    TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    PRIMARY KEY (student_id, incident_id)
);
CREATE INDEX incidents_time ON incidents(student_id, t_start_us);
CREATE INDEX incidents_id ON incidents(incident_id);

CREATE TABLE seen_seq (
    student_id  TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    incident_id TEXT NOT NULL,
    received_at TEXT NOT NULL,
    PRIMARY KEY (student_id, seq)
);

CREATE TABLE clip_requests (
    request_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id    TEXT NOT NULL,
    incident_id   TEXT NOT NULL,
    command_id    TEXT,
    status        TEXT NOT NULL,          -- pending | failed | fulfilled
    reason_code   TEXT,
    reason_ru     TEXT,
    requested_at  TEXT NOT NULL,
    requested_at_us INTEGER NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX clip_requests_incident ON clip_requests(student_id, incident_id, request_id);
CREATE INDEX clip_requests_command ON clip_requests(command_id);

CREATE TABLE clips (
    student_id   TEXT NOT NULL,
    incident_id  TEXT NOT NULL,
    file_name    TEXT NOT NULL,
    media_type   TEXT NOT NULL,
    container    TEXT NOT NULL,
    codec        TEXT,
    duration_s   REAL,
    faststart    INTEGER,
    size_bytes   INTEGER NOT NULL,
    sha256       TEXT NOT NULL,
    source       TEXT NOT NULL,
    uploaded_at  TEXT NOT NULL,
    PRIMARY KEY (student_id, incident_id)
);
CREATE INDEX clips_incident ON clips(incident_id);
CREATE TRIGGER clips_immutable BEFORE UPDATE ON clips
BEGIN
    SELECT RAISE(ABORT, 'stored clips are immutable');
END;

CREATE TABLE decisions (
    decision_id   TEXT PRIMARY KEY,
    student_id    TEXT NOT NULL,
    incident_id   TEXT NOT NULL,
    seq           INTEGER NOT NULL,
    decision      TEXT NOT NULL,
    note_ru       TEXT NOT NULL,
    operator      TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    created_at_us INTEGER NOT NULL,
    supersedes    TEXT,
    UNIQUE (student_id, incident_id, seq)
);
CREATE TRIGGER decisions_no_update BEFORE UPDATE ON decisions
BEGIN
    SELECT RAISE(ABORT, 'decisions are append-only');
END;
CREATE TRIGGER decisions_no_delete BEFORE DELETE ON decisions
BEGIN
    SELECT RAISE(ABORT, 'decisions are append-only');
END;
"""

MIGRATIONS = {1: _V1}


class SchemaTooNew(Exception):
    pass


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=10.0, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    else:
        conn.execute("COMMIT")


def migrate(conn: sqlite3.Connection) -> int:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current > SCHEMA_VERSION:
        raise SchemaTooNew(f"review database schema v{current} is newer than this server (v{SCHEMA_VERSION})")
    for version in range(current + 1, SCHEMA_VERSION + 1):
        with transaction(conn):
            buf: list[str] = []
            for line in MIGRATIONS[version].splitlines():
                buf.append(line)
                stmt = "\n".join(buf).strip()
                if stmt and sqlite3.complete_statement(stmt):
                    conn.execute(stmt)
                    buf = []
            conn.execute(f"PRAGMA user_version = {int(version)}")
    return conn.execute("PRAGMA user_version").fetchone()[0]
