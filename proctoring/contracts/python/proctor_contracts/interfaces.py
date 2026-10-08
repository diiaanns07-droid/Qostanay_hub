"""Qorgau Exam in-process Python interfaces v1 (owner: A01).

These Protocols are the ONLY allowed cross-module imports between backend modules:
    from proctor_contracts.v1 import ...          # wire models
    from proctor_contracts.interfaces import ...  # in-process types below
    from proctor.settings import Settings         # read-only configuration

Every module exposes ONE factory from its package __init__.py (see OWNERSHIP.json):
    proctor.capture   -> create_capture_service(settings) -> CaptureService           (A02)
    proctor.phone     -> create_phone_analyzer(settings) -> FrameAnalyzer             (A03)
    proctor.attention -> create_attention_analyzer(settings) -> AttentionAnalyzer     (A04)
    proctor.fusion    -> create_incident_engine(session_id, source_mode, settings)
                         -> IncidentEngine                                            (A05)
    proctor.evidence  -> create_evidence_store(settings) -> EvidenceStore             (A08)

Thread rules (normative, CONTRACTS.md §Threads):
  * CaptureService owns all capture threads and one worker thread per consumer.
  * FrameAnalyzer.process() is called ONLY from its consumer thread (never concurrently).
  * AttentionAnalyzer.calibration_*() are called from API threads -> must be thread-safe.
  * IncidentEngine methods are called ONLY from the single fusion thread owned by A01.
  * EvidenceStore.record_*() are called from the fusion thread; router handlers from the
    FastAPI threadpool/event loop -> the store must be thread-safe.
  * Nobody blocks the asyncio event loop with inference or disk I/O.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Callable, Protocol, Sequence, runtime_checkable

from .v1 import (
    CalibrationState,
    CalibrationTarget,
    ErrorCode,
    EvidenceItem,
    FramePacketMeta,
    Health,
    IncidentChange,
    IncidentEndReason,
    Observation,
    PreviewFrameMeta,
    RuntimeMetrics,
    SessionInfo,
    SourceConfig,
    SourceMode,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    import numpy as np
    from fastapi import APIRouter

    from proctor.settings import Settings


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ProctorError(Exception):
    """Base error. The API layer maps it to ApiError(code, message, retryable)."""

    http_status = 500

    def __init__(self, code: ErrorCode, message: str, *, retryable: bool = False, **details: Any):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = {k: v for k, v in details.items() if isinstance(v, (bool, int, float, str))}


class InvalidStateError(ProctorError):
    http_status = 409


class NotFoundError(ProctorError):
    http_status = 404


class CaptureError(ProctorError):
    """CAMERA_UNAVAILABLE | CAMERA_BUSY | CAMERA_DENIED | REPLAY_INVALID."""

    http_status = 503


class ModelError(ProctorError):
    """MODEL_MISSING | MODEL_INVALID."""

    http_status = 503


class StorageError(ProctorError):
    http_status = 503


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------


class SessionClock:
    """Session timeline. t_session_ms = 0 at session creation (POST /v1/sessions).

    * Live/synthetic: t_session_ms = (time.monotonic_ns() - origin_mono_ns) / 1e6.
    * Replay: the source maps media timestamps onto the timeline itself:
      t_session_ms = replay_start_t_ms + media_pts_ms (see CONTRACTS.md §Time).
    * Session time keeps running during pause; pauses are recorded as intervals.
    * wall_time is derived from origin_wall + t_session_ms (never from datetime.now() per frame),
      so a wall-clock jump during the session does not reorder anything.
    """

    __slots__ = ("origin_mono_ns", "origin_wall")

    def __init__(self, origin_mono_ns: int | None = None, origin_wall: datetime | None = None):
        self.origin_mono_ns = time.monotonic_ns() if origin_mono_ns is None else origin_mono_ns
        self.origin_wall = origin_wall or datetime.now(timezone.utc)
        if self.origin_wall.tzinfo is None:
            raise ValueError("origin_wall must be timezone-aware")

    def now_ms(self) -> float:
        return self.mono_to_session_ms(time.monotonic_ns())

    def mono_to_session_ms(self, mono_ns: int) -> float:
        return max(0.0, (mono_ns - self.origin_mono_ns) / 1e6)

    def wall_at(self, t_session_ms: float) -> datetime:
        return self.origin_wall + timedelta(milliseconds=t_session_ms)


# ---------------------------------------------------------------------------
# Frames
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FramePacket:
    """One captured frame shared by all consumers.

    image: numpy uint8 array, shape (height, width, 3), BGR, C-contiguous, READ-ONLY
           (image.flags.writeable == False). Consumers must copy before modifying.
           Never mirrored. Shared between consumers without copying.
    """

    meta: FramePacketMeta
    image: "np.ndarray"

    @property
    def session_id(self) -> str:
        return self.meta.session_id

    @property
    def frame_id(self) -> int:
        return self.meta.frame_id

    @property
    def t_session_ms(self) -> float:
        return self.meta.t_session_ms


FrameCallback = Callable[[FramePacket], None]
HealthListener = Callable[[Health], None]


@runtime_checkable
class CaptureService(Protocol):
    """A02. The single owner of the camera/replay/synthetic source in the whole product.

    Lifecycle: open() -> running/degraded -> close() -> open() again (restart is supported).
    At most one source is open at a time; open() while open raises
    CaptureError(SESSION_ACTIVE or CAMERA_BUSY).
    """

    def open(self, session_id: str, source: SourceConfig, clock: SessionClock) -> None:
        """Open the source and start the capture thread. Blocks only for device open (<~5 s).
        Raises CaptureError(CAMERA_UNAVAILABLE|CAMERA_BUSY|CAMERA_DENIED|REPLAY_INVALID)."""
        ...

    def close(self, timeout_s: float = 3.0) -> None:
        """Stop capture and consumer threads, release the device. Idempotent, never raises."""
        ...

    def add_consumer(self, name: str, callback: FrameCallback, *, max_fps: float | None = None) -> None:
        """Register a consumer. Each consumer runs on its OWN worker thread with a size-1
        latest-frame mailbox (older pending frame is replaced and counted as skipped).
        Exceptions from callback are caught, counted in metrics/health, and do not stop capture.
        May be called before or after open(); name must be unique."""
        ...

    def remove_consumer(self, name: str) -> None: ...

    def latest_preview(self) -> tuple[PreviewFrameMeta, bytes] | None:
        """Latest JPEG-encoded preview (unmirrored) at <= settings.preview_fps. Thread-safe."""
        ...

    def get_frame(self, frame_id: int) -> FramePacket | None:
        """Frame from a bounded in-memory ring buffer (settings.frame_ring_seconds), or None
        if evicted. Used by A08 for evidence snapshots only when retain_media is enabled."""
        ...

    def set_health_listener(self, listener: HealthListener | None) -> None:
        """listener(Health) is called on every capture state change (any thread)."""
        ...

    def health(self) -> Health: ...

    def metrics(self) -> RuntimeMetrics: ...


@runtime_checkable
class FrameAnalyzer(Protocol):
    """A03 phone / A04 attention. Pure consumer of FramePacket; never opens a camera."""

    name: str  # "phone" | "attention"

    def load(self) -> Health:
        """Load local weights (verify ModelManifest sha256). Never downloads. Called once at
        backend startup from a worker thread. Returns UNAVAILABLE health instead of raising
        when weights are missing."""
        ...

    def start_session(self, session_id: str, source_mode: SourceMode) -> None:
        """Reset per-session state (tracks, smoothing, calibration)."""
        ...

    def process(self, frame: FramePacket) -> Sequence[Observation]:
        """Analyze one frame. Called only from this analyzer's consumer thread. Must not mutate
        frame.image. Returned observations carry frame.session_id, frame.frame_id,
        frame.t_session_ms, frame.meta.wall_time, frame.meta.source_mode."""
        ...

    def end_session(self) -> None:
        """Drop all per-session state including personal calibration data."""
        ...

    def health(self) -> Health: ...

    def close(self) -> None: ...


@runtime_checkable
class AttentionAnalyzer(FrameAnalyzer, Protocol):
    """A04. Calibration methods are called from API threads (must be thread-safe); samples are
    taken from frames delivered to process(). Completion is decided by sample count/quality,
    never by a timer."""

    def calibration_start(self) -> CalibrationState: ...

    def calibration_target(self, target: CalibrationTarget) -> CalibrationState: ...

    def calibration_state(self) -> CalibrationState: ...

    def calibration_finish(self) -> CalibrationState: ...

    def calibration_cancel(self) -> CalibrationState: ...

    def calibration_skip(self, reason: str) -> CalibrationState: ...


@runtime_checkable
class IncidentEngine(Protocol):
    """A05. One instance per session; deterministic; no I/O, no camera, no UI, no DB.
    Same observation sequence + config => same IncidentChange sequence, independent of
    wall-clock or replay speed (uses t_session_ms only)."""

    rule_version: str
    config_version: str

    def consume(self, observation: Observation) -> list[IncidentChange]: ...

    def advance(self, t_session_ms: float) -> list[IncidentChange]:
        """Called by A01 about every 250 ms of session time (TTL expiry, timeouts)."""
        ...

    def set_paused(self, paused: bool, t_session_ms: float) -> list[IncidentChange]: ...

    def finish(self, t_session_ms: float, reason: IncidentEndReason) -> list[IncidentChange]:
        """Close every open incident. Further consume() calls return []."""
        ...

    def config_snapshot(self) -> dict[str, Any]:
        """JSON-serializable thresholds actually used (stored in the report)."""
        ...


@runtime_checkable
class BackendContext(Protocol):
    """What A01 exposes to the A08 router (read-only views)."""

    def get_session(self, session_id: str) -> SessionInfo | None: ...

    def active_session_id(self) -> str | None: ...

    def capture(self) -> CaptureService | None: ...


@runtime_checkable
class EvidenceStore(Protocol):
    """A08. SQLite + media outside the repo (settings.data_dir). Thread-safe."""

    def open(self) -> Health:
        """Create/migrate the database. Returns health (never raises for disk errors)."""
        ...

    def close(self) -> None: ...

    def upsert_session(self, info: SessionInfo) -> None: ...

    def record_observation(self, observation: Observation) -> None:
        """Called for EVERY observation during running; A08 keeps only what is needed."""
        ...

    def record_incident_change(self, change: IncidentChange) -> None:
        """Idempotent by (incident_id, update_seq)."""
        ...

    def capture_snapshot(self, session_id: str, incident_id: str, frame: FramePacket) -> EvidenceItem | None:
        """Store a JPEG for the incident if the session has retain_media=True, else return None."""
        ...

    def create_router(self, context: BackendContext) -> "APIRouter":
        """FastAPI router with the A08 routes from CONTRACTS.md §HTTP API (paths relative to /v1).
        A01 mounts it with authentication; the router must not add its own auth bypass."""
        ...

    def delete_session(self, session_id: str) -> None: ...

    def health(self) -> Health: ...


# Factory signatures (documentation + type checking of module entry points)
CaptureFactory = Callable[["Settings"], CaptureService]
PhoneFactory = Callable[["Settings"], FrameAnalyzer]
AttentionFactory = Callable[["Settings"], AttentionAnalyzer]
IncidentEngineFactory = Callable[[str, SourceMode, "Settings"], IncidentEngine]
EvidenceFactory = Callable[["Settings"], EvidenceStore]
