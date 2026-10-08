"""Qorgau Classroom contracts — single source of truth (owner: T01, Pydantic v2).

Surfaces
--------
1. STUDENT WIRE  `qorgau.class.v1` — WebSocket /ws/student + HTTP /api/student/* between the student app (C2) and
   the class server. Frozen in proctoring/contracts/class/PROTOCOL_v1.md (owner A01). Everything marked
   "v1.1" below is an OPTIONAL additive field/message: a plain v1 peer may omit it and must ignore it.
   Inbound student messages ignore unknown fields (forward compatibility, v1 §3); outbound are exact.
2. TEACHER API   `qorgau.classroom` 1.0.0 — REST /api/teacher/* + WS /ws/teacher between the class server and
   the teacher console (T02–T05 modules). Not covered by v1; defined here. Request bodies forbid unknown fields.

Rules that every surface keeps
------------------------------
* Ids are opaque `^[A-Za-z0-9._:-]{1,128}$` strings, never paths.
* Every event has a unique id, the time the student says it happened (`event_time`, client clock) and the time the
  server received it (`received_at`, server clock). Ordering on the server uses `received_at`; client times are
  shown, never trusted for security decisions.
* A command has `command_id`, `expires_at` and a status history. "sent" means written to the student's socket —
  it is NOT "executed". Only an ack with ok=true makes it `succeeded`; ok=false makes it `failed`.
* Video never travels inside JSON: clips are uploaded/downloaded as binary HTTP bodies; the teacher stream only
  carries preview METADATA + a URL (inline base64 only on explicit legacy opt-in, previews ≤ 30 KB).
* Data from the simulator carries `origin = "simulated"` everywhere; it is test data, not a camera.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Any, Literal, Union

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

CONTRACT_ID = "qorgau.classroom"
CONTRACT_VERSION = "1.0.0"
WIRE_PROTOCOL = "qorgau.class.v1"
WIRE_EXTENSION = "1.1"  # additive optional fields on top of the frozen v1 wire (see module docstring)

Id = Annotated[str, Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")]
JoinCode = Annotated[str, Field(pattern=r"^[0-9]{6}$")]
Token = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]  # 32 random bytes, hex (v1 §2.4)
ShortText = Annotated[str, Field(max_length=64)]
RuText = Annotated[str, Field(max_length=1000)]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

MAX_WS_MESSAGE_BYTES = 256 * 1024  # v1 §3
MAX_PREVIEW_JPEG_BYTES = 30 * 1024  # v1 §3.1 preview
MAX_SNAPSHOT_JPEG_BYTES = 40 * 1024  # v1 §3.1 incident.snapshot_jpeg_b64
MAX_CLIP_BYTES = 8 * 1024 * 1024  # v1 §5
CLIP_MEDIA_TYPES = ("video/mp4", "video/x-msvideo")


def _b64_len(raw_bytes: int) -> int:
    return 4 * ((raw_bytes + 2) // 3)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class _Out(BaseModel):
    """Server-produced message/record: exact shape."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class _In(BaseModel):
    """Student-produced wire message: unknown fields are ignored (additive evolution, v1 §3)."""

    model_config = ConfigDict(extra="ignore", frozen=True)


class _Body(BaseModel):
    """Teacher request body: strict."""

    model_config = ConfigDict(extra="forbid", frozen=True)


# ---------------------------------------------------------------------------------------------------- enums
class DataOrigin(StrEnum):
    REAL = "real"  # a real student app (C2) — still not proof that its camera works; see DeviceStatus.camera
    SIMULATED = "simulated"  # classroom.simulator or any client that says hello.simulated=true


class ConnectionState(StrEnum):
    ONLINE = "online"
    OFFLINE = "offline"


class ExamState(StrEnum):
    IDLE = "idle"
    PREFLIGHT = "preflight"
    CALIBRATING = "calibrating"
    RUNNING = "running"
    PAUSED = "paused"
    FINISHED = "finished"


class CameraState(StrEnum):
    OK = "ok"
    BUSY = "busy"
    OFF = "off"
    UNKNOWN = "unknown"


class MonitoringState(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"


class Zone(StrEnum):
    GREEN = "green"
    YELLOW = "yellow"  # shown ORANGE in the UI (v1 §4); wire value stays "yellow"
    RED = "red"
    GREY = "grey"  # not enough data — never "all clear"


class ZoneSource(StrEnum):
    STUDENT = "student"  # zone reported by the student app (A05 assess_session_zone)
    SERVER_STALE = "server_stale"  # server forced grey: no status for > status_stale_s
    SERVER_CAMERA = "server_camera"  # server forced grey: camera != ok
    SERVER_OFFLINE = "server_offline"  # server forced grey: student offline
    NONE = "none"  # student never sent a zone


class Priority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class IncidentState(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class ExamMode(StrEnum):
    URL = "url"  # exam on an external website
    APP = "app"  # exam in a separate program


class CommandKind(StrEnum):
    START_EXAM = "start_exam"
    FINISH_EXAM = "finish_exam"
    LOCK = "lock"
    UNLOCK = "unlock"
    REQUEST_CLIP = "request_clip"
    AUDIO_START = "audio_start"
    AUDIO_STOP = "audio_stop"
    APPLY_POLICY = "apply_policy"  # v1.1 (T04): only sent to clients whose hello.capabilities.commands lists it


V1_COMMAND_KINDS = frozenset(k for k in CommandKind if k != CommandKind.APPLY_POLICY)


class CommandStatus(StrEnum):
    QUEUED = "queued"  # accepted by the server, student not reachable yet (offline) — will be sent on reconnect
    SENT = "sent"  # written to the student's socket; NOT executed
    RECEIVED = "received"  # v1.1 command_progress from the client: it has the command
    SUCCEEDED = "succeeded"  # ack ok=true — the client says it executed it
    FAILED = "failed"  # ack ok=false (see ack.code / error_ru)
    EXPIRED = "expired"  # no ack before expires_at; never (re)sent after expiry
    CANCELLED = "cancelled"  # teacher cancelled while still queued


TERMINAL_COMMAND_STATUSES = frozenset({CommandStatus.SUCCEEDED, CommandStatus.FAILED, CommandStatus.EXPIRED, CommandStatus.CANCELLED})


class AckCode(StrEnum):  # v1.1 machine reason for ok=false (T04 R2)
    UNSUPPORTED = "unsupported"
    INVALID = "invalid"
    EXPIRED = "expired"
    DUPLICATE = "duplicate"
    FAILED = "failed"
    BUSY = "busy"


class AudioDirection(StrEnum):
    LISTEN = "listen"
    TALK = "talk"
    BOTH = "both"


class AudioState(StrEnum):
    REQUESTED = "requested"  # audio_start command issued, not acked yet
    ACTIVE = "active"  # student acked audio_start ok (its mic indicator is on per v1 §3.2)
    ENDED = "ended"
    FAILED = "failed"


class EventKind(StrEnum):
    INCIDENT = "incident"


# ---------------------------------------------------------------------------------------------- shared blocks
class ExamPolicy(_Out):
    """What the student app opens and allows. `welcome.exam` carries the v1 subset (exam_id, title, mode,
    allowed_urls, allowed_apps, instructions_ru); the rest is v1.1 / T04."""

    exam_id: Id
    title: Annotated[str, Field(min_length=1, max_length=200)]
    mode: ExamMode
    allowed_urls: list[Annotated[str, Field(max_length=500)]] = Field(default_factory=list, max_length=100)
    allowed_apps: list[Annotated[str, Field(max_length=128)]] = Field(default_factory=list, max_length=100)
    instructions_ru: Annotated[str, Field(max_length=4000)] = ""
    policy_id: Id | None = None  # v1.1
    version: Annotated[int, Field(ge=1)] | None = None  # v1.1
    start_url: Annotated[str, Field(max_length=500)] | None = None  # v1.1
    auth_domains: list[Annotated[str, Field(max_length=253)]] = Field(default_factory=list, max_length=50)  # v1.1


class Capabilities(_In):
    """v1.1 hello.capabilities. Absent = plain v1 client: only the V1_COMMAND_KINDS are offered to it."""

    commands: list[str] = Field(default_factory=list, max_length=32)
    modes: list[str] = Field(default_factory=list, max_length=8)
    command_progress: bool = False
    command_expiry: bool = False
    site_timer_pause: bool = False


# ============================================================================== STUDENT WIRE: student -> server
class _Envelope(_In):
    v: Literal[1] = 1
    msg_id: Annotated[str, Field(min_length=1, max_length=64)]
    sent_at: AwareDatetime


class Hello(_Envelope):
    type: Literal["hello"]
    protocol: Literal["qorgau.class.v1"]
    join_code: JoinCode | None = None
    resume_token: Token | None = None
    computer_name: ShortText = ""
    student_label: ShortText = ""
    app_version: ShortText = ""
    client_run_id: Id | None = None  # v1.1: new value when the client's seq counter restarts
    simulated: bool = False  # v1.1: test client; the simulator always sets it
    capabilities: Capabilities | None = None  # v1.1

    @model_validator(mode="after")
    def _one_credential(self) -> "Hello":
        if (self.join_code is None) == (self.resume_token is None):
            raise ValueError("hello needs exactly one of join_code / resume_token")
        return self


class IncidentsByPriority(_Out):
    low: Annotated[int, Field(ge=0)] = 0
    medium: Annotated[int, Field(ge=0)] = 0
    high: Annotated[int, Field(ge=0)] = 0


class Status(_Envelope):
    type: Literal["status"]
    exam_state: ExamState
    camera: CameraState
    monitoring: MonitoringState
    zone: Zone | None = None
    zone_reasons_ru: list[Annotated[str, Field(max_length=300)]] = Field(default_factory=list, max_length=3)
    incidents_total: Annotated[int, Field(ge=0)] = 0
    incidents_by_priority: IncidentsByPriority = Field(default_factory=IncidentsByPriority)
    locked: bool = False
    mic_active: bool = False


class IncidentMsg(_Envelope):
    type: Literal["incident"]
    seq: Annotated[int, Field(ge=1)]
    incident_id: Id
    rule_id: Annotated[str, Field(min_length=1, max_length=64)]
    category: Annotated[str, Field(min_length=1, max_length=64)]
    priority: Priority
    state: IncidentState
    t_start_wall: AwareDatetime
    duration_ms: Annotated[float, Field(ge=0)] = 0.0
    explanation_ru: RuText = ""
    clip_available: bool = False
    snapshot_jpeg_b64: Annotated[str, Field(max_length=_b64_len(MAX_SNAPSHOT_JPEG_BYTES))] | None = None
    event_id: Id | None = None  # v1.1: client-unique id of this event; derived when absent (see server.events)


class Preview(_Envelope):
    type: Literal["preview"]
    jpeg_b64: Annotated[str, Field(min_length=4, max_length=_b64_len(MAX_PREVIEW_JPEG_BYTES))]
    frame_wall: AwareDatetime


class Ack(_Envelope):
    type: Literal["ack"]
    command_id: Id
    ok: bool
    error_ru: Annotated[str, Field(max_length=300)] | None = None
    seq: Annotated[int, Field(ge=1)] | None = None  # v1 §3.1: acks are queued by seq (C2 sends it); dedup stays by command_id
    code: AckCode | None = None  # v1.1
    executed_at: AwareDatetime | None = None  # v1.1
    result: dict[str, bool | int | float | str | None] | None = None  # v1.1, e.g. {"locked": true}


class CommandProgress(_Envelope):  # v1.1: not an ack (v1 rule "exactly one ack per command" is kept)
    type: Literal["command_progress"]
    command_id: Id
    state: Literal["received", "executing"]


class AudioSignalIn(_Envelope):
    type: Literal["audio_signal"]
    command_id: Id
    sdp: Annotated[str, Field(max_length=16_384)] | None = None
    ice: Annotated[str, Field(max_length=4_096)] | dict[str, Any] | None = None
    audio_session_id: Id | None = None  # v1.1


class Pong(_Envelope):
    type: Literal["pong"]


StudentMessage = Annotated[
    Union[Hello, Status, IncidentMsg, Preview, Ack, CommandProgress, AudioSignalIn, Pong],
    Field(discriminator="type"),
]


# ============================================================================== STUDENT WIRE: server -> student
class _OutEnvelope(_Out):
    v: Literal[1] = 1
    msg_id: Annotated[str, Field(min_length=1, max_length=64)]
    sent_at: AwareDatetime


class Welcome(_OutEnvelope):
    type: Literal["welcome"] = "welcome"
    student_id: Id
    resume_token: Token
    server_time: AwareDatetime
    exam: ExamPolicy
    session_id: Id | None = None  # v1.1
    resumed: bool = False  # v1.1: true when the hello carried a valid resume_token


class CommandMsg(_OutEnvelope):
    type: Literal["command"] = "command"
    command_id: Id
    kind: CommandKind
    payload: dict[str, Any]
    issued_at: AwareDatetime | None = None  # v1.1
    expires_at: AwareDatetime | None = None  # v1.1: a v1.1 client must not execute after this (ack code=expired)
    ttl_ms: Annotated[int, Field(ge=0)] | None = None  # v1.1: time LEFT at this delivery
    attempt: Annotated[int, Field(ge=1)] | None = None  # v1.1: re-deliveries keep the same command_id


class Ping(_OutEnvelope):
    type: Literal["ping"] = "ping"


class ErrorMsg(_OutEnvelope):
    type: Literal["error"] = "error"
    code: Annotated[str, Field(pattern=r"^[a-z0-9_]{1,64}$")]
    message_ru: Annotated[str, Field(max_length=300)]


class AudioSignalOut(_OutEnvelope):
    type: Literal["audio_signal"] = "audio_signal"
    command_id: Id
    sdp: Annotated[str, Field(max_length=16_384)] | None = None
    ice: Annotated[str, Field(max_length=4_096)] | dict[str, Any] | None = None
    audio_session_id: Id | None = None


ServerToStudentMessage = Annotated[Union[Welcome, CommandMsg, Ping, ErrorMsg, AudioSignalOut], Field(discriminator="type")]

# v1 §3.2 command payloads (validated by the server before anything is queued)
class LockPayload(_Body):
    reason_ru: Annotated[str, Field(min_length=1, max_length=200)]


class RequestClipPayload(_Body):
    incident_id: Id


class AudioStartPayload(_Body):
    direction: AudioDirection


class EmptyPayload(_Body):
    pass


COMMAND_PAYLOADS: dict[CommandKind, type[BaseModel]] = {
    CommandKind.START_EXAM: EmptyPayload,
    CommandKind.FINISH_EXAM: EmptyPayload,
    CommandKind.LOCK: LockPayload,
    CommandKind.UNLOCK: EmptyPayload,
    CommandKind.REQUEST_CLIP: RequestClipPayload,
    CommandKind.AUDIO_START: AudioStartPayload,
    CommandKind.AUDIO_STOP: EmptyPayload,
    CommandKind.APPLY_POLICY: ExamPolicy,
}


# ================================================================================== TEACHER API: records
class Student(_Out):
    student_id: Id
    session_id: Id
    student_label: ShortText
    computer_name: ShortText
    app_version: ShortText
    origin: DataOrigin
    connection: ConnectionState
    paired_at: AwareDatetime
    last_seen_at: AwareDatetime | None = None  # last message of any kind (incl. pong)
    connected_since: AwareDatetime | None = None
    reconnects: Annotated[int, Field(ge=0)] = 0
    capabilities: list[str] = Field(default_factory=list)  # command kinds the client can execute


class DeviceStatus(_Out):
    """Last `status` of a student + the server's view of it (v1 §4: server may force grey)."""

    student_id: Id
    received_at: AwareDatetime | None = None
    sent_at: AwareDatetime | None = None
    exam_state: ExamState | None = None
    camera: CameraState = CameraState.UNKNOWN
    monitoring: MonitoringState | None = None
    zone_reported: Zone | None = None
    zone: Zone = Zone.GREY  # effective zone shown to the teacher
    zone_source: ZoneSource = ZoneSource.NONE
    zone_reasons_ru: list[str] = Field(default_factory=list)
    incidents_total: Annotated[int, Field(ge=0)] = 0
    incidents_by_priority: IncidentsByPriority = Field(default_factory=IncidentsByPriority)
    locked: bool | None = None  # None = never reported
    mic_active: bool | None = None
    stale: bool = True  # no status for > status_stale_s (or never)


class StudentCard(_Out):
    """One card of the class panel = identity + device status + counters (answers T02 request 1)."""

    student: Student
    status: DeviceStatus
    connected: bool
    last_status_at: AwareDatetime | None = None
    last_event_at: AwareDatetime | None = None
    incidents_open: Annotated[int, Field(ge=0)] = 0
    incidents_unreviewed: Annotated[int, Field(ge=0)] | None = None  # filled by the history feature (T03) when mounted
    preview_url: str | None = None
    preview_at: AwareDatetime | None = None


class Session(_Out):
    session_id: Id
    title: Annotated[str, Field(min_length=1, max_length=200)]
    state: Literal["open", "closed"]
    created_at: AwareDatetime
    closed_at: AwareDatetime | None = None
    exam: ExamPolicy
    join_code: JoinCode | None = None  # shown to the teacher only while the session is open
    students_total: Annotated[int, Field(ge=0)] = 0


class ObservationEvent(_Out):
    """One event as received from a student (persisted, deduplicated)."""

    event_id: Id  # client event_id (v1.1) or derived "v1:<incident_id>:<state>"
    student_id: Id
    session_id: Id
    kind: EventKind
    seq: Annotated[int, Field(ge=1)] | None = None
    client_run_id: Id | None = None
    event_time: AwareDatetime  # when it happened, client clock (incident open: t_start_wall; close: + duration)
    sent_at: AwareDatetime | None = None  # client envelope time
    received_at: AwareDatetime  # server clock
    origin: DataOrigin
    seq_conflict: bool = False  # same (student, run, seq) arrived earlier with a DIFFERENT event; both kept
    payload: dict[str, Any]


class Incident(_Out):
    """Latest state of one episode as reported by the student app (A05 fusion on the student PC)."""

    incident_id: Id
    student_id: Id
    session_id: Id
    rule_id: str
    category: str
    priority: Priority
    state: IncidentState
    t_start_wall: AwareDatetime
    duration_ms: Annotated[float, Field(ge=0)]
    explanation_ru: str
    clip_available: bool
    has_snapshot: bool
    origin: DataOrigin
    first_received_at: AwareDatetime
    last_received_at: AwareDatetime
    events: Annotated[int, Field(ge=1)]


class ClipMetadata(_Out):
    """A clip uploaded by a student (binary HTTP, never JSON/base64). Storage/playback: T03."""

    clip_id: Id
    incident_id: Id
    student_id: Id
    media_type: Literal["video/mp4", "video/x-msvideo"]
    size_bytes: Annotated[int, Field(gt=0, le=MAX_CLIP_BYTES)]
    sha256: Sha256
    uploaded_at: AwareDatetime
    url: str  # GET with HTTP Range support, teacher only
    origin: DataOrigin


class CommandAck(_Out):
    command_id: Id
    ok: bool
    code: AckCode | None = None
    error_ru: str | None = None
    executed_at: AwareDatetime | None = None  # client clock (v1.1)
    received_at: AwareDatetime  # server clock
    late: bool = False  # arrived after expires_at (the client still says what it did)
    result: dict[str, bool | int | float | str | None] | None = None


class CommandStatusChange(_Out):
    status: CommandStatus
    at: AwareDatetime
    note: Annotated[str, Field(max_length=200)] | None = None


class Command(_Out):
    command_id: Id
    student_id: Id
    kind: CommandKind
    payload: dict[str, Any]
    issued_by: Annotated[str, Field(max_length=64)]
    issued_at: AwareDatetime
    expires_at: AwareDatetime
    status: CommandStatus
    status_at: AwareDatetime
    attempts: Annotated[int, Field(ge=0)] = 0  # times written to a socket
    sent_at: AwareDatetime | None = None
    ack_deadline_at: AwareDatetime | None = None  # sent_at + ack window (v1: 10 s)
    unconfirmed: bool = False  # sent/received, ack window passed, not expired yet ("команда не подтверждена")
    ack: CommandAck | None = None
    history: list[CommandStatusChange] = Field(default_factory=list)


class AudioSession(_Out):
    audio_session_id: Id
    student_id: Id
    direction: AudioDirection
    state: AudioState
    started_by: Annotated[str, Field(max_length=64)]
    start_command_id: Id
    stop_command_id: Id | None = None
    requested_at: AwareDatetime
    active_at: AwareDatetime | None = None
    ended_at: AwareDatetime | None = None
    end_reason: Annotated[str, Field(max_length=64)] | None = None  # stopped | ack_failed | expired | student_offline


class FeatureInfo(_Out):
    name: Id
    owner: Annotated[str, Field(max_length=8)]
    status: Literal["mounted", "not_installed", "failed"]
    detail: Annotated[str, Field(max_length=300)] = ""


class ServerInfo(_Out):
    contract: Literal["qorgau.classroom"] = CONTRACT_ID
    contract_version: str = CONTRACT_VERSION
    wire_protocol: Literal["qorgau.class.v1"] = WIRE_PROTOCOL
    wire_extension: str = WIRE_EXTENSION
    server_version: str
    server_time: AwareDatetime
    session: Session | None = None
    students_online: Annotated[int, Field(ge=0)] = 0
    students_total: Annotated[int, Field(ge=0)] = 0
    simulated_students: Annotated[int, Field(ge=0)] = 0
    features: list[FeatureInfo] = Field(default_factory=list)


# ============================================================================ TEACHER API: request bodies
class LoginRequest(_Body):
    pin: Annotated[str, Field(pattern=r"^[0-9]{6,10}$")]


class SessionCreate(_Body):
    title: Annotated[str, Field(min_length=1, max_length=200)]
    mode: ExamMode
    allowed_urls: list[Annotated[str, Field(max_length=500)]] = Field(default_factory=list, max_length=100)
    allowed_apps: list[Annotated[str, Field(max_length=128)]] = Field(default_factory=list, max_length=100)
    instructions_ru: Annotated[str, Field(max_length=4000)] = ""
    start_url: Annotated[str, Field(max_length=500)] | None = None


class SessionCreated(_Out):
    session_id: Id
    join_code: JoinCode
    session: Session


class CommandCreate(_Body):
    kind: CommandKind
    payload: dict[str, Any] = Field(default_factory=dict)
    ttl_ms: Annotated[int, Field(ge=1_000, le=3_600_000)] | None = None


class ApiErrorBody(_Out):
    code: Annotated[str, Field(pattern=r"^[a-z0-9_]{1,64}$")]
    message_ru: Annotated[str, Field(max_length=500)]
    details: dict[str, Any] = Field(default_factory=dict)


class ApiError(_Out):
    error: ApiErrorBody


# ============================================================================== TEACHER STREAM (/ws/teacher)
class _T(_Out):
    seq: Annotated[int, Field(ge=1)]  # per connection; a gap = this client lost messages -> refetch REST
    sent_at: AwareDatetime


class THello(_T):
    type: Literal["hello"] = "hello"
    info: ServerInfo


class TSnapshot(_T):
    type: Literal["snapshot"] = "snapshot"
    students: list[StudentCard]


class TStudentUpdate(_T):
    type: Literal["student_update"] = "student_update"
    student: StudentCard


class TIncident(_T):
    type: Literal["incident"] = "incident"
    student_id: Id
    incident: Incident
    duplicate: bool = False


class TPreview(_T):
    """Preview METADATA; fetch the JPEG from `url`. `jpeg_b64` only with ?inline_previews=1 (legacy v1 panel)."""

    type: Literal["preview"] = "preview"
    student_id: Id
    preview_seq: Annotated[int, Field(ge=1)]
    frame_wall: AwareDatetime
    received_at: AwareDatetime
    byte_length: Annotated[int, Field(gt=0, le=MAX_PREVIEW_JPEG_BYTES)]
    url: str
    origin: DataOrigin
    jpeg_b64: str | None = None


class TCommandUpdate(_T):
    type: Literal["command_update"] = "command_update"
    command: Command


class TAck(_T):  # v1 §5 legacy stream message, kept for v1 panels
    type: Literal["ack"] = "ack"
    student_id: Id
    command_id: Id
    ok: bool
    error_ru: str | None = None


class TAudioUpdate(_T):
    type: Literal["audio_session_update"] = "audio_session_update"
    audio: AudioSession


class TAudioSignal(_T):
    type: Literal["audio_signal"] = "audio_signal"
    student_id: Id
    command_id: Id
    audio_session_id: Id | None = None
    sdp: str | None = None
    ice: str | dict[str, Any] | None = None


class TSessionUpdate(_T):
    type: Literal["session_update"] = "session_update"
    session: Session | None


class TFeatureEvent(_T):
    """Generic channel for mounted features (T03 history changes, T04 exam/command views, T05 audio)."""

    type: Literal["feature_event"] = "feature_event"
    feature: Id
    event: Annotated[str, Field(pattern=r"^[a-z0-9_.]{1,64}$")]
    data: dict[str, Any]


class TResync(_T):
    type: Literal["resync_required"] = "resync_required"
    reason: Literal["queue_overflow", "server_restart"]


class TError(_T):
    """Answer to a rejected teacher->server message on /ws/teacher (the connection stays open)."""

    type: Literal["error"] = "error"
    code: Annotated[str, Field(pattern=r"^[a-z0-9_]{1,64}$")]
    message_ru: Annotated[str, Field(max_length=300)]


TeacherStreamMessage = Annotated[
    Union[THello, TSnapshot, TStudentUpdate, TIncident, TPreview, TCommandUpdate, TAck, TAudioUpdate, TAudioSignal, TSessionUpdate, TFeatureEvent, TResync, TError],
    Field(discriminator="type"),
]


class TeacherAudioSignal(_Body):
    """Teacher -> server over /ws/teacher (relayed to exactly one student; v1 §6)."""

    type: Literal["audio_signal"]
    student_id: Id
    command_id: Id
    audio_session_id: Id | None = None
    sdp: Annotated[str, Field(max_length=16_384)] | None = None
    ice: Annotated[str, Field(max_length=4_096)] | dict[str, Any] | None = None


# Models exported to JSON Schema / TypeScript (order = order in the generated files)
SCHEMA_MODELS: list[type[BaseModel]] = [
    ExamPolicy, Capabilities,
    Hello, Status, IncidentMsg, Preview, Ack, CommandProgress, AudioSignalIn, Pong,
    Welcome, CommandMsg, Ping, ErrorMsg, AudioSignalOut,
    LockPayload, RequestClipPayload, AudioStartPayload, EmptyPayload,
    Student, DeviceStatus, StudentCard, Session, ObservationEvent, Incident, ClipMetadata,
    CommandAck, CommandStatusChange, Command, AudioSession, FeatureInfo, ServerInfo,
    LoginRequest, SessionCreate, SessionCreated, CommandCreate, ApiErrorBody, ApiError,
    THello, TSnapshot, TStudentUpdate, TIncident, TPreview, TCommandUpdate, TAck, TAudioUpdate, TAudioSignal,
    TSessionUpdate, TFeatureEvent, TResync, TError, TeacherAudioSignal,
]
UNIONS: dict[str, Any] = {
    "StudentMessage": StudentMessage,
    "ServerToStudentMessage": ServerToStudentMessage,
    "TeacherStreamMessage": TeacherStreamMessage,
}
