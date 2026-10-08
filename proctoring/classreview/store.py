"""ReviewStore: episodes, clips and teacher decisions of a class session (T03).

Thread-safe (one SQLite connection behind an RLock). Used by the class server (C1):
  * ingest_incident(student_id, msg)     — accepted C1 event identity; legacy/dev dedup by (student_id, seq);
  * note_clip_requested / note_command_ack — when the teacher sends `request_clip` and the student acks it;
  * store_clip(...)                       — POST /api/student/clips/{incident_id} (router);
  * list_incidents / record_decision / clip_file — teacher routes (router);
  * on_change(callback)                   — push updated episodes to /ws/teacher.

An episode is ONE row per (student_id, incident_id): open -> closed updates the same row (frames are
never episodes; A05 on the student computer already merges frames into episodes).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import secrets
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import db
from .config import CLIP_MEDIA_TYPES, MAX_SNAPSHOT_B64, ReviewConfig
from .media import CODEC_LABEL_RU, InvalidMedia, MediaInfo, MediaStore, inspect
from .zones import zone_after_review

log = logging.getLogger("classreview")

ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
CODE_RE = re.compile(r"^[a-z0-9_.]{1,64}$")
PRIORITIES = ("low", "medium", "high")
STATES = ("open", "closed")
DECISIONS = ("confirmed", "dismissed", "needs_followup")
CLIP_SOURCES = ("live", "replay", "synthetic", "test", "unspecified")
JPEG_SOI = b"\xff\xd8\xff"


class ReviewError(Exception):
    """status: HTTP status the router answers with; code: machine code; message_ru: text for the UI."""

    def __init__(self, status: int, code: str, message_ru: str, **details: Any):
        super().__init__(message_ru)
        self.status = status
        self.code = code
        self.message_ru = message_ru
        self.details = details


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _us(dt: datetime) -> int:
    return int(dt.timestamp() * 1_000_000)


def _parse_wall(value: Any) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise ReviewError(422, "invalid_incident", "t_start_wall должен быть ISO-8601 со смещением")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ReviewError(422, "invalid_incident", "t_start_wall должен быть ISO-8601 со смещением") from None
    if dt.tzinfo is None:
        raise ReviewError(422, "invalid_incident", "t_start_wall без часового пояса")
    return dt.astimezone(timezone.utc)


@dataclass
class IngestResult:
    status: str  # new | updated | duplicate | stale
    incident: dict[str, Any] | None


class ReviewStore:
    def __init__(self, config: ReviewConfig | None = None):
        self.config = config or ReviewConfig.from_env()
        self.data_dir = Path(self.config.data_dir)
        self.clips = MediaStore(self.data_dir / "clips")
        self.snapshots = MediaStore(self.data_dir / "snapshots")
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._listeners: list[Callable[[dict[str, Any]], None]] = []
        self._verified: dict[str, tuple[int, int]] = {}  # file_name -> (mtime_ns, size) verified against sha256

    # ------------------------------------------------------------------ lifecycle
    def open(self) -> None:
        with self._lock:
            if self._conn is not None:
                return
            self.data_dir.mkdir(parents=True, exist_ok=True)
            conn = db.connect(self.data_dir / self.config.db_file_name)
            try:
                db.migrate(conn)
            except BaseException:
                conn.close()
                raise
            self._conn = conn
            self.clips.ensure()
            self.snapshots.ensure()
            self.clips.cleanup_tmp()
            self.snapshots.cleanup_tmp()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except sqlite3.Error:
                    pass
                self._conn.close()
                self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise ReviewError(503, "storage_unavailable", "Хранилище истории недоступно")
        return self._conn

    def on_change(self, callback: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        """callback({"type": "incident", "student_id", "incident": <episode view>}) after every change."""
        self._listeners.append(callback)
        return lambda: self._listeners.remove(callback) if callback in self._listeners else None

    def _emit(self, student_id: str, incident_id: str) -> None:
        if not self._listeners:
            return
        try:
            view = self.get_incident(student_id, incident_id)
        except ReviewError:
            return
        event = {"type": "incident", "student_id": student_id, "incident": view}
        for cb in list(self._listeners):
            try:
                cb(event)
            except Exception:
                log.exception("review change listener failed")

    # ------------------------------------------------------------------ class sessions
    def open_class_session(self, class_session_id: str, started_at: datetime | None = None) -> None:
        if not ID_RE.fullmatch(class_session_id or ""):
            raise ReviewError(422, "invalid_session", "Некорректный идентификатор сессии класса")
        started = started_at or now_utc()
        with self._lock, db.transaction(self.conn):
            self.conn.execute(
                "INSERT OR IGNORE INTO class_sessions(class_session_id, started_at, started_at_us) VALUES (?,?,?)",
                (class_session_id, started.isoformat(), _us(started)),
            )

    # ------------------------------------------------------------------ ingestion (from /ws/student)
    def ingest_incident(self, student_id: str, msg: dict[str, Any], *, class_session_id: str,
                        canonical_event: dict[str, Any] | None = None) -> IngestResult:
        """Store a legacy message by wire seq, or an accepted C1 event by canonical identity.
        A closed episode never reopens; late C1 events can still add clip/snapshot evidence."""
        if not ID_RE.fullmatch(student_id or ""):
            raise ReviewError(422, "invalid_student", "Некорректный student_id")
        f = self._validate_incident(msg)
        snapshot_bytes = f.pop("snapshot")
        now = now_utc()
        with self._lock:
            conn = self.conn
            if canonical_event is not None:
                # C1 has already resolved wire sequence reuse across runs and conflicts. Its immutable
                # event ID, not the wire seq, is the identity. Allocate a local ordering key atomically.
                if conn.execute("SELECT 1 FROM canonical_events WHERE student_id=? AND event_id=?",
                                (student_id, canonical_event["event_id"])).fetchone():
                    return IngestResult("duplicate", None)
                f["seq"] = conn.execute("SELECT COALESCE(MAX(seq),0)+1 FROM seen_seq WHERE student_id=?",
                                         (student_id,)).fetchone()[0]
            if conn.execute("SELECT 1 FROM seen_seq WHERE student_id=? AND seq=?", (student_id, f["seq"])).fetchone():
                return IngestResult("duplicate", None)
            row = conn.execute(
                "SELECT last_seq, state, snapshot_file, class_session_id FROM incidents WHERE student_id=? AND incident_id=?",
                (student_id, f["incident_id"]),
            ).fetchone()
            snapshot_name = None
            if snapshot_bytes is not None and (row is None or row["snapshot_file"] is None):
                snapshot_name, _ = self.snapshots.write("jpg", snapshot_bytes)
            try:
                with db.transaction(conn):
                    conn.execute(
                        "INSERT OR IGNORE INTO class_sessions(class_session_id, started_at, started_at_us) VALUES (?,?,?)",
                        (class_session_id, now.isoformat(), _us(now)),
                    )
                    conn.execute(
                        "INSERT INTO seen_seq(student_id, seq, incident_id, received_at) VALUES (?,?,?,?)",
                        (student_id, f["seq"], f["incident_id"], now.isoformat()),
                    )
                    if canonical_event is not None:
                        conn.execute("INSERT INTO canonical_events VALUES (?,?,?,?,?)",
                                     (student_id, canonical_event["event_id"], f["incident_id"], f["seq"], to_json(canonical_event)))
                    if row is not None and row["last_seq"] > f["seq"]:
                        status = "stale"
                    elif row is not None and row["state"] == "closed" and f["state"] == "open":
                        status = "stale"  # a closed episode is never reopened by a late message
                        if canonical_event is not None:
                            # C1 retains evidence from accepted out-of-order events while keeping
                            # the closed episode's state, explanation and timing. Mirror that merge.
                            conn.execute(
                                "UPDATE incidents SET last_seq=?,clip_available=MAX(clip_available,?),"
                                " snapshot_file=COALESCE(snapshot_file,?),updated_at=?"
                                " WHERE student_id=? AND incident_id=?",
                                (f["seq"], int(f["clip_available"]), snapshot_name, now.isoformat(),
                                 student_id, f["incident_id"]),
                            )
                            status = "updated"
                    else:
                        status = "new" if row is None else "updated"
                        conn.execute(
                            "INSERT INTO incidents(student_id, incident_id, class_session_id, last_seq, rule_id, category, priority,"
                            " state, t_start_wall, t_start_us, duration_ms, explanation_ru, clip_available, snapshot_file,"
                            " first_seen_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                            " ON CONFLICT(student_id, incident_id) DO UPDATE SET last_seq=excluded.last_seq,"
                            " rule_id=excluded.rule_id, category=excluded.category, priority=excluded.priority,"
                            " state=excluded.state, t_start_wall=excluded.t_start_wall, t_start_us=excluded.t_start_us,"
                            " duration_ms=excluded.duration_ms, explanation_ru=excluded.explanation_ru,"
                            " clip_available=MAX(incidents.clip_available,excluded.clip_available),"
                            " snapshot_file=COALESCE(incidents.snapshot_file, excluded.snapshot_file),"
                            " updated_at=excluded.updated_at",
                            (student_id, f["incident_id"], row["class_session_id"] if row is not None else class_session_id,
                             f["seq"], f["rule_id"], f["category"], f["priority"], f["state"], f["t_start_wall"],
                             f["t_start_us"], f["duration_ms"], f["explanation_ru"], int(f["clip_available"]),
                             snapshot_name, now.isoformat(), now.isoformat()),
                        )
            except BaseException:
                if snapshot_name:
                    self.snapshots.remove(snapshot_name)
                raise
            if status == "stale" and snapshot_name:
                self.snapshots.remove(snapshot_name)
        if status in ("new", "updated"):
            self._emit(student_id, f["incident_id"])
            return IngestResult(status, self.get_incident(student_id, f["incident_id"]))
        return IngestResult(status, None)

    def _validate_incident(self, m: dict[str, Any]) -> dict[str, Any]:
        def bad(field: str) -> ReviewError:
            return ReviewError(422, "invalid_incident", f"Некорректное поле эпизода: {field}", field=field)

        if not isinstance(m, dict):
            raise bad("message")
        seq = m.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool) or not 1 <= seq <= 2**53:
            raise bad("seq")
        iid = m.get("incident_id")
        if not isinstance(iid, str) or not ID_RE.fullmatch(iid):
            raise bad("incident_id")
        for key in ("rule_id", "category"):
            if not isinstance(m.get(key), str) or not CODE_RE.fullmatch(m[key]):
                raise bad(key)
        if m.get("priority") not in PRIORITIES:
            raise bad("priority")
        if m.get("state") not in STATES:
            raise bad("state")
        start = _parse_wall(m.get("t_start_wall"))
        dur = m.get("duration_ms")
        if isinstance(dur, bool) or not isinstance(dur, (int, float)) or not 0 <= dur <= self.config.max_duration_ms:
            raise bad("duration_ms")
        expl = m.get("explanation_ru", "")
        if not isinstance(expl, str) or len(expl) > self.config.max_explanation_chars:
            raise bad("explanation_ru")
        clip_available = m.get("clip_available", False)
        if not isinstance(clip_available, bool):
            raise bad("clip_available")
        snapshot = None
        b64 = m.get("snapshot_jpeg_b64")
        if b64 is not None:
            if not isinstance(b64, str) or len(b64) > MAX_SNAPSHOT_B64:
                raise bad("snapshot_jpeg_b64")
            try:
                snapshot = base64.b64decode(b64, validate=True)
            except ValueError:
                raise bad("snapshot_jpeg_b64") from None
            if not snapshot.startswith(JPEG_SOI):
                raise bad("snapshot_jpeg_b64")
        return {
            "seq": seq,
            "incident_id": iid,
            "rule_id": m["rule_id"],
            "category": m["category"],
            "priority": m["priority"],
            "state": m["state"],
            "t_start_wall": start.isoformat().replace("+00:00", "Z"),
            "t_start_us": _us(start),
            "duration_ms": float(dur),
            "explanation_ru": expl,
            "clip_available": clip_available,
            "snapshot": snapshot,
        }

    # ------------------------------------------------------------------ clip requests (teacher command)
    def note_clip_requested(self, student_id: str, incident_id: str, command_id: str | None,
                            *, requested_at: datetime | None = None) -> dict[str, Any]:
        """Called by the class server when it sends `request_clip` to the student."""
        now = requested_at or now_utc()  # the command's issue time: a replayed QUEUED/SENT cannot extend the timeout
        with self._lock:
            self._require_incident(student_id, incident_id)
            if command_id and self.conn.execute("SELECT 1 FROM clip_requests WHERE command_id=?", (command_id,)).fetchone():
                return self.get_incident(student_id, incident_id)
            if self.conn.execute(
                "SELECT 1 FROM clips WHERE student_id=? AND incident_id=?", (student_id, incident_id)
            ).fetchone():
                raise ReviewError(409, "clip_already_available", "Клип этого эпизода уже получен")
            with db.transaction(self.conn):
                self.conn.execute(
                    "INSERT INTO clip_requests(student_id, incident_id, command_id, status, requested_at, requested_at_us,"
                    " updated_at) VALUES (?,?,?,?,?,?,?)",
                    (student_id, incident_id, command_id, "pending", now.isoformat(), _us(now), now.isoformat()),
                )
        self._emit(student_id, incident_id)
        return self.get_incident(student_id, incident_id)

    def note_command_ack(self, command_id: str, ok: bool, error_ru: str | None = None,
                         reason_code: str = "student_refused") -> None:
        """`ack` for a request_clip command. ok=false -> the clip is shown as unavailable with the reason."""
        if ok:
            return  # the upload itself completes the request
        with self._lock:
            row = self.conn.execute(
                "SELECT request_id, student_id, incident_id FROM clip_requests WHERE command_id=? AND status='pending'",
                (command_id,),
            ).fetchone()
            if row is None:
                return
            with db.transaction(self.conn):
                self.conn.execute(
                    "UPDATE clip_requests SET status='failed', reason_code=?, reason_ru=?, updated_at=?"
                    " WHERE request_id=?",
                    (reason_code, (error_ru or "Компьютер студента не смог отправить клип")[:300], now_utc().isoformat(), row["request_id"]),
                )
        self._emit(row["student_id"], row["incident_id"])

    # ------------------------------------------------------------------ clip upload (from the student)
    def store_clip(self, student_id: str, incident_id: str, media_type: str, data: bytes, source: str = "unspecified") -> dict[str, Any]:
        """Validate and store a clip. Idempotent: the same bytes again -> 200 duplicate; other bytes for an
        already stored clip -> 409 (evidence is never replaced). Only clips that were requested are accepted."""
        if media_type not in CLIP_MEDIA_TYPES:
            raise ReviewError(415, "unsupported_media_type", "Допустимы только video/mp4 и video/x-msvideo")
        if len(data) > self.config.max_clip_bytes:
            raise ReviewError(413, "clip_too_large", f"Клип больше {self.config.max_clip_bytes // (1024 * 1024)} МБ")
        if source not in CLIP_SOURCES:
            source = "unspecified"
        sha = hashlib.sha256(data).hexdigest()
        with self._lock:
            self._require_incident(student_id, incident_id, status=404)
            provenance = self._provenance(student_id, incident_id)
            if provenance:
                # A transport header cannot turn an unknown/replay/synthetic incident into live video.
                original = provenance[0]
                mode = original.get("source_mode")
                if original["origin"] == "simulated" or mode == "synthetic":
                    source = "synthetic"
                elif mode == "replay":
                    source = "replay"
                elif mode != "live" or original["origin"] != "real":
                    source = "unspecified"
                elif source not in ("synthetic", "test", "replay"):
                    source = "live"
            existing = self.conn.execute(
                "SELECT sha256 FROM clips WHERE student_id=? AND incident_id=?", (student_id, incident_id)
            ).fetchone()
            if existing is not None:
                if existing["sha256"] == sha:
                    return {"status": "duplicate", **self._clip_meta(student_id, incident_id)}
                raise ReviewError(409, "clip_already_stored", "Для этого эпизода уже сохранён другой клип; он не заменяется")
            request = self.conn.execute(
                "SELECT request_id FROM clip_requests WHERE student_id=? AND incident_id=? AND status='pending'"
                " ORDER BY request_id DESC LIMIT 1",
                (student_id, incident_id),
            ).fetchone()
            if request is None:
                raise ReviewError(409, "clip_not_requested", "Клип не запрашивался преподавателем (видео передаётся только по запросу)")
        try:
            info: MediaInfo = inspect(media_type, data)
        except InvalidMedia as exc:
            self._fail_request(student_id, incident_id, exc.code, f"Файл клипа отклонён: {exc.message_ru}")
            raise ReviewError(422, exc.code, exc.message_ru) from None
        file_name, _ = self.clips.write(CLIP_MEDIA_TYPES[media_type], data)
        now = now_utc()
        with self._lock:  # every use of the shared connection happens under the lock
            try:
                with db.transaction(self.conn):
                    self.conn.execute(
                        "INSERT INTO clips(student_id, incident_id, file_name, media_type, container, codec, duration_s,"
                        " faststart, size_bytes, sha256, source, uploaded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (student_id, incident_id, file_name, media_type, info.container, info.codec, info.duration_s,
                         None if info.faststart is None else int(info.faststart), len(data), sha, source, now.isoformat()),
                    )
                    self.conn.execute(
                        "UPDATE clip_requests SET status='fulfilled', updated_at=? WHERE student_id=? AND incident_id=?"
                        " AND status='pending'",
                        (now.isoformat(), student_id, incident_id),
                    )
            except sqlite3.IntegrityError:
                self.clips.remove(file_name)  # a concurrent upload of this episode's clip won the race
                existing = self.conn.execute(
                    "SELECT sha256 FROM clips WHERE student_id=? AND incident_id=?", (student_id, incident_id)
                ).fetchone()
                if existing is not None and existing["sha256"] == sha:
                    return {"status": "duplicate", **self._clip_meta(student_id, incident_id)}
                raise ReviewError(409, "clip_already_stored", "Для этого эпизода уже сохранён другой клип; он не заменяется") from None
            except BaseException:
                self.clips.remove(file_name)
                raise
            result = {"status": "stored", **self._clip_meta(student_id, incident_id)}
        self._emit(student_id, incident_id)
        return result

    def _fail_request(self, student_id: str, incident_id: str, code: str, reason_ru: str) -> None:
        with self._lock, db.transaction(self.conn):
            self.conn.execute(
                "UPDATE clip_requests SET status='failed', reason_code=?, reason_ru=?, updated_at=? WHERE student_id=?"
                " AND incident_id=? AND status='pending'",
                (code, reason_ru[:300], now_utc().isoformat(), student_id, incident_id),
            )
        self._emit(student_id, incident_id)

    def _clip_meta(self, student_id: str, incident_id: str) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM clips WHERE student_id=? AND incident_id=?", (student_id, incident_id)).fetchone()
        return {
            "incident_id": incident_id,
            "media_type": row["media_type"],
            "codec": row["codec"],
            "size_bytes": row["size_bytes"],
            "sha256": row["sha256"],
            "duration_s": row["duration_s"],
            "source": row["source"],
            "uploaded_at": row["uploaded_at"],
        }

    # ------------------------------------------------------------------ teacher reads
    def clip_file(self, incident_id: str, student_id: str | None = None) -> tuple[dict[str, Any], Path]:
        """Stored clip for GET /api/teacher/clips/{incident_id}. Verifies size + SHA-256 once per file version."""
        if not ID_RE.fullmatch(incident_id or ""):
            raise ReviewError(422, "invalid_id", "Некорректный incident_id")
        with self._lock:
            if student_id is not None:
                rows = self.conn.execute(
                    "SELECT * FROM clips WHERE incident_id=? AND student_id=?", (incident_id, student_id)
                ).fetchall()
            else:
                rows = self.conn.execute("SELECT * FROM clips WHERE incident_id=?", (incident_id,)).fetchall()
        if not rows:
            raise ReviewError(404, "clip_not_found", "Клип для этого эпизода не получен")
        if len(rows) > 1:
            raise ReviewError(409, "ambiguous_incident", "incident_id совпадает у нескольких студентов: укажите student_id")
        row = rows[0]
        try:
            path = self.clips.path(row["file_name"])
            st = path.stat()
        except (InvalidMedia, FileNotFoundError):
            raise ReviewError(404, "clip_file_missing", "Файл клипа отсутствует в хранилище") from None
        if st.st_size != row["size_bytes"]:
            raise ReviewError(500, "clip_integrity", "Файл клипа изменён (размер не совпадает)")
        stamp = (st.st_mtime_ns, st.st_size)
        if self._verified.get(row["file_name"]) != stamp:
            h = hashlib.sha256()
            with open(path, "rb") as fh:
                for block in iter(lambda: fh.read(1 << 20), b""):
                    h.update(block)
            if h.hexdigest() != row["sha256"]:
                raise ReviewError(500, "clip_integrity", "Файл клипа изменён (SHA-256 не совпадает)")
            self._verified[row["file_name"]] = stamp
        meta = dict(row)
        meta.pop("file_name", None)
        return meta, path

    def snapshot_file(self, student_id: str, incident_id: str) -> Path:
        with self._lock:
            row = self._require_incident(student_id, incident_id)
        if not row["snapshot_file"]:
            raise ReviewError(404, "snapshot_not_found", "Снимок эпизода не получен")
        try:
            return self.snapshots.path(row["snapshot_file"])
        except InvalidMedia:
            raise ReviewError(404, "snapshot_not_found", "Снимок эпизода не получен") from None

    def list_incidents(self, student_id: str) -> list[dict[str, Any]]:
        if not ID_RE.fullmatch(student_id or ""):
            raise ReviewError(422, "invalid_student", "Некорректный student_id")
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM incidents WHERE student_id=? ORDER BY t_start_us, incident_id", (student_id,)
            ).fetchall()
            return [self._view(r) for r in rows]

    def list_students(self) -> list[dict[str, Any]]:
        """Students that have episodes in the store (survives restarts; the class server keeps the live cards)."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT student_id, MAX(class_session_id) AS class_session_id, COUNT(*) AS incidents_total,"
                " SUM(CASE WHEN NOT EXISTS (SELECT 1 FROM decisions d WHERE d.student_id=i.student_id"
                " AND d.incident_id=i.incident_id) THEN 1 ELSE 0 END) AS incidents_unreviewed"
                " FROM incidents i GROUP BY student_id ORDER BY student_id"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_incident(self, student_id: str, incident_id: str) -> dict[str, Any]:
        with self._lock:
            return self._view(self._require_incident(student_id, incident_id))

    def review_summary(self, student_id: str, *, reported_zone: str | None = None, monitoring: str | None = None) -> dict[str, Any]:
        incidents = self.list_incidents(student_id)
        with self._lock:
            started = None
            if incidents:
                srow = self.conn.execute(
                    "SELECT s.started_at FROM class_sessions s JOIN incidents i ON i.class_session_id=s.class_session_id"
                    " WHERE i.student_id=? ORDER BY s.started_at_us DESC LIMIT 1",
                    (student_id,),
                ).fetchone()
                started = datetime.fromisoformat(srow["started_at"]) if srow else None
        return zone_after_review(incidents, session_started_at=started, reported_zone=reported_zone, monitoring=monitoring)

    # ------------------------------------------------------------------ decisions
    def record_decision(self, student_id: str, incident_id: str, decision: str, note_ru: str, operator: str) -> dict[str, Any]:
        if decision not in DECISIONS:
            raise ReviewError(422, "invalid_decision", "Решение: confirmed, dismissed или needs_followup")
        if not isinstance(note_ru, str) or len(note_ru) > self.config.max_note_chars:
            raise ReviewError(422, "invalid_note", f"Комментарий длиннее {self.config.max_note_chars} символов")
        operator = (operator or "teacher")[:64]
        now = now_utc()
        with self._lock:
            self._require_incident(student_id, incident_id)
            latest = self.conn.execute(
                "SELECT * FROM decisions WHERE student_id=? AND incident_id=? ORDER BY seq DESC LIMIT 1",
                (student_id, incident_id),
            ).fetchone()
            if (
                latest is not None
                and latest["decision"] == decision
                and latest["note_ru"] == note_ru
                and latest["operator"] == operator
                and _us(now) - latest["created_at_us"] <= self.config.decision_dedup_window_s * 1_000_000
            ):
                pass  # double submit: keep the existing entry
            else:
                with db.transaction(self.conn):
                    self.conn.execute(
                        "INSERT INTO decisions(decision_id, student_id, incident_id, seq, decision, note_ru, operator,"
                        " created_at, created_at_us, supersedes) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (f"dec-{secrets.token_hex(10)}", student_id, incident_id, (latest["seq"] + 1) if latest else 1,
                         decision, note_ru, operator, now.isoformat(), _us(now), latest["decision_id"] if latest else None),
                    )
        self._emit(student_id, incident_id)
        return self.get_incident(student_id, incident_id)

    # ------------------------------------------------------------------ internals
    def _require_incident(self, student_id: str, incident_id: str, status: int = 404) -> sqlite3.Row:
        if not ID_RE.fullmatch(student_id or "") or not ID_RE.fullmatch(incident_id or ""):
            raise ReviewError(422, "invalid_id", "Некорректный идентификатор")
        row = self.conn.execute(
            "SELECT * FROM incidents WHERE student_id=? AND incident_id=?", (student_id, incident_id)
        ).fetchone()
        if row is None:
            raise ReviewError(status, "incident_not_found", "Эпизод не найден у этого студента")
        return row

    def _view(self, r: sqlite3.Row) -> dict[str, Any]:
        sid, iid = r["student_id"], r["incident_id"]
        provenance = self._provenance(sid, iid)
        first = provenance[0] if provenance else {}
        history = [
            {
                "decision_id": d["decision_id"],
                "decision": d["decision"],
                "note_ru": d["note_ru"],
                "operator": d["operator"],
                "created_at": d["created_at"],
                "supersedes": d["supersedes"],
            }
            for d in self.conn.execute(
                "SELECT * FROM decisions WHERE student_id=? AND incident_id=? ORDER BY seq", (sid, iid)
            )
        ]
        latest = history[-1] if history else None
        return {
            # qorgau.class.v1 §3.1 field names (what T02's panel normalises)
            "incident_id": iid,
            "student_id": sid,
            "seq": r["last_seq"],
            "rule_id": r["rule_id"],
            "category": r["category"],
            "priority": r["priority"],
            "state": r["state"],
            "t_start_wall": r["t_start_wall"],
            "duration_ms": r["duration_ms"],
            "explanation_ru": r["explanation_ru"],
            "clip_available": bool(r["clip_available"]),
            "decision": latest["decision"] if latest else None,
            # T03 additions
            "decision_note_ru": latest["note_ru"] if latest else None,
            "decision_history": history,
            "clip": self._clip_state(r),
            "snapshot_available": bool(r["snapshot_file"]),
            "class_session_id": r["class_session_id"],
            "session_id": r["class_session_id"],
            "origin": first.get("origin", "unknown"),
            "source_mode": first.get("source_mode", "unknown"),
            "source_session_id": first.get("source_session_id"),
            "event_provenance": provenance,
            "updated_at": r["updated_at"],
        }

    def _provenance(self, student_id: str, incident_id: str) -> list[dict[str, Any]]:
        events = [json.loads(row["event_json"]) for row in self.conn.execute(
            "SELECT event_json FROM canonical_events WHERE student_id=? AND incident_id=? ORDER BY review_seq",
            (student_id, incident_id))]
        return [{"event_id": e["event_id"], "client_run_id": e.get("client_run_id"), "seq": e.get("seq"),
                 "session_id": e.get("session_id"), "origin": e.get("origin", "unknown"),
                 "source_mode": e.get("payload", {}).get("source_mode") or "unknown",
                 "source_session_id": e.get("payload", {}).get("source_session_id"),
                 "received_at": e.get("received_at"), "seq_conflict": e.get("seq_conflict", False)} for e in events]

    def expire_requests(self) -> None:
        """Persist timeout states and publish them, even when no teacher is polling."""
        now = now_utc()
        with self._lock:
            rows = self.conn.execute("SELECT DISTINCT student_id,incident_id FROM clip_requests WHERE status='pending'"
                                     " AND requested_at_us < ?", (_us(now) - int(self.config.clip_request_timeout_s * 1e6),)).fetchall()
            with db.transaction(self.conn):
                self.conn.execute("UPDATE clip_requests SET status='failed',reason_code='upload_timeout',"
                                  " reason_ru='Клип не получен вовремя. Можно запросить снова.',updated_at=?"
                                  " WHERE status='pending' AND requested_at_us < ?",
                                  (now.isoformat(), _us(now) - int(self.config.clip_request_timeout_s * 1e6)))
        for row in rows:
            self._emit(row["student_id"], row["incident_id"])

    def _clip_state(self, r: sqlite3.Row) -> dict[str, Any]:
        sid, iid = r["student_id"], r["incident_id"]
        clip = self.conn.execute("SELECT * FROM clips WHERE student_id=? AND incident_id=?", (sid, iid)).fetchone()
        if clip is not None:
            playable = clip["container"] == "mp4" and clip["codec"] in {"avc1", "avc3", "vp09", "av01"}
            return {
                "state": "available",
                "media_type": clip["media_type"],
                "codec": clip["codec"],
                "codec_label": CODEC_LABEL_RU.get(clip["codec"] or "", clip["codec"]),
                "browser_playable": playable,
                "duration_s": clip["duration_s"],
                "size_bytes": clip["size_bytes"],
                "sha256": clip["sha256"],
                "source": clip["source"],
                "uploaded_at": clip["uploaded_at"],
            }
        req = self.conn.execute(
            "SELECT * FROM clip_requests WHERE student_id=? AND incident_id=? ORDER BY request_id DESC LIMIT 1", (sid, iid)
        ).fetchone()
        if req is not None and req["status"] == "pending":
            waited = (_us(now_utc()) - req["requested_at_us"]) / 1e6
            if waited <= self.config.clip_request_timeout_s:
                return {"state": "loading", "requested_at": req["requested_at"], "timeout_s": self.config.clip_request_timeout_s}
            return {
                "state": "unavailable",
                "reason_code": "upload_timeout",
                "reason_ru": f"Клип не получен за {int(self.config.clip_request_timeout_s)} с. Можно запросить снова.",
                "can_request": True,
            }
        if req is not None and req["status"] == "failed":
            return {"state": "unavailable", "reason_code": req["reason_code"], "reason_ru": req["reason_ru"], "can_request": True}
        if not r["clip_available"]:
            return {
                "state": "unavailable",
                "reason_code": "not_recorded",
                "reason_ru": "На компьютере студента клип этого эпизода не сохранён.",
                "can_request": False,
            }
        return {"state": "not_requested", "can_request": True}


def to_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
