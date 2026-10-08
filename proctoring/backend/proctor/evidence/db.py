"""SQLite schema, versioned migrations and transaction helper (owner: A08).

* PRAGMA user_version = applied schema version. A database newer than this code is never touched.
* Each migration runs in one IMMEDIATE transaction (all-or-nothing).
* WAL journal + synchronous=FULL: a crash or kill never leaves a half-applied write.
* reviews are append-only (UPDATE is rejected by a trigger); rows are deleted only together
  with their session.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

SCHEMA_VERSION = 1

_V1 = """
CREATE TABLE sessions (
    session_id     TEXT PRIMARY KEY,
    created_at_us  INTEGER NOT NULL,
    state          TEXT NOT NULL,
    source_mode    TEXT NOT NULL,
    retain_media   INTEGER NOT NULL,
    media_token    TEXT NOT NULL UNIQUE,
    info_json      TEXT NOT NULL,
    recovered      INTEGER NOT NULL DEFAULT 0,
    updated_at_us  INTEGER NOT NULL
);
CREATE INDEX sessions_created ON sessions(created_at_us);

CREATE TABLE session_events (
    session_id     TEXT NOT NULL,
    seq            INTEGER NOT NULL,
    state          TEXT NOT NULL,
    t_session_ms   REAL NOT NULL,
    paused_total_ms REAL NOT NULL,
    at             TEXT NOT NULL,
    PRIMARY KEY (session_id, seq)
);

CREATE TABLE observations (
    session_id     TEXT NOT NULL,
    observation_id TEXT NOT NULL,
    kind           TEXT NOT NULL,
    t_session_ms   REAL NOT NULL,
    frame_id       INTEGER,
    body_json      TEXT NOT NULL,
    PRIMARY KEY (session_id, observation_id)
);
CREATE INDEX observations_time ON observations(session_id, t_session_ms);

CREATE TABLE incidents (
    session_id     TEXT NOT NULL,
    incident_id    TEXT NOT NULL,
    update_seq     INTEGER NOT NULL,
    state          TEXT NOT NULL,
    rule_id        TEXT NOT NULL,
    t_start_ms     REAL NOT NULL,
    body_json      TEXT NOT NULL,
    PRIMARY KEY (session_id, incident_id)
);
CREATE INDEX incidents_time ON incidents(session_id, t_start_ms);

CREATE TABLE incident_changes (
    session_id     TEXT NOT NULL,
    incident_id    TEXT NOT NULL,
    update_seq     INTEGER NOT NULL,
    change         TEXT NOT NULL,
    body_sha256    TEXT NOT NULL,
    received_at    TEXT NOT NULL,
    PRIMARY KEY (session_id, incident_id, update_seq)
);

CREATE TABLE incident_observations (
    session_id     TEXT NOT NULL,
    incident_id    TEXT NOT NULL,
    observation_id TEXT NOT NULL,
    PRIMARY KEY (session_id, incident_id, observation_id)
);

CREATE TABLE reviews (
    session_id     TEXT NOT NULL,
    review_id      TEXT NOT NULL,
    incident_id    TEXT NOT NULL,
    seq            INTEGER NOT NULL,
    decision       TEXT NOT NULL,
    comment        TEXT NOT NULL,
    operator       TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    created_at_us  INTEGER NOT NULL,
    supersedes_review_id TEXT,
    PRIMARY KEY (session_id, review_id),
    UNIQUE (session_id, incident_id, seq)
);
CREATE TRIGGER reviews_append_only BEFORE UPDATE ON reviews
BEGIN
    SELECT RAISE(ABORT, 'reviews are append-only');
END;

CREATE TABLE answers (
    session_id     TEXT NOT NULL,
    question_id    TEXT NOT NULL,
    value_json     TEXT NOT NULL,
    client_seq     INTEGER NOT NULL,
    saved_at       TEXT NOT NULL,
    PRIMARY KEY (session_id, question_id)
);

CREATE TABLE evidence (
    session_id     TEXT NOT NULL,
    evidence_id    TEXT NOT NULL,
    incident_id    TEXT,
    kind           TEXT NOT NULL,
    frame_id       INTEGER,
    t_session_ms   REAL NOT NULL,
    media_type     TEXT NOT NULL,
    sha256         TEXT NOT NULL,
    size_bytes     INTEGER NOT NULL,
    created_at     TEXT NOT NULL,
    created_at_us  INTEGER NOT NULL,
    file_name      TEXT NOT NULL,
    purged_at      TEXT,
    purge_reason   TEXT,
    PRIMARY KEY (session_id, evidence_id)
);
CREATE INDEX evidence_incident ON evidence(session_id, incident_id);
CREATE INDEX evidence_created ON evidence(created_at_us);

CREATE TABLE coverage_segments (
    session_id     TEXT NOT NULL,
    component      TEXT NOT NULL,
    seg_no         INTEGER NOT NULL,
    cls            TEXT NOT NULL,
    t_start_ms     REAL NOT NULL,
    t_end_ms       REAL NOT NULL,
    n              INTEGER NOT NULL,
    PRIMARY KEY (session_id, component, seg_no)
);

CREATE TABLE producers (
    session_id     TEXT NOT NULL,
    producer_key   TEXT NOT NULL,
    body_json      TEXT NOT NULL,
    PRIMARY KEY (session_id, producer_key)
);

CREATE TABLE counters (
    session_id     TEXT NOT NULL,
    name           TEXT NOT NULL,
    value          INTEGER NOT NULL,
    PRIMARY KEY (session_id, name)
);

CREATE TABLE deleted_sessions (
    session_id     TEXT PRIMARY KEY,
    deleted_at     TEXT NOT NULL
);

CREATE TABLE pending_media_deletions (
    media_token    TEXT PRIMARY KEY,
    requested_at   TEXT NOT NULL
);
"""

MIGRATIONS: dict[int, str] = {1: _V1}

# Tables that hold per-session rows (deleted together with the session).
SESSION_TABLES = (
    "session_events",
    "observations",
    "incidents",
    "incident_changes",
    "incident_observations",
    "reviews",
    "answers",
    "evidence",
    "coverage_segments",
    "producers",
    "counters",
    "sessions",
)


class SchemaTooNewError(Exception):
    pass


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=10.0, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE ... COMMIT; ROLLBACK on any exception (re-raised)."""
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


def migrate(conn: sqlite3.Connection, on_step: Callable[[int], None] | None = None) -> int:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current > SCHEMA_VERSION:
        raise SchemaTooNewError(f"database schema v{current} is newer than this backend (v{SCHEMA_VERSION})")
    for version in range(current + 1, SCHEMA_VERSION + 1):
        script = MIGRATIONS[version]
        with transaction(conn):
            for statement in _split(script):
                conn.execute(statement)
            if on_step is not None:
                on_step(version)
            conn.execute(f"PRAGMA user_version = {int(version)}")
    return conn.execute("PRAGMA user_version").fetchone()[0]


def _split(script: str) -> list[str]:
    """Split a migration script into statements (triggers contain ';' inside BEGIN..END)."""
    statements: list[str] = []
    buf: list[str] = []
    for line in script.splitlines():
        buf.append(line)
        candidate = "\n".join(buf).strip()
        if candidate and sqlite3.complete_statement(candidate):
            statements.append(candidate)
            buf = []
    tail = "\n".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements
