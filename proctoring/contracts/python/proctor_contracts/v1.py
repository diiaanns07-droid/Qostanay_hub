"""Qorgau Exam wire contracts v1 — the SINGLE SOURCE OF TRUTH.

Generated from this file (never edit by hand):
  * proctoring/contracts/schema/v1/qorgau.v1.schema.json  (JSON Schema 2020-12)
  * proctoring/contracts/ts/qorgau-v1.generated.ts        (TypeScript wire types)
Regenerate / verify:  python proctoring/contracts/tools/generate.py [--check]

Owner: A01. Other agents request changes in proctoring/handoffs/Axx/DEPENDENCIES.txt.
Semantics (time, coordinates, confidence, unknown, mirroring, versions, errors)
are normative in proctoring/coordination/CONTRACTS.md; field docs below are a summary.

Rules that apply to every model:
  * extra fields are rejected (extra="forbid"); a field rename is a contract change;
  * datetimes are timezone-aware UTC ISO-8601 strings on the wire ("wall_time");
    they are for display/report only and are NEVER used for ordering;
  * ordering/duration use t_session_ms (session timeline, see CONTRACTS.md §Time);
  * normalized coordinates are in [0, 1] of the UNMIRRORED frame, origin top-left;
  * "left"/"right" directions are SUBJECT-CENTRIC (the student's own left/right);
  * confidence = estimator score for that specific claim, NOT probability of cheating;
  * None/"unknown" means "not determined" — never "no violation" and never "violation".
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Union

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

CONTRACT_ID = "qorgau.v1"
CONTRACT_VERSION = "1.1.0"

# ---------------------------------------------------------------------------
# Constrained primitives
# ---------------------------------------------------------------------------

Id = Annotated[str, Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")]
"""Opaque identifier. Never a filesystem path, never user-controlled free text."""

Code = Annotated[str, Field(pattern=r"^[a-z0-9_.]{1,64}$")]
"""Machine-readable reason/message code (snake_case). UI maps codes to text."""

Unit = Annotated[float, Field(ge=0.0, le=1.0)]
"""A value in [0, 1]."""

SessionMs = Annotated[float, Field(ge=0.0)]
"""Milliseconds on the session timeline (see CONTRACTS.md §Time)."""

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

FactValue = Union[bool, int, float, str]


class Wire(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=False)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class SourceMode(StrEnum):
    """Where frames/observations come from. Always visible in UI and reports."""

    LIVE = "live"
    REPLAY = "replay"
    SYNTHETIC = "synthetic"


class ObservationStatus(StrEnum):
    OK = "ok"
    UNKNOWN = "unknown"  # estimator ran but could not determine the value
    DEGRADED = "degraded"  # value present but input quality is limited (see quality_flags)
    ERROR = "error"  # estimator failed on this input


class Component(StrEnum):
    BACKEND = "backend"
    CAPTURE = "capture"
    PHONE = "phone"
    ATTENTION = "attention"
    FUSION = "fusion"
    EVIDENCE = "evidence"
    ENVIRONMENT = "environment"
    AUDIO = "audio"  # 1.1
    IDENTITY = "identity"  # 1.1


class HealthStatus(StrEnum):
    STARTING = "starting"
    OK = "ok"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"  # missing module/model/device
    ERROR = "error"
    STOPPED = "stopped"


class PhoneSignalName(StrEnum):
    PHONE_VISIBLE = "phone_visible"
    PHONE_RAISED = "phone_raised"
    POSSIBLE_SCREEN_CAPTURE = "possible_screen_capture"


class SignalState(StrEnum):
    PRESENT = "present"
    ABSENT = "absent"
    UNKNOWN = "unknown"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"  # the view cannot show it (coverage gap)


class Direction(StrEnum):
    """Subject-centric direction (the student's own left/right)."""

    CENTER = "center"
    LEFT = "left"
    RIGHT = "right"
    UP = "up"
    DOWN = "down"
    UNKNOWN = "unknown"


class GazeMethod(StrEnum):
    HEAD_POSE_ONLY = "head_pose_only"
    LANDMARK_IRIS = "landmark_iris"
    FUSED = "fused"


class EnvironmentAction(StrEnum):
    SHORTCUT_ALT_TAB = "shortcut_alt_tab"
    SHORTCUT_CTRL_C = "shortcut_ctrl_c"
    SHORTCUT_CTRL_V = "shortcut_ctrl_v"
    SHORTCUT_CTRL_X = "shortcut_ctrl_x"
    SHORTCUT_CTRL_TAB = "shortcut_ctrl_tab"
    SHORTCUT_WIN = "shortcut_win"
    SHORTCUT_PRINT_SCREEN = "shortcut_print_screen"
    SHORTCUT_ALT_F4 = "shortcut_alt_f4"
    FOCUS_LOST = "focus_lost"
    FOCUS_REGAINED = "focus_regained"
    FOREIGN_WINDOW_FOREGROUND = "foreign_window_foreground"
    NEW_WINDOW_BLOCKED = "new_window_blocked"
    NAVIGATION_BLOCKED = "navigation_blocked"
    DEVTOOLS_BLOCKED = "devtools_blocked"
    CLIPBOARD_BLOCKED = "clipboard_blocked"
    DISPLAY_CHANGED = "display_changed"
    EXAM_MODE_ENGAGED = "exam_mode_engaged"
    EXAM_MODE_RELEASED = "exam_mode_released"
    ENFORCEMENT_ERROR = "enforcement_error"


class EnforcementResult(StrEnum):
    BLOCKED = "blocked"  # action prevented AND verified by the mechanism result
    DETECTED_ONLY = "detected_only"  # action happened, was only recorded
    ALLOWED = "allowed"  # informational (e.g. focus_regained, exam_mode_engaged)
    FAILED = "failed"  # mechanism tried and failed
    UNSUPPORTED = "unsupported"


class EnforcementScope(StrEnum):
    RENDERER = "renderer"
    WINDOW = "window"
    APP = "app"
    OS_SESSION = "os_session"


class CapabilityStatus(StrEnum):
    BLOCKED = "blocked"
    DETECTED_ONLY = "detected_only"
    UNSUPPORTED = "unsupported"
    UNVERIFIED = "unverified"


class IncidentRule(StrEnum):
    PHONE_VISIBLE = "phone_visible"
    PHONE_RAISED = "phone_raised"
    POSSIBLE_SCREEN_CAPTURE = "possible_screen_capture"
    GAZE_PROLONGED_DOWN = "gaze_prolonged_down"
    GAZE_PROLONGED_SIDE = "gaze_prolonged_side"
    FACE_MISSING = "face_missing"
    MULTIPLE_FACES = "multiple_faces"
    ENVIRONMENT_BLOCKED_ACTION = "environment_blocked_action"
    ENVIRONMENT_ESCAPE = "environment_escape"
    MONITORING_DEGRADED = "monitoring_degraded"
    # 1.1 (additive)
    BACKGROUND_SPEECH = "background_speech"  # possible speech/conversation near the student (levels only)
    HEADPHONES_VISIBLE = "headphones_visible"
    IDENTITY_MISMATCH = "identity_mismatch"  # face differs from the one enrolled at exam start
    FOREIGN_OBJECT_VISIBLE = "foreign_object_visible"  # e.g. COCO "book"; review only
    SECOND_SCREEN_VISIBLE = "second_screen_visible"  # e.g. COCO "laptop"/"tv"; review only


class IncidentCategory(StrEnum):
    PHONE = "phone"
    ATTENTION = "attention"
    PRESENCE = "presence"
    ENVIRONMENT = "environment"
    TECHNICAL = "technical"
    AUDIO = "audio"  # 1.1
    IDENTITY = "identity"  # 1.1
    OBJECTS = "objects"  # 1.1


class ReviewPriority(StrEnum):
    """Priority of HUMAN REVIEW, from transparent rules. Not a probability of guilt."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class IncidentState(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class IncidentEndReason(StrEnum):
    CONDITION_CLEARED = "condition_cleared"
    SESSION_FINISHED = "session_finished"
    SESSION_PAUSED = "session_paused"
    SESSION_ABORTED = "session_aborted"
    SOURCE_LOST = "source_lost"
    MERGED = "merged"


class IncidentChangeType(StrEnum):
    OPENED = "opened"
    UPDATED = "updated"
    CLOSED = "closed"


class ReviewDecision(StrEnum):
    CONFIRMED = "confirmed"
    DISMISSED = "dismissed"
    INCONCLUSIVE = "inconclusive"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    DISMISSED = "dismissed"
    INCONCLUSIVE = "inconclusive"


class SessionState(StrEnum):
    CREATED = "created"
    PREFLIGHT = "preflight"
    CALIBRATING = "calibrating"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    FINISHED = "finished"
    ABORTED = "aborted"
    FAILED = "failed"


class CheckStatus(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    NOT_RUN = "not_run"


class PreflightCheckId(StrEnum):
    BACKEND = "backend"
    CAMERA = "camera"
    LIGHTING = "lighting"
    PHONE_MODEL = "phone_model"
    FACE_MODEL = "face_model"
    FUSION = "fusion"
    STORAGE = "storage"
    ENVIRONMENT_PROTECTION = "environment_protection"
    OFFLINE_ASSETS = "offline_assets"


class CalibrationTarget(StrEnum):
    CENTER = "center"
    LEFT = "left"
    RIGHT = "right"
    UP = "up"
    DOWN = "down"


class CalibrationPhase(StrEnum):
    NOT_STARTED = "not_started"
    COLLECTING = "collecting"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class CalibrationTargetState(StrEnum):
    PENDING = "pending"
    COLLECTING = "collecting"
    OK = "ok"
    FAILED = "failed"


class QuestionKind(StrEnum):
    SINGLE_CHOICE = "single_choice"
    MULTI_CHOICE = "multi_choice"
    SHORT_TEXT = "short_text"


class EvidenceKind(StrEnum):
    SNAPSHOT = "snapshot"
    CLIP = "clip"


class ErrorCode(StrEnum):
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN_ORIGIN = "FORBIDDEN_ORIGIN"
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    NOT_FOUND = "NOT_FOUND"
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    SESSION_ACTIVE = "SESSION_ACTIVE"  # another session holds the camera
    SESSION_MISMATCH = "SESSION_MISMATCH"
    INVALID_STATE = "INVALID_STATE"  # transition not allowed from current state
    PREFLIGHT_FAILED = "PREFLIGHT_FAILED"
    CAMERA_UNAVAILABLE = "CAMERA_UNAVAILABLE"
    CAMERA_BUSY = "CAMERA_BUSY"
    CAMERA_DENIED = "CAMERA_DENIED"
    REPLAY_INVALID = "REPLAY_INVALID"
    MODEL_MISSING = "MODEL_MISSING"
    MODEL_INVALID = "MODEL_INVALID"
    MODULE_NOT_INTEGRATED = "MODULE_NOT_INTEGRATED"
    CALIBRATION_FAILED = "CALIBRATION_FAILED"
    STORAGE_ERROR = "STORAGE_ERROR"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    INTERNAL = "INTERNAL"


# ---------------------------------------------------------------------------
# Shared building blocks
# ---------------------------------------------------------------------------


class BBox(Wire):
    """Normalized box in the UNMIRRORED frame: x right, y down, origin top-left."""

    x_min: Unit
    y_min: Unit
    x_max: Unit
    y_max: Unit

    @model_validator(mode="after")
    def _ordered(self) -> "BBox":
        if self.x_min > self.x_max or self.y_min > self.y_max:
            raise ValueError("bbox must satisfy x_min<=x_max and y_min<=y_max")
        return self


class Producer(Wire):
    """Who produced a record. model_* is set when an ML model was involved."""

    module: Annotated[str, Field(pattern=r"^[a-z0-9_.]{1,64}$")]  # e.g. "phone", "bootstrap.synthetic"
    version: Annotated[str, Field(min_length=1, max_length=32)]
    model_id: Id | None = None
    model_sha256: Sha256 | None = None
    config_version: Annotated[str, Field(max_length=64)] | None = None


class Health(Wire):
    component: Component
    status: HealthStatus
    code: Code  # e.g. "ok", "model_missing", "camera_disconnected", "module_not_integrated"
    message: Annotated[str, Field(max_length=500)] = ""
    since_t_session_ms: SessionMs | None = None
    details: dict[str, FactValue] = Field(default_factory=dict)


class ApiErrorBody(Wire):
    code: ErrorCode
    message: Annotated[str, Field(max_length=1000)]
    retryable: bool = False
    details: dict[str, FactValue] = Field(default_factory=dict)


class ApiError(Wire):
    """Body of every non-2xx response."""

    error: ApiErrorBody


# ---------------------------------------------------------------------------
# Frames
# ---------------------------------------------------------------------------


class FramePacketMeta(Wire):
    """Metadata of a captured frame. Pixels travel in-process only (see interfaces.FramePacket)."""

    session_id: Id
    frame_id: Annotated[int, Field(ge=0)]  # strictly increasing within a session, starts at 0
    t_session_ms: SessionMs
    wall_time: AwareDatetime
    t_capture_mono_ns: Annotated[int, Field(ge=0)]  # process-local monotonic clock, latency only
    width: Annotated[int, Field(gt=0, le=7680)]
    height: Annotated[int, Field(gt=0, le=4320)]
    color_format: Literal["BGR"] = "BGR"
    mirrored: Literal[False] = False  # backend frames are NEVER mirrored; UI mirrors for display
    source_mode: SourceMode
    source_id: Annotated[str, Field(max_length=128)]  # "camera:0", "replay:<replay_id>", "synthetic:bootstrap"


class PreviewFrameMeta(Wire):
    """Header of a binary preview message (see CONTRACTS.md §Preview transport)."""

    session_id: Id
    frame_id: Annotated[int, Field(ge=0)]
    t_session_ms: SessionMs
    wall_time: AwareDatetime
    width: Annotated[int, Field(gt=0)]
    height: Annotated[int, Field(gt=0)]
    mirrored: Literal[False] = False
    source_mode: SourceMode
    media_type: Literal["image/jpeg"] = "image/jpeg"
    byte_length: Annotated[int, Field(gt=0, le=4_000_000)]


# ---------------------------------------------------------------------------
# Observations (raw, per frame/event) — produced by A03/A04/A06/A02, consumed by A05/A08/A07
# ---------------------------------------------------------------------------


class ObservationBase(Wire):
    observation_id: Id  # unique within the session; re-delivery uses the same id
    session_id: Id
    frame_id: Annotated[int, Field(ge=0)] | None  # None only for environment/health
    t_session_ms: SessionMs
    wall_time: AwareDatetime
    source_mode: SourceMode
    producer: Producer
    status: ObservationStatus
    quality: Unit | None = None  # input/measurement quality, independent from confidence
    quality_flags: list[Code] = Field(default_factory=list, max_length=16)
    latency_ms: Annotated[float, Field(ge=0)] | None = None  # capture -> observation produced, measured


class PhoneDetection(Wire):
    bbox: BBox
    confidence: Unit  # detector score for "cell phone" in this box
    class_name: Annotated[str, Field(max_length=64)]  # from the model's names, e.g. "cell phone"
    class_index: Annotated[int, Field(ge=0)]
    track_id: Id | None = None
    track_quality: Unit | None = None  # association quality, separate from detection confidence
    track_age_ms: Annotated[float, Field(ge=0)] | None = None


class PhoneSignal(Wire):
    name: PhoneSignalName
    state: SignalState
    confidence: Unit | None = None
    track_id: Id | None = None
    reason: Code  # e.g. "track_rose_into_upper_zone", "camera_side_not_observable"
    facts: dict[str, FactValue] = Field(default_factory=dict)  # numbers used by the heuristic


class PhoneObservation(ObservationBase):
    kind: Literal["phone"] = "phone"
    detections: list[PhoneDetection] = Field(default_factory=list, max_length=16)
    signals: list[PhoneSignal] = Field(default_factory=list, max_length=8)


class FaceBox(Wire):
    bbox: BBox
    confidence: Unit | None = None
    is_primary: bool = False


class HeadPose(Wire):
    """Degrees, subject-centric: +yaw = student turns to THEIR right, +pitch = up, +roll = tilt to their right."""

    yaw_deg: Annotated[float, Field(ge=-180, le=180)]
    pitch_deg: Annotated[float, Field(ge=-180, le=180)]
    roll_deg: Annotated[float, Field(ge=-180, le=180)]


class GazeEstimate(Wire):
    """Approximate gaze. NOT eye tracking, NOT evidence of cheating."""

    direction: Direction
    yaw_deg: Annotated[float, Field(ge=-180, le=180)] | None = None
    pitch_deg: Annotated[float, Field(ge=-180, le=180)] | None = None
    confidence: Unit | None = None
    method: GazeMethod
    calibrated: bool


class AttentionObservation(ObservationBase):
    kind: Literal["attention"] = "attention"
    face_count: Annotated[int, Field(ge=0, le=16)] | None  # None = could not determine
    faces: list[FaceBox] = Field(default_factory=list, max_length=16)
    primary_face_present: bool | None
    head_pose: HeadPose | None = None
    head_direction: Direction = Direction.UNKNOWN
    gaze: GazeEstimate | None = None
    calibration_id: Id | None = None
    reasons: list[Code] = Field(default_factory=list, max_length=16)


class EnvironmentDetail(Wire):
    """Allow-listed, non-sensitive details only. No window titles, no typed text, no clipboard content."""

    process_name: Annotated[str, Field(pattern=r"^[A-Za-z0-9 ._()-]{1,64}$")] | None = None  # basename only
    shortcut: Annotated[str, Field(max_length=32)] | None = None
    duration_ms: Annotated[float, Field(ge=0)] | None = None


class EnvironmentObservation(ObservationBase):
    """Created by the backend from EnvironmentEventIn (A06). frame_id is always None."""

    kind: Literal["environment"] = "environment"
    action: EnvironmentAction
    enforcement: EnforcementResult
    mechanism: Annotated[str, Field(pattern=r"^[a-z0-9_.:-]{1,64}$")]  # e.g. "electron.before_input_event"
    scope: EnforcementScope
    client_seq: Annotated[int, Field(ge=0)]
    client_wall_time: AwareDatetime
    detail: EnvironmentDetail = Field(default_factory=EnvironmentDetail)


class AudioObservation(ObservationBase):
    """1.1. Microphone features only; audio is never recorded or transmitted. frame_id is None."""

    kind: Literal["audio"] = "audio"
    voice_like: SignalState  # present = speech-like sound near the student (VAD), not "who" or "what"
    voice_probability: Unit | None = None
    rms_dbfs: Annotated[float, Field(ge=-200, le=0)] | None = None
    noise_floor_dbfs: Annotated[float, Field(ge=-200, le=0)] | None = None
    reasons: list[Code] = Field(default_factory=list, max_length=16)


class IdentityObservation(ObservationBase):
    """1.1. Is the primary face the same person as enrolled at exam start? Embeddings stay in memory."""

    kind: Literal["identity"] = "identity"
    same_person: SignalState  # present = matches the enrolled face, absent = differs, unknown = cannot tell
    similarity: Annotated[float, Field(ge=-1, le=1)] | None = None  # cosine similarity to the enrolled face
    enrolled: bool = False
    reasons: list[Code] = Field(default_factory=list, max_length=16)


class HealthObservation(ObservationBase):
    """Health change as an observation so fusion can open technical incidents / coverage gaps."""

    kind: Literal["health"] = "health"
    health: Health


Observation = Annotated[
    Union[PhoneObservation, AttentionObservation, EnvironmentObservation, HealthObservation, AudioObservation, IdentityObservation],
    Field(discriminator="kind"),
]


# ---------------------------------------------------------------------------
# Incidents (aggregated episodes) — produced by A05 only
# ---------------------------------------------------------------------------


class ExplanationFact(Wire):
    key: Code
    value: FactValue
    unit: Literal["ms", "s", "count", "ratio", "deg", "none"] = "none"
    label_ru: Annotated[str, Field(max_length=200)]


class Explanation(Wire):
    """Human text is rendered by code from facts; every number in summary_ru comes from facts."""

    summary_ru: Annotated[str, Field(max_length=1000)]
    summary_kk: Annotated[str, Field(max_length=1000)] | None = None  # needs language review
    facts: list[ExplanationFact] = Field(default_factory=list, max_length=32)
    caveats_ru: list[Annotated[str, Field(max_length=300)]] = Field(default_factory=list, max_length=8)


class Incident(Wire):
    incident_id: Id
    session_id: Id
    rule_id: IncidentRule
    category: IncidentCategory
    state: IncidentState
    priority: ReviewPriority
    t_start_ms: SessionMs
    t_end_ms: SessionMs | None = None
    wall_start: AwareDatetime
    wall_end: AwareDatetime | None = None
    duration_ms: Annotated[float, Field(ge=0)]
    source_mode: SourceMode
    max_confidence: Unit | None = None  # max model confidence over contributing observations
    mean_quality: Unit | None = None
    explanation: Explanation
    observation_ids: list[Id] = Field(default_factory=list, max_length=200)  # representative refs
    observation_count: Annotated[int, Field(ge=0)] = 0
    trigger_frame_id: Annotated[int, Field(ge=0)] | None = None  # frame used for evidence snapshot
    related_incident_ids: list[Id] = Field(default_factory=list, max_length=32)
    evidence_ids: list[Id] = Field(default_factory=list, max_length=32)  # filled by A08
    rule_version: Annotated[str, Field(min_length=1, max_length=64)]
    config_version: Annotated[str, Field(min_length=1, max_length=64)]
    end_reason: IncidentEndReason | None = None
    update_seq: Annotated[int, Field(ge=0)]  # +1 on every change of this incident
    review_status: ReviewStatus = ReviewStatus.PENDING  # denormalized by A08 from HumanReview


class IncidentChange(Wire):
    change: IncidentChangeType
    incident: Incident


# ---------------------------------------------------------------------------
# Human review — the decision belongs to the teacher, stored by A08
# ---------------------------------------------------------------------------


class HumanReviewCreate(Wire):
    decision: ReviewDecision
    comment: Annotated[str, Field(max_length=2000)] = ""
    operator: Annotated[str, Field(min_length=1, max_length=64)]  # label/pseudonym, not an account


class HumanReview(Wire):
    review_id: Id
    session_id: Id
    incident_id: Id
    decision: ReviewDecision
    comment: Annotated[str, Field(max_length=2000)]
    operator: Annotated[str, Field(max_length=64)]
    created_at: AwareDatetime
    supersedes_review_id: Id | None = None  # append-only history


class EvidenceItem(Wire):
    evidence_id: Id  # opaque; never a path
    session_id: Id
    incident_id: Id | None = None
    kind: EvidenceKind
    frame_id: Annotated[int, Field(ge=0)] | None = None
    t_session_ms: SessionMs
    media_type: Literal["image/jpeg", "video/mp4", "video/webm"]
    sha256: Sha256
    size_bytes: Annotated[int, Field(gt=0)]
    created_at: AwareDatetime


class IncidentDetail(Wire):
    incident: Incident
    reviews: list[HumanReview] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ModelManifest(Wire):
    """Committed next to the module code; weights live outside Git under settings.models_dir."""

    model_id: Id
    module: Literal["phone", "attention"]
    task: Annotated[str, Field(max_length=64)]  # e.g. "object_detection", "face_landmarker"
    file: Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*$", max_length=200)]
    format: Literal["onnx", "task", "tflite", "pt"]
    version: Annotated[str, Field(max_length=64)]
    source_url: Annotated[str, Field(pattern=r"^https://", max_length=500)]
    license: Annotated[str, Field(max_length=64)]
    sha256: Sha256
    size_bytes: Annotated[int, Field(gt=0)]
    input_size: list[Annotated[int, Field(gt=0)]] | None = None
    class_names: dict[str, str] | None = None  # copied from the model's own names
    prepared_at: AwareDatetime
    notes: Annotated[str, Field(max_length=1000)] = ""

    @model_validator(mode="after")
    def _relative_file(self) -> "ModelManifest":
        if any(part in (".", "..") for part in self.file.split("/")):
            raise ValueError("file must be a relative path inside models_dir without '.' or '..' segments")
        return self


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------


class SourceConfig(Wire):
    mode: SourceMode
    camera_index: Annotated[int, Field(ge=0, le=16)] = 0  # live only
    replay_id: Id | None = None  # replay only: id of an entry in settings.replay_dir (never a path)
    width: Annotated[int, Field(ge=160, le=1920)] = 640
    height: Annotated[int, Field(ge=120, le=1080)] = 480
    fps: Annotated[int, Field(ge=1, le=60)] = 30

    @model_validator(mode="after")
    def _replay_needs_id(self) -> "SourceConfig":
        if self.mode == SourceMode.REPLAY and not self.replay_id:
            raise ValueError("replay mode requires replay_id")
        return self


class ConsentRecord(Wire):
    accepted: bool
    text_version: Annotated[str, Field(min_length=1, max_length=32)]
    accepted_at: AwareDatetime


class SessionCreate(Wire):
    source: SourceConfig
    exam_id: Id
    student_label: Annotated[str, Field(max_length=64)] | None = None  # pseudonym; no national IDs
    consent: ConsentRecord
    retain_media: bool = False  # snapshots/clips only when explicitly enabled


class CalibrationTargetStatus(Wire):
    target: CalibrationTarget
    state: CalibrationTargetState
    samples: Annotated[int, Field(ge=0)] = 0
    required_samples: Annotated[int, Field(ge=0)] = 0
    quality: Unit | None = None
    message_code: Code | None = None


class CalibrationState(Wire):
    calibration_id: Id | None = None
    phase: CalibrationPhase
    targets: list[CalibrationTargetStatus] = Field(default_factory=list, max_length=8)
    current_target: CalibrationTarget | None = None
    message_code: Code | None = None
    updated_at: AwareDatetime


class SessionInfo(Wire):
    session_id: Id
    state: SessionState
    source: SourceConfig
    source_mode: SourceMode
    exam_id: Id
    student_label: Annotated[str, Field(max_length=64)] | None = None
    retain_media: bool
    created_at: AwareDatetime
    started_at: AwareDatetime | None = None  # exam start (running first time)
    finished_at: AwareDatetime | None = None
    exam_started_t_ms: SessionMs | None = None
    paused_total_ms: Annotated[float, Field(ge=0)] = 0.0
    calibration: CalibrationState
    contract_version: str = CONTRACT_VERSION
    backend_version: str
    last_error: ApiErrorBody | None = None


class PauseRequest(Wire):
    reason: Annotated[str, Field(min_length=1, max_length=200)]


class AbortRequest(Wire):
    reason: Annotated[str, Field(min_length=1, max_length=200)]


class CalibrationTargetRequest(Wire):
    target: CalibrationTarget


class CalibrationSkipRequest(Wire):
    reason: Annotated[str, Field(min_length=1, max_length=200)]


class PreflightCheck(Wire):
    check_id: PreflightCheckId
    status: CheckStatus
    required: bool
    message_code: Code
    message_ru: Annotated[str, Field(max_length=500)] = ""
    details: dict[str, FactValue] = Field(default_factory=dict)


class PreflightReport(Wire):
    session_id: Id
    source_mode: SourceMode
    checks: list[PreflightCheck]
    ready: bool  # True only if every required check is "pass"
    created_at: AwareDatetime


class CoverageGap(Wire):
    t_start_ms: SessionMs
    t_end_ms: SessionMs | None = None
    component: Component
    reason: Code  # "paused", "camera_disconnected", "model_unavailable", ...


class ReviewZone(StrEnum):
    """Review queue priority; grey is insufficient coverage, never clearance or guilt."""

    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"
    GREY = "grey"


class SessionSummary(Wire):
    session: SessionInfo
    observed_ms: Annotated[float, Field(ge=0)]
    paused_ms: Annotated[float, Field(ge=0)]
    gaps: list[CoverageGap] = Field(default_factory=list)
    incidents_total: Annotated[int, Field(ge=0)]
    incidents_by_rule: dict[str, int] = Field(default_factory=dict)
    reviews_by_decision: dict[str, int] = Field(default_factory=dict)
    limitations_ru: list[str] = Field(default_factory=list)
    # Contract 1.1: A05 assessment as served by A08; null means not calculated.
    review_zone: ReviewZone | None = None
    review_zone_reasons_ru: list[str] = Field(default_factory=list, max_length=3)
    review_zone_rule_version: str | None = None


class HealthReport(Wire):
    backend_version: str
    contract_version: str = CONTRACT_VERSION
    overall: HealthStatus
    components: list[Health]
    active_session_id: Id | None = None
    server_time: AwareDatetime


# ---------------------------------------------------------------------------
# Environment protection (A06 -> backend)
# ---------------------------------------------------------------------------


class EnvironmentEventIn(Wire):
    """Sent by Electron main (A06). Backend assigns observation_id/t_session_ms on receipt."""

    action: EnvironmentAction
    enforcement: EnforcementResult
    mechanism: Annotated[str, Field(pattern=r"^[a-z0-9_.:-]{1,64}$")]
    scope: EnforcementScope
    client_seq: Annotated[int, Field(ge=0)]  # strictly increasing per shell run; dedup key
    client_wall_time: AwareDatetime
    detail: EnvironmentDetail = Field(default_factory=EnvironmentDetail)


class EnvironmentEventBatch(Wire):
    session_id: Id
    events: list[EnvironmentEventIn] = Field(min_length=1, max_length=100)


class EnvironmentEventAck(Wire):
    accepted: Annotated[int, Field(ge=0)]
    duplicates: Annotated[int, Field(ge=0)]
    observation_ids: list[Id]


class EnvironmentCapability(Wire):
    action: EnvironmentAction
    status: CapabilityStatus
    mechanism: Annotated[str, Field(max_length=64)]
    verified_on: Annotated[str, Field(max_length=128)] | None = None  # exact OS build/edition/rights
    note_ru: Annotated[str, Field(max_length=300)] | None = None


class EnvironmentCapabilities(Wire):
    reported_at: AwareDatetime
    platform: Annotated[str, Field(max_length=128)]  # e.g. "win32 10.0.22631 Pro, standard user"
    shell_version: Annotated[str, Field(max_length=32)]
    exam_mode_supported: bool
    items: list[EnvironmentCapability] = Field(max_length=64)


# ---------------------------------------------------------------------------
# Exam content and answers (content: A10 data; storage: A08)
# ---------------------------------------------------------------------------


class QuestionOption(Wire):
    option_id: Id
    text: Annotated[str, Field(max_length=500)]


class Question(Wire):
    question_id: Id
    kind: QuestionKind
    prompt: Annotated[str, Field(max_length=4000)]
    options: list[QuestionOption] = Field(default_factory=list, max_length=12)
    max_length: Annotated[int, Field(gt=0, le=4000)] | None = None


class ExamDefinition(Wire):
    exam_id: Id
    title: Annotated[str, Field(max_length=200)]
    locale: Literal["ru", "kk"]
    duration_s: Annotated[int, Field(gt=0, le=4 * 3600)]
    is_demo: bool
    questions: list[Question] = Field(min_length=1, max_length=100)


class AnswerUpsert(Wire):
    value: Union[Annotated[str, Field(max_length=4000)], list[Id]]
    client_seq: Annotated[int, Field(ge=0)]  # last-writer-wins by client_seq


class AnswerRecord(Wire):
    session_id: Id
    question_id: Id
    value: Union[str, list[Id]]
    client_seq: Annotated[int, Field(ge=0)]
    saved_at: AwareDatetime


# ---------------------------------------------------------------------------
# Runtime metrics (A02 measures; nothing here is a target value)
# ---------------------------------------------------------------------------


class ConsumerMetrics(Wire):
    name: Annotated[str, Field(max_length=64)]
    processed_fps: Annotated[float, Field(ge=0)]
    frames_processed: Annotated[int, Field(ge=0)]
    frames_skipped: Annotated[int, Field(ge=0)]  # replaced by a newer frame before processing
    errors: Annotated[int, Field(ge=0)]
    frame_age_ms_p50: Annotated[float, Field(ge=0)] | None = None  # capture -> processing start
    frame_age_ms_p95: Annotated[float, Field(ge=0)] | None = None
    process_ms_p50: Annotated[float, Field(ge=0)] | None = None
    process_ms_p95: Annotated[float, Field(ge=0)] | None = None


class RuntimeMetrics(Wire):
    session_id: Id | None = None
    t_session_ms: SessionMs | None = None
    window_s: Annotated[float, Field(gt=0)]
    capture_fps: Annotated[float, Field(ge=0)]
    frames_captured: Annotated[int, Field(ge=0)]
    frames_dropped: Annotated[int, Field(ge=0)]
    consumers: list[ConsumerMetrics] = Field(default_factory=list)
    e2e_latency_ms_p50: Annotated[float, Field(ge=0)] | None = None  # capture -> observation published
    e2e_latency_ms_p95: Annotated[float, Field(ge=0)] | None = None


# ---------------------------------------------------------------------------
# Event stream (WebSocket /v1/stream, JSON text frames)
# ---------------------------------------------------------------------------


class HelloMsg(Wire):
    type: Literal["hello"] = "hello"
    contract: Literal["qorgau.v1"] = "qorgau.v1"
    contract_version: str = CONTRACT_VERSION
    backend_version: str
    server_time: AwareDatetime


class SessionStateMsg(Wire):
    type: Literal["session_state"] = "session_state"
    session: SessionInfo


class ObservationMsg(Wire):
    type: Literal["observation"] = "observation"
    observation: Observation


class IncidentMsg(Wire):
    type: Literal["incident"] = "incident"
    change: IncidentChange


class HealthMsg(Wire):
    type: Literal["health"] = "health"
    report: HealthReport


class MetricsMsg(Wire):
    type: Literal["metrics"] = "metrics"
    metrics: RuntimeMetrics


class CalibrationMsg(Wire):
    type: Literal["calibration"] = "calibration"
    session_id: Id
    calibration: CalibrationState


class StreamErrorMsg(Wire):
    type: Literal["error"] = "error"
    error: ApiErrorBody


StreamPayload = Annotated[
    Union[
        HelloMsg,
        SessionStateMsg,
        ObservationMsg,
        IncidentMsg,
        HealthMsg,
        MetricsMsg,
        CalibrationMsg,
        StreamErrorMsg,
    ],
    Field(discriminator="type"),
]


class StreamEnvelope(Wire):
    contract: Literal["qorgau.v1"] = "qorgau.v1"
    seq: Annotated[int, Field(ge=0)]  # per-connection, strictly increasing; gap = dropped (slow client)
    sent_at: AwareDatetime
    session_id: Id | None = None
    message: StreamPayload


# ---------------------------------------------------------------------------
# Export (A08)
# ---------------------------------------------------------------------------


class ExportFile(Wire):
    name: Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]{1,128}$")]
    media_type: Annotated[str, Field(max_length=64)]
    sha256: Sha256
    size_bytes: Annotated[int, Field(ge=0)]


class ExportManifest(Wire):
    """Integrity manifest. A hash is NOT tamper-proofing against the machine owner."""

    contract: Literal["qorgau.v1"] = "qorgau.v1"
    contract_version: str = CONTRACT_VERSION
    exported_at: AwareDatetime
    session_id: Id
    source_mode: SourceMode
    backend_version: str
    config_versions: dict[str, str] = Field(default_factory=dict)
    models: list[ModelManifest] = Field(default_factory=list)
    files: list[ExportFile] = Field(default_factory=list)
    limitations_ru: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Registry used by the generator, fixtures and compatibility tests
# ---------------------------------------------------------------------------

WIRE_MODELS: tuple[type[Wire], ...] = (
    ApiError,
    Health,
    HealthReport,
    FramePacketMeta,
    PreviewFrameMeta,
    PhoneObservation,
    AttentionObservation,
    EnvironmentObservation,
    HealthObservation,
    Incident,
    IncidentChange,
    IncidentDetail,
    HumanReviewCreate,
    HumanReview,
    EvidenceItem,
    ModelManifest,
    SourceConfig,
    SessionCreate,
    SessionInfo,
    SessionSummary,
    PauseRequest,
    AbortRequest,
    CalibrationState,
    CalibrationTargetRequest,
    CalibrationSkipRequest,
    PreflightReport,
    EnvironmentEventBatch,
    EnvironmentEventAck,
    EnvironmentCapabilities,
    ExamDefinition,
    AnswerUpsert,
    AnswerRecord,
    RuntimeMetrics,
    StreamEnvelope,
    ExportManifest,
)

__all__ = [name for name in dir() if not name.startswith("_")]


def utc_now() -> datetime:
    """Timezone-aware UTC now (wall clock, display only)."""
    from datetime import timezone

    return datetime.now(timezone.utc)
