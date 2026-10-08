"""SQLite EvidenceStore (owner: A08).

Thread model: one SQLite connection guarded by one RLock (record_* from the fusion thread,
router handlers from FastAPI worker threads). Media operations of a session (snapshot write,
export read, delete) are additionally serialized by a per-session lock. Lock order is always
session lock -> DB lock.

Write policy:
  * record_*() never raise: a storage failure is counted, logged without data, and visible in
    health() and in the session report (fusion must keep running);
  * router-facing methods raise ProctorError subclasses (StorageError -> 503 STORAGE_ERROR);
  * a session accepts records only while non-terminal; after finish/abort nothing is appended;
  * a deleted session leaves a tombstone so late writes cannot resurrect it;
  * re-delivery is idempotent: observations by (session_id, observation_id), incident changes
    by (session_id, incident_id, update_seq), answers last-writer-wins by client_seq.
Privacy: metadata by default. CV observations are kept in a bounded in-memory buffer and only
those referenced by an incident are persisted (environment/health observations always are).
Media only when SessionInfo.retain_media is true, with size/count/TTL limits.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import sqlite3
import sys
import threading
from collections import Counter, OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from proctor_contracts.interfaces import (
    BackendContext,
    FramePacket,
    InvalidStateError,
    NotFoundError,
    ProctorError,
    StorageError,
)
from proctor_contracts.v1 import (
    AnswerRecord,
    AnswerUpsert,
    ApiErrorBody,
    Component,
    CoverageGap,
    ErrorCode,
    EvidenceItem,
    EvidenceKind,
    Health,
    HealthObservation,
    HealthStatus,
    HumanReview,
    HumanReviewCreate,
    Incident,
    IncidentChange,
    IncidentDetail,
    IncidentState,
    Observation,
    ObservationStatus,
    ReviewStatus,
    SessionInfo,
    SessionState,
    SourceMode,
    utc_now,
)

from proctor.settings import PROCTORING_ROOT, Settings

from . import db
from .config import EvidenceConfig
from .coverage import (
    COVERED_COMPONENTS,
    DETERMINED,
    CoverageTracker,
    Interval,
    intersect,
    merge,
    subtract,
    total,
)
from .media import JPEG_SOI, MediaPathError, MediaVault, encode_jpeg
from . import review_zones
from .review_zones import SessionSummary, SessionOverviewRow, ZONE_ORDER

log = logging.getLogger("proctor.evidence")

ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
TERMINAL = {SessionState.FINISHED.value, SessionState.ABORTED.value, SessionState.FAILED.value}
DETERMINED_STATUSES = {ObservationStatus.OK, ObservationStatus.DEGRADED}
INTERRUPTED_MESSAGE = (
    "Сессия прервана: локальный сервис был остановлен без завершения сессии (сбой или принудительное "
    "закрытие). Данные сохранены до последней успешной записи."
)


def _us(dt: datetime) -> int:
    return int(dt.timestamp() * 1_000_000)


def _ms_between(start: datetime, end: datetime) -> float:
    return max(0.0, (end - start).total_seconds() * 1000.0)


@dataclass
class _Live:
    """In-memory state of a session known to this process."""

    session_id: str
    state: str
    source_mode: str
    retain_media: bool
    media_token: str
    created_at: datetime
    coverage: CoverageTracker
    ring: "OrderedDict[str, tuple[float, Observation, bool]]" = field(default_factory=OrderedDict)
    producers: set[str] = field(default_factory=set)
    counters: Counter = field(default_factory=Counter)
    last_t: float = 0.0

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL


@dataclass
class EvidenceFile:
    item: EvidenceItem
    data: bytes | None
    status: str  # ok | missing | hash_mismatch | not_embedded | unreadable


@dataclass
class ExportSnapshot:
    """Consistent copy of one session taken under the store locks; rendering happens outside."""

    info: SessionInfo
    summary: SessionSummary
    coverage: dict[str, Any]
    incidents: list[IncidentDetail]
    incidents_total: int
    observations: list[dict[str, Any]]
    observations_total: int
    missing_observation_refs: int
    answers: list[AnswerRecord]
    producers: list[dict[str, Any]]
    counters: dict[str, int]
    evidence: dict[str, EvidenceFile]
    evidence_expired: int
    recovered: bool
    exported_at: datetime
    config_versions: dict[str, str]


class SqliteEvidenceStore:
    """EvidenceStore (proctor_contracts.interfaces.EvidenceStore) on SQLite + media vault."""

    def __init__(self, settings: Settings, config: EvidenceConfig | None = None):
        self.settings = settings
        self.config = config or EvidenceConfig.from_env()
        for name in (self.config.db_file_name, self.config.media_dir_name):
            if not NAME_RE.fullmatch(name) or name in (".", ".."):
                raise ValueError(f"invalid storage name {name!r}")
        self.data_dir = Path(settings.data_dir).expanduser()
        self.db_path = self.data_dir / self.config.db_file_name
        self.vault = MediaVault(self.data_dir / self.config.media_dir_name)
        self._db_lock = threading.RLock()
        self._locks_lock = threading.Lock()
        self._session_locks: dict[str, threading.Lock] = {}
        self._conn: sqlite3.Connection | None = None
        self._lock_fh: Any = None
        self._live: dict[str, _Live] = {}
        self._deleted: set[str] = set()
        self._stats: Counter = Counter()
        self._open_problem: tuple[HealthStatus, str, str] | None = (HealthStatus.STARTING, "not_opened", "")
        self._last_error = ""
        self._schema_version = 0
        self._in_source_tree = False
        # Test hook: called with a point name inside write transactions (fault injection).
        self._fault: Callable[[str], None] | None = None

    # ================================================================ lifecycle
    def open(self) -> Health:
        """Create/migrate the database, recover interrupted sessions, purge expired media.
        Never raises for storage problems: they are reported in the returned Health."""
        with self._db_lock:
            if self._conn is not None:
                return self.health()
            try:
                self.data_dir.mkdir(parents=True, exist_ok=True)
                self._in_source_tree = _is_within(self.data_dir, PROCTORING_ROOT)
                if not self._acquire_process_lock():
                    self._open_problem = (
                        HealthStatus.ERROR,
                        "store_in_use",
                        "Хранилище уже используется другим процессом backend",
                    )
                    return self.health()
                conn = db.connect(self.db_path)
                conn.execute("PRAGMA secure_delete=ON")
                try:
                    self._schema_version = db.migrate(conn)
                except BaseException:
                    conn.close()
                    raise
                self._conn = conn
                self._deleted = {r[0] for r in conn.execute("SELECT session_id FROM deleted_sessions")}
                self._recover_interrupted()
                self.vault.ensure_root()
                self.vault.cleanup_debris()
                self._retry_media_deletions()
                self._remove_orphan_media()
                self.purge_expired_media()
                self._open_problem = None
            except db.SchemaTooNewError as exc:
                self._release_process_lock()
                self._open_problem = (HealthStatus.ERROR, "schema_too_new", str(exc)[:300])
            except (sqlite3.Error, OSError) as exc:
                self._release_process_lock()
                if self._conn is not None:
                    self._conn.close()
                    self._conn = None
                self._open_problem = (HealthStatus.ERROR, "storage_unavailable", _describe(exc))
                log.error("evidence store could not open: %s", _describe(exc))
            return self.health()

    def close(self) -> None:
        with self._db_lock:
            for live in list(self._live.values()):
                if not live.terminal:
                    self._flush_live(live, final=True)
            if self._conn is not None:
                try:
                    self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except sqlite3.Error:
                    pass
                self._conn.close()
                self._conn = None
            self._release_process_lock()
            self._open_problem = (HealthStatus.STOPPED, "closed", "")

    def health(self) -> Health:
        details: dict[str, Any] = {
            "schema_version": self._schema_version,
            "write_errors": int(self._stats["write_errors"]),
            "media_delete_pending": int(self._stats["media_delete_pending"]),
        }
        if self._open_problem is not None:
            status, code, message = self._open_problem
            return Health(component=Component.EVIDENCE, status=status, code=code, message=message[:500], details=details)
        if self._stats["write_errors"]:
            return Health(
                component=Component.EVIDENCE,
                status=HealthStatus.DEGRADED,
                code="storage_write_failed",
                message=f"Ошибки записи хранилища: {self._stats['write_errors']} (последняя: {self._last_error})"[:500],
                details=details,
            )
        if self._in_source_tree:
            return Health(
                component=Component.EVIDENCE,
                status=HealthStatus.DEGRADED,
                code="data_dir_in_source_tree",
                message="Каталог данных находится внутри исходников: перенесите QORGAU_DATA_DIR",
                details=details,
            )
        return Health(component=Component.EVIDENCE, status=HealthStatus.OK, code="ok", message="SQLite evidence store", details=details)

    def create_router(self, context: BackendContext):
        from .router import build_router

        return build_router(self, context)

    def write_error_count(self) -> int:
        """Monotonic failure signal for the composition root; recording remains non-raising."""
        with self._db_lock:
            return int(self._stats["write_errors"])

    # ================================================================ recording (never raises)
    def upsert_session(self, info: SessionInfo) -> None:
        try:
            # re-validate: callers may build it with model_copy(update=...), which skips validation
            self._upsert_session(SessionInfo.model_validate(info.model_dump()))
        except Exception as exc:
            self._write_failed("upsert_session", exc, info.session_id)

    def _upsert_session(self, info: SessionInfo) -> None:
        conn = self._require_conn()
        sid = info.session_id
        state = info.state.value
        now = utc_now()
        with self._db_lock:
            if sid in self._deleted:
                self._stats["writes_for_deleted_session"] += 1
                return
            row = conn.execute(
                "SELECT state, source_mode, media_token FROM sessions WHERE session_id=?", (sid,)
            ).fetchone()
            created_new = row is None
            if row is not None:
                if row["state"] in TERMINAL and state != row["state"]:
                    self._count(sid, "state_change_after_end_rejected")
                    log.warning("session %s: state change after end rejected", sid)
                    return
                if row["source_mode"] != info.source_mode.value:
                    self._count(sid, "source_mode_change_rejected")
                    log.error("session %s: source_mode change rejected", sid)
                    return
            with db.transaction(conn):
                if row is None:
                    token = MediaVault.new_token()
                    conn.execute(
                        "INSERT INTO sessions(session_id, created_at_us, state, source_mode, retain_media, media_token,"
                        " info_json, recovered, updated_at_us) VALUES (?,?,?,?,?,?,?,0,?)",
                        (sid, _us(info.created_at), state, info.source_mode.value, int(info.retain_media), token,
                         info.model_dump_json(), _us(now)),
                    )
                    prev_state = None
                else:
                    token = row["media_token"]
                    prev_state = row["state"]
                    conn.execute(
                        "UPDATE sessions SET state=?, retain_media=?, info_json=?, updated_at_us=? WHERE session_id=?",
                        (state, int(info.retain_media), info.model_dump_json(), _us(now), sid),
                    )
                if prev_state != state:
                    first_start = state == SessionState.RUNNING.value and conn.execute(
                        "SELECT 1 FROM session_events WHERE session_id=? AND state=?", (sid, state)
                    ).fetchone() is None
                    t_ms = self._event_t(info, now, first_start)
                    seq = conn.execute(
                        "SELECT COALESCE(MAX(seq), -1) + 1 FROM session_events WHERE session_id=?", (sid,)
                    ).fetchone()[0]
                    conn.execute(
                        "INSERT INTO session_events(session_id, seq, state, t_session_ms, paused_total_ms, at)"
                        " VALUES (?,?,?,?,?,?)",
                        (sid, seq, state, t_ms, info.paused_total_ms, now.isoformat()),
                    )
            live = self._live.get(sid)
            if live is None:
                live = _Live(
                    session_id=sid,
                    state=state,
                    source_mode=info.source_mode.value,
                    retain_media=info.retain_media,
                    media_token=token,
                    created_at=info.created_at,
                    coverage=self._new_tracker(sid),
                )
                self._live[sid] = live
            live.state = state
            live.retain_media = info.retain_media
            if live.terminal:
                self._flush_live(live, final=True)
        if created_new:
            self.purge_expired_media()

    def record_observation(self, observation: Observation) -> None:
        try:
            self._record_observation(observation)
        except Exception as exc:
            self._write_failed("record_observation", exc, observation.session_id)

    def _record_observation(self, obs: Observation) -> None:
        with self._db_lock:
            live = self._live_for_write(obs.session_id, obs.source_mode)
            if live is None:
                return
            t = obs.t_session_ms
            self._note_producer(live, obs)
            if obs.kind in COVERED_COMPONENTS:
                if obs.observation_id in live.ring:
                    live.counters["duplicate_observations"] += 1
                    return
                live.last_t = max(live.last_t, t)
                live.coverage.observe(obs.kind, obs.status in DETERMINED_STATUSES, t)
                live.ring[obs.observation_id] = (t, obs, False)
                self._evict(live, t)
                last = live.coverage.last_flush_t
                if last is None or t - last >= self.config.coverage_flush_ms:
                    self._flush_live(live)
                return
            body = obs.model_dump_json()
            if len(body) > self.config.max_observation_json_bytes:
                live.counters["oversized_observations"] += 1
                return
            conn = self._require_conn()
            with db.transaction(conn):
                cur = conn.execute(
                    "INSERT OR IGNORE INTO observations(session_id, observation_id, kind, t_session_ms, frame_id, body_json)"
                    " VALUES (?,?,?,?,?,?)",
                    (obs.session_id, obs.observation_id, obs.kind, t, obs.frame_id, body),
                )
            if cur.rowcount == 0:
                live.counters["duplicate_observations"] += 1
            else:
                live.last_t = max(live.last_t, t)

    def record_incident_change(self, change: IncidentChange) -> None:
        try:
            self._record_incident_change(IncidentChange.model_validate(change.model_dump()))
        except Exception as exc:
            self._write_failed("record_incident_change", exc, change.incident.session_id)

    def _record_incident_change(self, change: IncidentChange) -> None:
        inc = change.incident
        sid = inc.session_id
        with self._db_lock:
            live = self._live_for_write(sid, inc.source_mode)
            if live is None:
                return
            conn = self._require_conn()
            body = inc.model_dump_json()
            digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
            persisted: list[str] = []
            with db.transaction(conn):
                seen = conn.execute(
                    "SELECT body_sha256 FROM incident_changes WHERE session_id=? AND incident_id=? AND update_seq=?",
                    (sid, inc.incident_id, inc.update_seq),
                ).fetchone()
                if seen is not None:
                    live.counters["duplicate_incident_changes" if seen[0] == digest else "conflicting_incident_changes"] += 1
                    return
                conn.execute(
                    "INSERT INTO incident_changes(session_id, incident_id, update_seq, change, body_sha256, received_at)"
                    " VALUES (?,?,?,?,?,?)",
                    (sid, inc.incident_id, inc.update_seq, change.change.value, digest, utc_now().isoformat()),
                )
                self._fault_point("incident_change:after_history")
                row = conn.execute(
                    "SELECT update_seq FROM incidents WHERE session_id=? AND incident_id=?", (sid, inc.incident_id)
                ).fetchone()
                if row is None or inc.update_seq > row[0]:
                    conn.execute(
                        "INSERT OR REPLACE INTO incidents(session_id, incident_id, update_seq, state, rule_id, t_start_ms, body_json)"
                        " VALUES (?,?,?,?,?,?,?)",
                        (sid, inc.incident_id, inc.update_seq, inc.state.value, inc.rule_id.value, inc.t_start_ms, body),
                    )
                else:
                    live.counters["stale_incident_changes"] += 1
                self._fault_point("incident_change:after_incident")
                for oid in inc.observation_ids:
                    conn.execute(
                        "INSERT OR IGNORE INTO incident_observations(session_id, incident_id, observation_id) VALUES (?,?,?)",
                        (sid, inc.incident_id, oid),
                    )
                    entry = live.ring.get(oid)
                    if entry is not None and not entry[2]:
                        ref = entry[1]
                        ref_body = ref.model_dump_json()
                        if len(ref_body) <= self.config.max_observation_json_bytes:
                            conn.execute(
                                "INSERT OR IGNORE INTO observations(session_id, observation_id, kind, t_session_ms, frame_id, body_json)"
                                " VALUES (?,?,?,?,?,?)",
                                (sid, oid, ref.kind, ref.t_session_ms, ref.frame_id, ref_body),
                            )
                            persisted.append(oid)
                self._fault_point("incident_change:before_commit")
            for oid in persisted:
                t, ref, _ = live.ring[oid]
                live.ring[oid] = (t, ref, True)

    def capture_snapshot(self, session_id: str, incident_id: str, frame: FramePacket) -> EvidenceItem | None:
        try:
            return self._capture_snapshot(session_id, incident_id, frame)
        except Exception as exc:
            self._write_failed("capture_snapshot", exc, session_id)
            return None

    def _capture_snapshot(self, session_id: str, incident_id: str, frame: FramePacket) -> EvidenceItem | None:
        cfg = self.config
        with self._session_lock(session_id):
            with self._db_lock:
                live = self._live_for_write(session_id, frame.meta.source_mode)
                if live is None or not live.retain_media:
                    return None
                if frame.session_id != session_id:
                    live.counters["snapshot_session_mismatch"] += 1
                    return None
                conn = self._require_conn()
                if conn.execute(
                    "SELECT 1 FROM incidents WHERE session_id=? AND incident_id=?", (session_id, incident_id)
                ).fetchone() is None:
                    live.counters["snapshot_unknown_incident"] += 1
                    return None
                per_incident = conn.execute(
                    "SELECT COUNT(*) FROM evidence WHERE session_id=? AND incident_id=?", (session_id, incident_id)
                ).fetchone()[0]
                n_items, n_bytes = conn.execute(
                    "SELECT COUNT(*), COALESCE(SUM(size_bytes), 0) FROM evidence WHERE session_id=?", (session_id,)
                ).fetchone()
                token = live.media_token
            if per_incident >= cfg.max_snapshots_per_incident or n_items >= cfg.max_media_items_per_session:
                self._count(session_id, "snapshot_skipped_limit")
                return None
            if self.vault.free_bytes() < cfg.min_free_disk_bytes:
                self._count(session_id, "snapshot_skipped_low_disk")
                return None
            data = encode_jpeg(frame.image, cfg.jpeg_quality)
            if data is None:
                self._count(session_id, "snapshot_encoder_unavailable")
                return None
            if len(data) > cfg.max_snapshot_bytes or n_bytes + len(data) > cfg.max_media_bytes_per_session:
                self._count(session_id, "snapshot_skipped_limit")
                return None
            file_name = MediaVault.new_file_name("image/jpeg")
            sha, size = self.vault.write(token, file_name, data)
            created = utc_now()
            item = EvidenceItem(
                evidence_id=f"ev-{secrets.token_hex(12)}",
                session_id=session_id,
                incident_id=incident_id,
                kind=EvidenceKind.SNAPSHOT,
                frame_id=frame.frame_id,
                t_session_ms=frame.t_session_ms,
                media_type="image/jpeg",
                sha256=sha,
                size_bytes=size,
                created_at=created,
            )
            try:
                with self._db_lock:
                    if session_id in self._deleted:
                        raise _Discard()
                    conn = self._require_conn()
                    with db.transaction(conn):
                        conn.execute(
                            "INSERT INTO evidence(session_id, evidence_id, incident_id, kind, frame_id, t_session_ms, media_type,"
                            " sha256, size_bytes, created_at, created_at_us, file_name) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                            (session_id, item.evidence_id, incident_id, item.kind.value, item.frame_id, item.t_session_ms,
                             item.media_type, sha, size, created.isoformat(), _us(created), file_name),
                        )
                        self._fault_point("snapshot:before_commit")
            except BaseException as exc:
                self.vault.remove_file(token, file_name)
                if isinstance(exc, _Discard):
                    return None
                raise
            return item

    # ================================================================ router-facing (raise ProctorError)
    def list_sessions(self) -> list[SessionInfo]:
        with self._read() as conn:
            rows = conn.execute("SELECT info_json FROM sessions ORDER BY created_at_us DESC, session_id DESC").fetchall()
        return [SessionInfo.model_validate_json(r[0]) for r in rows]

    def overview(self) -> list[SessionOverviewRow]:
        """Local sessions only; one DB lock keeps reviews/deletion/counts consistent."""
        rows = []
        with self._read() as conn:
            for session in self.list_sessions():
                summary = self.summary(session.session_id)
                priorities = {p: 0 for p in ("low", "medium", "high")}
                for incident in self.list_incidents(session.session_id):
                    priorities[incident.priority.value] += 1
                rows.append(SessionOverviewRow(
                    session=session, review_zone=summary.review_zone,
                    reasons_ru=summary.review_zone_reasons_ru,
                    incidents_total=summary.incidents_total, incidents_by_priority=priorities,
                    pending_reviews=summary.reviews_by_decision.get("pending", 0),
                ))
        rows.sort(key=lambda r: r.session.session_id, reverse=True)
        rows.sort(key=lambda r: r.session.created_at, reverse=True)
        rows.sort(key=lambda r: ZONE_ORDER[r.review_zone])
        return rows

    def get_session(self, session_id: str) -> SessionInfo | None:
        with self._read() as conn:
            row = conn.execute("SELECT info_json FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        return SessionInfo.model_validate_json(row[0]) if row is not None else None

    def require_session(self, session_id: str) -> SessionInfo:
        info = self.get_session(session_id)
        if info is None:
            raise NotFoundError(ErrorCode.SESSION_NOT_FOUND, f"session {session_id} not found")
        return info

    def list_incidents(self, session_id: str) -> list[Incident]:
        with self._read() as conn:
            self._require_session_row(conn, session_id)
            rows = conn.execute(
                "SELECT body_json FROM incidents WHERE session_id=? ORDER BY t_start_ms, incident_id", (session_id,)
            ).fetchall()
            overlay = self._overlay(conn, session_id)
        return [self._with_overlay(Incident.model_validate_json(r[0]), overlay) for r in rows]

    def get_incident_detail(self, session_id: str, incident_id: str) -> IncidentDetail:
        with self._read() as conn:
            self._require_session_row(conn, session_id)
            row = conn.execute(
                "SELECT body_json FROM incidents WHERE session_id=? AND incident_id=?", (session_id, incident_id)
            ).fetchone()
            if row is None:
                raise NotFoundError(ErrorCode.NOT_FOUND, f"incident {incident_id} not found in session {session_id}")
            overlay = self._overlay(conn, session_id)
            reviews = self._reviews(conn, session_id, incident_id)
            evidence = self._evidence_items(conn, session_id, incident_id)
        return IncidentDetail(
            incident=self._with_overlay(Incident.model_validate_json(row[0]), overlay), reviews=reviews, evidence=evidence
        )

    def add_review(self, session_id: str, incident_id: str, body: HumanReviewCreate) -> HumanReview:
        conn = self._require_conn()
        try:
            with self._db_lock:
                self._require_session_row(conn, session_id)
                if conn.execute(
                    "SELECT 1 FROM incidents WHERE session_id=? AND incident_id=?", (session_id, incident_id)
                ).fetchone() is None:
                    raise NotFoundError(ErrorCode.NOT_FOUND, f"incident {incident_id} not found in session {session_id}")
                latest = conn.execute(
                    "SELECT * FROM reviews WHERE session_id=? AND incident_id=? ORDER BY seq DESC LIMIT 1",
                    (session_id, incident_id),
                ).fetchone()
                now = utc_now()
                if (
                    latest is not None
                    and latest["decision"] == body.decision.value
                    and latest["comment"] == body.comment
                    and latest["operator"] == body.operator
                    and _us(now) - latest["created_at_us"] <= self.config.review_dedup_window_s * 1_000_000
                ):
                    return self._review_from_row(latest)  # double submit
                review = HumanReview(
                    review_id=f"rev-{secrets.token_hex(10)}",
                    session_id=session_id,
                    incident_id=incident_id,
                    decision=body.decision,
                    comment=body.comment,
                    operator=body.operator,
                    created_at=now,
                    supersedes_review_id=latest["review_id"] if latest is not None else None,
                )
                with db.transaction(conn):
                    conn.execute(
                        "INSERT INTO reviews(session_id, review_id, incident_id, seq, decision, comment, operator, created_at,"
                        " created_at_us, supersedes_review_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (session_id, review.review_id, incident_id, (latest["seq"] + 1) if latest is not None else 1,
                         review.decision.value, review.comment, review.operator, now.isoformat(), _us(now),
                         review.supersedes_review_id),
                    )
                return review
        except (sqlite3.Error, OSError) as exc:
            raise self._storage_error("add_review", exc) from None

    def save_answer(self, session_id: str, question_id: str, body: AnswerUpsert) -> AnswerRecord:
        conn = self._require_conn()
        try:
            with self._db_lock:
                row = self._require_session_row(conn, session_id)
                if row["state"] != SessionState.RUNNING.value:
                    raise InvalidStateError(
                        ErrorCode.INVALID_STATE,
                        f"answers are accepted only while running (session is {row['state']})",
                        state=row["state"],
                    )
                current = conn.execute(
                    "SELECT * FROM answers WHERE session_id=? AND question_id=?", (session_id, question_id)
                ).fetchone()
                if current is not None and current["client_seq"] >= body.client_seq:
                    return self._answer_from_row(current)
                now = utc_now()
                with db.transaction(conn):
                    conn.execute(
                        "INSERT OR REPLACE INTO answers(session_id, question_id, value_json, client_seq, saved_at)"
                        " VALUES (?,?,?,?,?)",
                        (session_id, question_id, json.dumps(body.value, ensure_ascii=False), body.client_seq, now.isoformat()),
                    )
                return AnswerRecord(
                    session_id=session_id, question_id=question_id, value=body.value, client_seq=body.client_seq, saved_at=now
                )
        except (sqlite3.Error, OSError) as exc:
            raise self._storage_error("save_answer", exc) from None

    def list_answers(self, session_id: str) -> list[AnswerRecord]:
        with self._read() as conn:
            self._require_session_row(conn, session_id)
            rows = conn.execute("SELECT * FROM answers WHERE session_id=? ORDER BY question_id", (session_id,)).fetchall()
        return [self._answer_from_row(r) for r in rows]

    def evidence_media(self, session_id: str, evidence_id: str) -> tuple[EvidenceItem, bytes]:
        with self._session_lock(session_id):
            with self._read() as conn:
                srow = self._require_session_row(conn, session_id)
                row = conn.execute(
                    "SELECT * FROM evidence WHERE session_id=? AND evidence_id=?", (session_id, evidence_id)
                ).fetchone()
                if row is None:
                    raise NotFoundError(ErrorCode.NOT_FOUND, f"evidence {evidence_id} not found in session {session_id}")
                if row["purged_at"] is not None:
                    raise NotFoundError(ErrorCode.NOT_FOUND, "evidence media was removed", reason=row["purge_reason"] or "purged")
                item = self._evidence_from_row(row)
                token, file_name = srow["media_token"], row["file_name"]
            try:
                data = self.vault.read(token, file_name, self.config.max_snapshot_bytes)
            except MediaPathError:
                raise NotFoundError(ErrorCode.NOT_FOUND, "evidence media is not available", reason="invalid_path") from None
            except OSError as exc:
                raise self._storage_error("read_evidence", exc) from None
        if data is None:
            raise NotFoundError(ErrorCode.NOT_FOUND, "evidence file is missing", reason="media_missing")
        if hashlib.sha256(data).hexdigest() != item.sha256:
            raise StorageError(ErrorCode.STORAGE_ERROR, "evidence file does not match its recorded SHA-256", reason="hash_mismatch")
        return item, data

    def summary(self, session_id: str, info: SessionInfo | None = None) -> SessionSummary:
        with self._read() as conn:
            row = self._require_session_row(conn, session_id)
            stored = SessionInfo.model_validate_json(row["info_json"])
            info = info if info is not None and info.session_id == session_id else stored
            coverage = self._coverage(conn, session_id, info, bool(row["recovered"]))
            summary = self._summary(conn, session_id, info, coverage, bool(row["recovered"]))
        return summary

    def export_snapshot(self, session_id: str, info: SessionInfo | None = None) -> ExportSnapshot:
        """Everything needed for report.json/report.html, read consistently: the per-session lock keeps
        a concurrent delete out until the snapshot (including media bytes) is complete."""
        cfg = self.config
        with self._session_lock(session_id):
            with self._read() as conn:
                row = self._require_session_row(conn, session_id)
                stored = SessionInfo.model_validate_json(row["info_json"])
                info = info if info is not None and info.session_id == session_id else stored
                recovered = bool(row["recovered"])
                coverage = self._coverage(conn, session_id, info, recovered)
                summary = self._summary(conn, session_id, info, coverage, recovered)
                incidents_total = conn.execute("SELECT COUNT(*) FROM incidents WHERE session_id=?", (session_id,)).fetchone()[0]
                rows = conn.execute(
                    "SELECT incident_id, body_json FROM incidents WHERE session_id=? ORDER BY t_start_ms, incident_id LIMIT ?",
                    (session_id, cfg.report_max_incidents),
                ).fetchall()
                overlay = self._overlay(conn, session_id)
                details: list[IncidentDetail] = []
                for r in rows:
                    details.append(
                        IncidentDetail(
                            incident=self._with_overlay(Incident.model_validate_json(r["body_json"]), overlay),
                            reviews=self._reviews(conn, session_id, r["incident_id"]),
                            evidence=self._evidence_items(conn, session_id, r["incident_id"]),
                        )
                    )
                observations_total = conn.execute(
                    "SELECT COUNT(*) FROM observations WHERE session_id=?", (session_id,)
                ).fetchone()[0]
                observations = [
                    json.loads(r[0])
                    for r in conn.execute(
                        "SELECT body_json FROM observations WHERE session_id=? ORDER BY t_session_ms, observation_id LIMIT ?",
                        (session_id, cfg.report_max_observations),
                    )
                ]
                missing_refs = conn.execute(
                    "SELECT COUNT(*) FROM incident_observations io LEFT JOIN observations o"
                    " ON o.session_id=io.session_id AND o.observation_id=io.observation_id"
                    " WHERE io.session_id=? AND o.observation_id IS NULL",
                    (session_id,),
                ).fetchone()[0]
                answers = [
                    self._answer_from_row(r)
                    for r in conn.execute("SELECT * FROM answers WHERE session_id=? ORDER BY question_id", (session_id,))
                ]
                producers = [
                    json.loads(r[0])
                    for r in conn.execute("SELECT body_json FROM producers WHERE session_id=? ORDER BY producer_key", (session_id,))
                ]
                counters = self._counters(conn, session_id)
                ev_rows = conn.execute(
                    "SELECT * FROM evidence WHERE session_id=? ORDER BY created_at_us, evidence_id", (session_id,)
                ).fetchall()
                token = row["media_token"]
            evidence: dict[str, EvidenceFile] = {}
            expired = 0
            embedded_bytes = 0
            embedded = 0
            for ev in ev_rows:
                if ev["purged_at"] is not None:
                    expired += 1
                    continue
                item = self._evidence_from_row(ev)
                if embedded >= cfg.report_max_embedded_images or embedded_bytes + item.size_bytes > cfg.report_max_embedded_bytes:
                    evidence[item.evidence_id] = EvidenceFile(item, None, "not_embedded")
                    continue
                try:
                    data = self.vault.read(token, ev["file_name"], cfg.max_snapshot_bytes)
                except (OSError, MediaPathError):
                    evidence[item.evidence_id] = EvidenceFile(item, None, "unreadable")
                    continue
                if data is None:
                    evidence[item.evidence_id] = EvidenceFile(item, None, "missing")
                elif hashlib.sha256(data).hexdigest() != item.sha256 or not data.startswith(JPEG_SOI):
                    evidence[item.evidence_id] = EvidenceFile(item, None, "hash_mismatch")
                else:
                    evidence[item.evidence_id] = EvidenceFile(item, data, "ok")
                    embedded += 1
                    embedded_bytes += len(data)
        config_versions: dict[str, str] = {"evidence_schema": str(self._schema_version)}
        for d in details:
            config_versions.setdefault("fusion.rule_version", d.incident.rule_version)
            config_versions.setdefault("fusion.config_version", d.incident.config_version)
        for p in producers:
            prefix = f"producer.{p['module']}"
            config_versions.setdefault(f"{prefix}.version", str(p["version"]))
            for key in ("model_id", "model_sha256", "config_version"):
                if p.get(key):
                    config_versions.setdefault(f"{prefix}.{key}", str(p[key]))
        return ExportSnapshot(
            info=info,
            summary=summary,
            coverage=coverage,
            incidents=details,
            incidents_total=incidents_total,
            observations=observations,
            observations_total=observations_total,
            missing_observation_refs=missing_refs,
            answers=answers,
            producers=producers,
            counters=counters,
            evidence=evidence,
            evidence_expired=expired,
            recovered=recovered,
            exported_at=utc_now(),
            config_versions=config_versions,
        )

    def delete_session(self, session_id: str) -> None:
        """Delete metadata, media and in-memory buffers of a finished session (409 while active).
        SQLite runs with secure_delete and a WAL checkpoint, but this is NOT a forensic erase of
        the disk/SSD."""
        conn = self._require_conn()
        with self._session_lock(session_id):
            try:
                with self._db_lock:
                    row = conn.execute("SELECT state, media_token FROM sessions WHERE session_id=?", (session_id,)).fetchone()
                    if row is None:
                        if session_id in self._deleted:
                            return  # idempotent
                        raise NotFoundError(ErrorCode.SESSION_NOT_FOUND, f"session {session_id} not found")
                    if row["state"] not in TERMINAL:
                        raise InvalidStateError(
                            ErrorCode.SESSION_ACTIVE, "finish or abort the session before deleting it", state=row["state"]
                        )
                    token = row["media_token"]
                    now = utc_now().isoformat()
                    with db.transaction(conn):
                        conn.execute("INSERT OR REPLACE INTO deleted_sessions(session_id, deleted_at) VALUES (?,?)", (session_id, now))
                        conn.execute(
                            "INSERT OR REPLACE INTO pending_media_deletions(media_token, requested_at) VALUES (?,?)", (token, now)
                        )
                        for table in db.SESSION_TABLES:
                            conn.execute(f"DELETE FROM {table} WHERE session_id=?", (session_id,))
                            self._fault_point(f"delete:{table}")
                    self._deleted.add(session_id)
                    self._live.pop(session_id, None)
                    try:
                        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    except sqlite3.Error:
                        pass
            except (sqlite3.Error, OSError) as exc:
                raise self._storage_error("delete_session", exc) from None
            self._delete_media_dir(token)

    def purge_expired_media(self) -> int:
        """Remove media older than config.media_ttl_s (metadata stays, marked purged)."""
        conn = self._conn
        if conn is None:
            return 0
        cutoff = _us(utc_now()) - int(self.config.media_ttl_s * 1_000_000)
        purged = 0
        try:
            with self._db_lock:
                rows = conn.execute(
                    "SELECT e.session_id, e.evidence_id, e.file_name, s.media_token FROM evidence e"
                    " JOIN sessions s ON s.session_id=e.session_id WHERE e.purged_at IS NULL AND e.created_at_us < ?",
                    (cutoff,),
                ).fetchall()
                for r in rows:
                    if not self.vault.remove_file(r["media_token"], r["file_name"]):
                        continue
                    with db.transaction(conn):
                        conn.execute(
                            "UPDATE evidence SET purged_at=?, purge_reason='ttl_expired' WHERE session_id=? AND evidence_id=?",
                            (utc_now().isoformat(), r["session_id"], r["evidence_id"]),
                        )
                    purged += 1
        except (sqlite3.Error, OSError) as exc:
            self._write_failed("purge_expired_media", exc, None)
        return purged

    # ================================================================ internals: recording
    def _new_tracker(self, session_id: str) -> CoverageTracker:
        next_no: dict[str, int] = {}
        conn = self._conn
        if conn is not None:
            for r in conn.execute(
                "SELECT component, MAX(seg_no) + 1 FROM coverage_segments WHERE session_id=? GROUP BY component", (session_id,)
            ):
                next_no[r[0]] = r[1]
        return CoverageTracker(self.config.coverage_gap_ms, self.config.max_coverage_segments, next_no)

    def _live_for_write(self, session_id: str, source_mode: SourceMode) -> _Live | None:
        live = self._live.get(session_id)
        if live is None:
            self._stats["writes_for_unknown_session" if session_id not in self._deleted else "writes_for_deleted_session"] += 1
            return None
        if live.terminal:
            live.counters["rejected_after_end"] += 1
            return None
        if source_mode.value != live.source_mode:
            live.counters["source_mode_mismatch"] += 1
            return None
        return live

    def _note_producer(self, live: _Live, obs: Observation) -> None:
        p = obs.producer
        key = "|".join(str(x or "") for x in (p.module, p.version, p.model_id, p.model_sha256, p.config_version))
        if key in live.producers:
            return
        conn = self._require_conn()
        with db.transaction(conn):
            conn.execute(
                "INSERT OR IGNORE INTO producers(session_id, producer_key, body_json) VALUES (?,?,?)",
                (live.session_id, key, p.model_dump_json()),
            )
        live.producers.add(key)

    def _evict(self, live: _Live, now_t: float) -> None:
        ring = live.ring
        horizon = now_t - self.config.observation_buffer_ms
        while ring:
            oid, (t, _, _) = next(iter(ring.items()))
            if len(ring) > self.config.observation_buffer_max or t < horizon:
                ring.popitem(last=False)
            else:
                break

    def _flush_live(self, live: _Live, final: bool = False) -> None:
        """Persist coverage segments and counters; on the final flush drop the temporary buffers."""
        conn = self._conn
        if final:
            live.coverage.close_all()
        segments = live.coverage.take_dirty()
        live.coverage.last_flush_t = live.last_t
        try:
            if conn is None:
                raise sqlite3.OperationalError("store not open")
            with db.transaction(conn):
                for s in segments:
                    conn.execute(
                        "INSERT OR REPLACE INTO coverage_segments(session_id, component, seg_no, cls, t_start_ms, t_end_ms, n)"
                        " VALUES (?,?,?,?,?,?,?)",
                        (live.session_id, s.component, s.seg_no, s.cls, s.t_start_ms, s.t_end_ms, s.n),
                    )
                if live.coverage.overflow:
                    live.counters["coverage_segments_overflow"] = 1
                for name, value in live.counters.items():
                    conn.execute(
                        "INSERT INTO counters(session_id, name, value) VALUES (?,?,?)"
                        " ON CONFLICT(session_id, name) DO UPDATE SET value=MAX(value, excluded.value)",
                        (live.session_id, name, int(value)),
                    )
        except (sqlite3.Error, OSError) as exc:
            current = {id(s) for s in live.coverage.current.values()}
            for s in segments:
                s.dirty = True
            live.coverage.pending = [s for s in segments if id(s) not in current] + live.coverage.pending
            self._write_failed("flush_coverage", exc, live.session_id)
        if final:
            live.ring.clear()  # temporary buffer: nothing is kept after the session ends

    def _count(self, session_id: str, name: str) -> None:
        with self._db_lock:  # counters are read (iterated) by API threads under the same lock
            live = self._live.get(session_id)
            if live is not None:
                live.counters[name] += 1
            else:
                self._stats[name] += 1

    def _write_failed(self, op: str, exc: BaseException, session_id: str | None) -> None:
        with self._db_lock:
            self._stats["write_errors"] += 1
            self._last_error = f"{op}: {_describe(exc)}"
            live = self._live.get(session_id) if session_id is not None else None
            if live is not None:
                live.counters["write_failures"] += 1
        log.warning("evidence %s failed: %s", op, _describe(exc))

    def _fault_point(self, name: str) -> None:
        if self._fault is not None:
            self._fault(name)

    def _session_lock(self, session_id: str) -> threading.Lock:
        with self._locks_lock:
            lock = self._session_locks.get(session_id)
            if lock is None:
                lock = self._session_locks[session_id] = threading.Lock()
            return lock

    def _delete_media_dir(self, token: str) -> None:
        if self.vault.remove_session_dir(token):
            try:
                with self._db_lock:
                    conn = self._require_conn()
                    with db.transaction(conn):
                        conn.execute("DELETE FROM pending_media_deletions WHERE media_token=?", (token,))
            except (sqlite3.Error, OSError, ProctorError) as exc:
                self._write_failed("delete_media_record", exc, None)
        else:
            self._stats["media_delete_pending"] += 1
            log.warning("media directory could not be removed yet; retried on next open")

    def _retry_media_deletions(self) -> None:
        conn = self._require_conn()
        for r in conn.execute("SELECT media_token FROM pending_media_deletions").fetchall():
            self._delete_media_dir(r[0])

    def _remove_orphan_media(self) -> None:
        conn = self._require_conn()
        known = {r[0] for r in conn.execute("SELECT media_token FROM sessions")}
        for token in self.vault.list_session_tokens():
            if token not in known:
                self.vault.remove_session_dir(token)

    def _recover_interrupted(self) -> None:
        """Sessions left non-terminal by a crashed/killed backend become FAILED (marked recovered)."""
        conn = self._require_conn()
        placeholders = ",".join("?" * len(TERMINAL))
        rows = conn.execute(
            f"SELECT session_id, info_json FROM sessions WHERE state NOT IN ({placeholders})", tuple(TERMINAL)
        ).fetchall()
        for r in rows:
            sid = r["session_id"]
            info = SessionInfo.model_validate_json(r["info_json"])
            last_t = conn.execute(
                "SELECT MAX(t) FROM (SELECT MAX(t_session_ms) t FROM observations WHERE session_id=:s"
                " UNION ALL SELECT MAX(t_end_ms) FROM coverage_segments WHERE session_id=:s"
                " UNION ALL SELECT MAX(t_session_ms) FROM session_events WHERE session_id=:s)",
                {"s": sid},
            ).fetchone()[0] or 0.0
            failed = info.model_copy(
                update={
                    "state": SessionState.FAILED,
                    "last_error": ApiErrorBody(code=ErrorCode.INTERNAL, message=INTERRUPTED_MESSAGE, details={"recovered": True}),
                }
            )
            now = utc_now()
            with db.transaction(conn):
                conn.execute(
                    "UPDATE sessions SET state=?, info_json=?, recovered=1, updated_at_us=? WHERE session_id=?",
                    (SessionState.FAILED.value, failed.model_dump_json(), _us(now), sid),
                )
                seq = conn.execute(
                    "SELECT COALESCE(MAX(seq), -1) + 1 FROM session_events WHERE session_id=?", (sid,)
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO session_events(session_id, seq, state, t_session_ms, paused_total_ms, at) VALUES (?,?,?,?,?,?)",
                    (sid, seq, SessionState.FAILED.value, float(last_t), info.paused_total_ms, now.isoformat()),
                )
            log.warning("session %s was interrupted by a backend restart; marked failed", sid)

    @staticmethod
    def _event_t(info: SessionInfo, now: datetime, first_start: bool) -> float:
        """Session-time of a state change. Exact for created/first start; other transitions are
        derived from the wall clock at receipt (SessionInfo carries no t_session_ms for them)."""
        if info.state == SessionState.CREATED:
            return 0.0
        if first_start and info.exam_started_t_ms is not None:
            return info.exam_started_t_ms
        if info.state.value in TERMINAL and info.finished_at is not None:
            return _ms_between(info.created_at, info.finished_at)
        return _ms_between(info.created_at, now)

    # ================================================================ internals: reading
    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        conn = self._require_conn()
        try:
            with self._db_lock:
                yield conn
        except (sqlite3.Error, OSError) as exc:
            raise self._storage_error("read", exc) from None

    def _require_conn(self) -> sqlite3.Connection:
        conn = self._conn
        if conn is None:
            raise StorageError(ErrorCode.STORAGE_ERROR, "evidence storage is not available", retryable=True)
        return conn

    def _require_session_row(self, conn: sqlite3.Connection, session_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        if row is None:
            raise NotFoundError(ErrorCode.SESSION_NOT_FOUND, f"session {session_id} not found")
        return row

    def _storage_error(self, op: str, exc: BaseException) -> StorageError:
        self._write_failed(op, exc, None)
        return StorageError(ErrorCode.STORAGE_ERROR, f"storage error during {op}: {_describe(exc)}", retryable=True)

    def _overlay(self, conn: sqlite3.Connection, session_id: str) -> dict[str, tuple[str, list[str]]]:
        """incident_id -> (review_status, evidence_ids) maintained by A08."""
        status: dict[str, str] = {}
        for r in conn.execute(
            "SELECT r.incident_id, r.decision FROM reviews r JOIN (SELECT incident_id, MAX(seq) AS m FROM reviews"
            " WHERE session_id=? GROUP BY incident_id) x ON x.incident_id=r.incident_id AND x.m=r.seq WHERE r.session_id=?",
            (session_id, session_id),
        ):
            status[r[0]] = r[1]
        evidence: dict[str, list[str]] = {}
        for r in conn.execute(
            "SELECT incident_id, evidence_id FROM evidence WHERE session_id=? AND purged_at IS NULL AND incident_id IS NOT NULL"
            " ORDER BY created_at_us, evidence_id",
            (session_id,),
        ):
            evidence.setdefault(r[0], []).append(r[1])
        keys = set(status) | set(evidence)
        return {k: (status.get(k, ReviewStatus.PENDING.value), evidence.get(k, [])[:32]) for k in keys}

    @staticmethod
    def _with_overlay(inc: Incident, overlay: dict[str, tuple[str, list[str]]]) -> Incident:
        status, evidence_ids = overlay.get(inc.incident_id, (ReviewStatus.PENDING.value, []))
        return inc.model_copy(update={"review_status": ReviewStatus(status), "evidence_ids": list(evidence_ids)})

    def _reviews(self, conn: sqlite3.Connection, session_id: str, incident_id: str) -> list[HumanReview]:
        rows = conn.execute(
            "SELECT * FROM reviews WHERE session_id=? AND incident_id=? ORDER BY seq", (session_id, incident_id)
        ).fetchall()
        return [self._review_from_row(r) for r in rows]

    def _evidence_items(self, conn: sqlite3.Connection, session_id: str, incident_id: str) -> list[EvidenceItem]:
        rows = conn.execute(
            "SELECT * FROM evidence WHERE session_id=? AND incident_id=? AND purged_at IS NULL ORDER BY created_at_us, evidence_id",
            (session_id, incident_id),
        ).fetchall()
        return [self._evidence_from_row(r) for r in rows]

    @staticmethod
    def _review_from_row(r: sqlite3.Row) -> HumanReview:
        return HumanReview(
            review_id=r["review_id"],
            session_id=r["session_id"],
            incident_id=r["incident_id"],
            decision=r["decision"],
            comment=r["comment"],
            operator=r["operator"],
            created_at=datetime.fromisoformat(r["created_at"]),
            supersedes_review_id=r["supersedes_review_id"],
        )

    @staticmethod
    def _answer_from_row(r: sqlite3.Row) -> AnswerRecord:
        return AnswerRecord(
            session_id=r["session_id"],
            question_id=r["question_id"],
            value=json.loads(r["value_json"]),
            client_seq=r["client_seq"],
            saved_at=datetime.fromisoformat(r["saved_at"]),
        )

    @staticmethod
    def _evidence_from_row(r: sqlite3.Row) -> EvidenceItem:
        return EvidenceItem(
            evidence_id=r["evidence_id"],
            session_id=r["session_id"],
            incident_id=r["incident_id"],
            kind=r["kind"],
            frame_id=r["frame_id"],
            t_session_ms=r["t_session_ms"],
            media_type=r["media_type"],
            sha256=r["sha256"],
            size_bytes=r["size_bytes"],
            created_at=datetime.fromisoformat(r["created_at"]),
        )

    def _counters(self, conn: sqlite3.Connection, session_id: str) -> dict[str, int]:
        counters: dict[str, int] = {r[0]: int(r[1]) for r in conn.execute(
            "SELECT name, value FROM counters WHERE session_id=?", (session_id,)
        )}
        live = self._live.get(session_id)
        if live is not None:
            for name, value in live.counters.items():
                counters[name] = max(counters.get(name, 0), int(value))
        return dict(sorted(counters.items()))

    # ---------------------------------------------------------------- coverage
    def _coverage(self, conn: sqlite3.Connection, session_id: str, info: SessionInfo, recovered: bool) -> dict[str, Any]:
        cfg = self.config
        events = conn.execute(
            "SELECT state, t_session_ms FROM session_events WHERE session_id=? ORDER BY seq", (session_id,)
        ).fetchall()
        now_t = _ms_between(info.created_at, utc_now())
        terminal_t = next((e["t_session_ms"] for e in events if e["state"] in TERMINAL), None)
        start = info.exam_started_t_ms
        if info.state.value in TERMINAL:
            end = terminal_t if terminal_t is not None else (
                _ms_between(info.created_at, info.finished_at) if info.finished_at else now_t
            )
            end_estimated = recovered
        else:
            end, end_estimated = now_t, True
        window: list[Interval] = [(start, max(start, end))] if start is not None else []

        pauses: list[Interval] = []
        pause_start: float | None = None
        for e in events:
            if e["state"] == SessionState.PAUSED.value and pause_start is None:
                pause_start = e["t_session_ms"]
            elif e["state"] != SessionState.PAUSED.value and pause_start is not None:
                pauses.append((pause_start, max(pause_start, e["t_session_ms"])))
                pause_start = None
        if pause_start is not None:
            pauses.append((pause_start, max(pause_start, end)))
        pauses = intersect(merge(pauses), window)
        active = subtract(window, pauses)

        segs: dict[str, dict[str, list[Interval]]] = {c: {"determined": [], "undetermined": []} for c in COVERED_COMPONENTS}
        counts: dict[str, int] = {c: 0 for c in COVERED_COMPONENTS}
        for r in conn.execute(
            "SELECT component, cls, t_start_ms, t_end_ms, n FROM coverage_segments WHERE session_id=?", (session_id,)
        ):
            if r["component"] in segs:
                segs[r["component"]][r["cls"]].append((r["t_start_ms"], r["t_end_ms"]))
                counts[r["component"]] += r["n"]
        live = self._live.get(session_id)
        if live is not None:  # include segments not flushed yet
            persisted_keys = {
                (r[0], r[1]) for r in conn.execute("SELECT component, seg_no FROM coverage_segments WHERE session_id=?", (session_id,))
            }
            for s in list(live.coverage.current.values()) + live.coverage.pending:
                if s.component in segs and (s.component, s.seg_no) not in persisted_keys:
                    segs[s.component][s.cls].append((s.t_start_ms, s.t_end_ms))
                    counts[s.component] += s.n

        gaps: list[CoverageGap] = [CoverageGap(t_start_ms=a, t_end_ms=b, component=Component.BACKEND, reason="paused") for a, b in pauses]
        components: dict[str, dict[str, Any]] = {}
        determined_active: list[list[Interval]] = []
        for comp in COVERED_COMPONENTS:
            det = intersect(merge(segs[comp]["determined"]), active)
            und = intersect(merge(segs[comp]["undetermined"]), active)
            determined_active.append(det)
            holes = subtract(active, det)
            parts = [(a, b, "undetermined") for a, b in intersect(holes, und)]
            parts += [(a, b, "no_observations") for a, b in subtract(holes, und)]
            for a, b, reason in parts:
                if b - a >= cfg.coverage_gap_ms:
                    gaps.append(CoverageGap(t_start_ms=a, t_end_ms=b, component=Component(comp), reason=reason))
            components[comp] = {
                "observed_ms": round(total(det), 1),
                "undetermined_ms": round(total(subtract(und, det)), 1),
                "unknown_ms": round(total(subtract(subtract(active, det), und)), 1),
                "observations": counts[comp],
            }
        gaps.extend(self._health_gaps(conn, session_id, window, info.state.value in TERMINAL))
        both = determined_active[0]
        for det in determined_active[1:]:
            both = intersect(both, det)
        gaps.sort(key=lambda g: (g.t_start_ms, g.component.value))
        truncated = len(gaps) > cfg.max_gaps_reported
        return {
            "window": {"t_start_ms": start, "t_end_ms": end if start is not None else None, "end_estimated": end_estimated},
            "exam_ms": round(total(window), 1),
            "active_ms": round(total(active), 1),
            "paused_ms": round(info.paused_total_ms, 1),
            "pauses": [{"t_start_ms": a, "t_end_ms": b} for a, b in pauses],
            "observed_ms": round(total(both), 1),
            "components": components,
            "gaps": gaps[: cfg.max_gaps_reported],
            "gaps_total": len(gaps),
            "gaps_truncated": truncated,
            "definition_ru": (
                "Наблюдаемое время — интервалы экзамена (без пауз), где и детектор телефона, и анализ лица/взгляда "
                f"выдавали определённые результаты; разрыв дольше {cfg.coverage_gap_ms / 1000:.0f} с считается пропуском."
            ),
        }

    def _health_gaps(self, conn: sqlite3.Connection, session_id: str, window: list[Interval], terminal: bool) -> list[CoverageGap]:
        if not window:
            return []
        w_start, w_end = window[0]
        gaps: list[CoverageGap] = []
        open_since: dict[str, tuple[float, str]] = {}
        for r in conn.execute(
            "SELECT body_json FROM observations WHERE session_id=? AND kind='health' ORDER BY t_session_ms, observation_id",
            (session_id,),
        ):
            obs = HealthObservation.model_validate_json(r[0])
            comp = obs.health.component.value
            if obs.health.status == HealthStatus.OK:
                if comp in open_since:
                    start, reason = open_since.pop(comp)
                    gaps.append(self._clip_gap(start, obs.t_session_ms, comp, reason, w_start, w_end))
            elif comp not in open_since:
                open_since[comp] = (obs.t_session_ms, obs.health.code)
        for comp, (start, reason) in open_since.items():
            gaps.append(self._clip_gap(start, w_end if terminal else None, comp, reason, w_start, w_end))
        return [g for g in gaps if g is not None]

    @staticmethod
    def _clip_gap(start: float, end: float | None, comp: str, reason: str, w_start: float, w_end: float) -> CoverageGap | None:
        a = max(start, w_start)
        b = None if end is None else min(end, w_end)
        if b is not None and b <= a:
            return None
        if a > w_end:
            return None
        return CoverageGap(t_start_ms=a, t_end_ms=b, component=Component(comp), reason=reason)

    def _summary(self, conn: sqlite3.Connection, session_id: str, info: SessionInfo, coverage: dict[str, Any], recovered: bool) -> SessionSummary:
        by_rule: dict[str, int] = {}
        for r in conn.execute("SELECT rule_id, COUNT(*) FROM incidents WHERE session_id=? GROUP BY rule_id", (session_id,)):
            by_rule[r[0]] = r[1]
        total_incidents = sum(by_rule.values())
        overlay = self._overlay(conn, session_id)
        by_decision: dict[str, int] = {}
        reviewed = 0
        for status, _ in overlay.values():
            if status != ReviewStatus.PENDING.value:
                by_decision[status] = by_decision.get(status, 0) + 1
                reviewed += 1
        if total_incidents - reviewed:
            by_decision[ReviewStatus.PENDING.value] = total_incidents - reviewed
        open_left = conn.execute(
            "SELECT COUNT(*) FROM incidents WHERE session_id=? AND state=?", (session_id, IncidentState.OPEN.value)
        ).fetchone()[0]
        counters = self._counters(conn, session_id)
        retained = conn.execute("SELECT COUNT(*) FROM evidence WHERE session_id=? AND purged_at IS NULL", (session_id,)).fetchone()[0]
        summary = SessionSummary(
            session=info,
            observed_ms=coverage["observed_ms"],
            paused_ms=coverage["paused_ms"],
            gaps=coverage["gaps"],
            incidents_total=total_incidents,
            incidents_by_rule=dict(sorted(by_rule.items())),
            reviews_by_decision=dict(sorted(by_decision.items())),
            limitations_ru=limitations(info, coverage, counters, recovered, open_left, retained),
        )
        # Raw incidents deliberately exclude the human-review overlay: teacher decisions
        # never change the automatic review-priority zone.
        incidents = [Incident.model_validate_json(r[0]) for r in conn.execute(
            "SELECT body_json FROM incidents WHERE session_id=? ORDER BY t_start_ms, incident_id", (session_id,))]
        assessment = review_zones.assess_session_zone(incidents, summary, None)
        return summary.model_copy(update={
            "review_zone": assessment.zone, "review_zone_reasons_ru": assessment.reasons_ru,
            "review_zone_rule_version": assessment.rule_version,
        })

    # ---------------------------------------------------------------- process lock
    def _acquire_process_lock(self) -> bool:
        path = self.data_dir / (self.config.db_file_name + ".lock")
        fh = open(path, "a+b")
        try:
            if sys.platform == "win32":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        self._lock_fh = fh
        return True

    def _release_process_lock(self) -> None:
        fh, self._lock_fh = self._lock_fh, None
        if fh is not None:
            try:
                fh.close()  # closing releases flock/msvcrt locks
            except OSError:
                pass


class _Discard(Exception):
    pass


def limitations(
    info: SessionInfo, coverage: dict[str, Any], counters: dict[str, int], recovered: bool, open_left: int, retained_media: int
) -> list[str]:
    """Plain-language limitations shown in the summary and the report (no guilt wording)."""
    out: list[str] = []
    if info.source_mode == SourceMode.SYNTHETIC:
        out.append("СИНТЕТИЧЕСКИЕ ДАННЫЕ: сценарные наблюдения для проверки системы, а не результат работы камеры и CV.")
    elif info.source_mode == SourceMode.REPLAY:
        out.append("REPLAY: воспроизведение заранее записанного видео через тот же конвейер, а не живая сессия.")
    out.append("Эпизоды — повод для проверки преподавателем, а не доказательство нарушения; решение принимает преподаватель.")
    out.append("Направление взгляда — приблизительная оценка, не eye-tracking; обнаружение телефона не доказывает съёмку экрана.")
    if info.calibration.phase.value == "skipped":
        out.append("Калибровка взгляда пропущена: оценки взгляда не откалиброваны под этого участника.")
    if info.exam_started_t_ms is None:
        out.append("Экзамен не был начат: наблюдение не проводилось.")
    gaps_total = coverage.get("gaps_total", 0)
    if gaps_total:
        out.append(
            f"Интервалов без наблюдения или с неопределёнными результатами: {gaps_total}. "
            "В них нарушения не определялись — это «неизвестно», а не «нарушений нет»."
        )
    if coverage.get("gaps_truncated"):
        out.append("Список пропусков сокращён до первых записей.")
    if info.paused_total_ms > 0:
        out.append("Во время паузы наблюдение не велось.")
    if recovered:
        out.append("Сессия прервана сбоем или принудительным закрытием: данные после последней записи отсутствуют.")
    if open_left and info.state.value in TERMINAL:
        out.append(f"Эпизодов, не закрытых до конца сессии: {open_left} (длительность — до последнего обновления).")
    failures = counters.get("write_failures", 0)
    if failures:
        out.append(f"Сбои записи хранилища: {failures}; часть данных могла не сохраниться.")
    if counters.get("coverage_segments_overflow"):
        out.append("Учёт покрытия переполнен: оценка наблюдаемого времени неполная.")
    if not info.retain_media:
        out.append("Снимки не сохранялись (сохранение медиа выключено): отчёт содержит только метаданные.")
    elif retained_media == 0:
        out.append("Сохранение снимков включено, но снимков нет.")
    out.append("Хэши SHA-256 помогают обнаружить случайное изменение файлов, но не защищают от изменения владельцем компьютера.")
    return out


def _describe(exc: BaseException) -> str:
    """Error description without paths or data (safe for logs/health)."""
    if isinstance(exc, OSError) and exc.errno is not None:
        import errno as _errno

        return f"{type(exc).__name__}[{_errno.errorcode.get(exc.errno, exc.errno)}]"
    if isinstance(exc, sqlite3.Error):
        return f"{type(exc).__name__}: {str(exc)[:120]}"
    return type(exc).__name__


def _is_within(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve())
    except OSError:
        return False
