// FixtureBridge — DEVELOPMENT ONLY implementation of QorgauBridge (transport = "fixture").
//
// * Seeds from the frozen contract fixtures (@contracts/fixtures.generated) and follows the backend lifecycle
//   rules of CONTRACTS.md §2 (INVALID_STATE / SESSION_ACTIVE / PREFLIGHT_FAILED errors included).
// * Every record is source_mode "synthetic", producer.module "fixture.*"; LIVE/REPLAY sessions FAIL preflight
//   here because there is no camera and no CV module behind this bridge (no silent substitution).
// * No camera is opened. Preview frames are vector drawings with a burned-in "FIXTURE" label.
// * Faults (backend loss, camera loss, save errors, calibration failure) can be injected from the dev panel to
//   exercise real error states in the UI.
import { BRIDGE_VERSION } from "@contracts/bridge";
import type {
  BridgeResult,
  EvidenceBlob,
  QorgauBridge,
  SavedExport,
  ShellState,
  Unsubscribe,
} from "@contracts/bridge";
import { fixtures } from "@contracts/fixtures.generated";
import type {
  AbortRequest,
  AnswerRecord,
  AnswerUpsert,
  ApiErrorBody,
  AttentionObservation,
  BBox,
  CalibrationSkipRequest,
  CalibrationState,
  CalibrationTarget,
  CoverageGap,
  EnvironmentCapabilities,
  EnvironmentObservation,
  ErrorCode,
  EvidenceItem,
  ExamDefinition,
  Health,
  HealthReport,
  HumanReview,
  HumanReviewCreate,
  Incident,
  IncidentDetail,
  IncidentEndReason,
  IncidentRule,
  Observation,
  PauseRequest,
  PhoneObservation,
  PreflightCheck,
  PreflightReport,
  PreviewFrameMeta,
  Producer,
  ReviewPriority,
  RuntimeMetrics,
  SessionCreate,
  SessionInfo,
  SessionState,
  SessionSummary,
  SourceMode,
  StreamEnvelope,
  StreamPayload,
} from "@contracts/qorgau-v1.generated";
import { CalibrationTargetValues } from "@contracts/qorgau-v1.generated";
import { FRAME_H, FRAME_W, renderFixtureFrame } from "./fixtureFrames";

/** Mirrors A06 ipc/api.ts so the fixture rejects exactly what the real shell rejects. */
type Gated = "listSessions" | "listIncidents" | "getIncident" | "addReview" | "getEvidence" | "getSummary" | "exportReport" | "deleteSession" | "pauseExam" | "resumeExam";
const BLOCKED_IN_EXAM = new Set<Gated>(["listSessions", "listIncidents", "getIncident", "addReview", "getEvidence", "getSummary", "exportReport", "deleteSession"]);
const OPERATOR_ONLY = new Set<Gated>(["pauseExam", "resumeExam", "addReview", "getEvidence", "exportReport", "deleteSession"]);

function shellErr(code: ErrorCode, shellCode: string, message: string, retryable = false): ApiErrorBody {
  return { code, message, retryable, details: { shell_code: shellCode, source: "fixture" } };
}

export interface FixtureFaults {
  /** Every call fails, streams go quiet, shell.backend = "restarting". */
  backendDown: boolean;
  /** Capture produces no frames: health capture=error, monitoring_degraded episode, coverage gap. */
  cameraLost: boolean;
  /** saveAnswer fails with STORAGE_ERROR (retryable). */
  answerSaveFails: boolean;
  /** Calibration target "up" fails once with low_quality before succeeding on retry. */
  calibrationFailsOnce: boolean;
  /** saveAnswer latency 0.2–1.5 s in random order (request races). */
  slowSaves: boolean;
}

const TERMINAL: SessionState[] = ["finished", "aborted", "failed"];
const clone = <T>(v: T): T => structuredClone(v);
const nowIso = () => new Date().toISOString();
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function err(code: ErrorCode, message: string, retryable = false, details: ApiErrorBody["details"] = {}): ApiErrorBody {
  return { code, message, retryable, details };
}

const producer = (module: string): Producer => ({
  module: `fixture.${module}`,
  version: "fixture-1",
  model_id: null,
  model_sha256: null,
  config_version: "fixture-config-1",
});

// Scripted episodes, in ms of ACTIVE exam time (pauses excluded). Bounding boxes reuse the contract fixtures.
interface ScriptWindow {
  rule: IncidentRule;
  from: number;
  to: number;
  priority: ReviewPriority;
}
const SCRIPT: ScriptWindow[] = [
  { rule: "phone_visible", from: 4_000, to: 9_500, priority: "medium" },
  { rule: "multiple_faces", from: 14_000, to: 17_500, priority: "high" },
  { rule: "gaze_prolonged_down", from: 26_000, to: 35_000, priority: "low" },
  { rule: "face_missing", from: 40_000, to: 44_000, priority: "medium" },
];
const ENV_EVENT_AT = 21_000;

const PRIMARY_FACE: BBox = fixtures["AttentionObservation.two_faces"].faces[0]!.bbox;
const SECOND_FACE: BBox = fixtures["AttentionObservation.two_faces"].faces[1]!.bbox;
const PHONE_BOX: BBox = fixtures["PhoneObservation.phone_visible"].detections[0]!.bbox;

function emptyCalibration(): CalibrationState {
  return {
    calibration_id: null,
    phase: "not_started",
    targets: CalibrationTargetValues.map((t) => ({
      target: t,
      state: "pending",
      samples: 0,
      required_samples: 30,
      quality: null,
      message_code: null,
    })),
    current_target: null,
    message_code: null,
    updated_at: nowIso(),
  };
}

export class FixtureBridge implements QorgauBridge {
  readonly bridgeVersion = BRIDGE_VERSION;
  readonly transport = "fixture" as const;

  readonly faults: FixtureFaults = {
    backendDown: false,
    cameraLost: false,
    answerSaveFails: false,
    calibrationFailsOnce: true,
    slowSaves: false,
  };

  /** One-time PIN of this tab (never a built-in constant); shown only in the FIXTURE panel/dialog. */
  readonly operatorPin = String(100000 + Math.floor(Math.random() * 900000));
  private pinFailures = 0;
  private pinLockedUntil = 0;
  private capsAskedAt: number | null = null;

  private shell: ShellState = {
    mode: "normal",
    backend: "ready",
    exam_mode_active: false,
    operator_unlocked: false,
    session_id: null,
    last_error: null,
    shell_version: "fixture-0.1.0",
    platform: "fixture (браузер, без оболочки)",
  };
  private readonly caps: EnvironmentCapabilities = clone(fixtures["EnvironmentCapabilities.unverified"]);
  private sessions: SessionInfo[] = [];
  private active: SessionInfo | null = null;
  private preflightReport: PreflightReport | null = null;
  private cal: CalibrationState = emptyCalibration();
  private calFailedOnce = false;
  private answers = new Map<string, Map<string, AnswerRecord>>();
  private incidents = new Map<string, Incident[]>();
  private reviews = new Map<string, HumanReview[]>();
  private evidence = new Map<string, { item: EvidenceItem; bytes: Uint8Array }>();
  private gaps = new Map<string, CoverageGap[]>();
  private openByRule = new Map<IncidentRule, Incident>();
  private envEventDone = false;
  private seq = 0;
  private idSeq = 0;
  private frameId = 0;
  private frameTimes: number[] = [];
  private origin = performance.now();
  private pausedAtMs: number | null = null;
  private cameraGapOpen = false;

  private shellListeners = new Set<(s: ShellState) => void>();
  private eventListeners = new Set<(e: StreamEnvelope) => void>();
  private previewListeners = new Set<(m: PreviewFrameMeta, j: Uint8Array) => void>();
  private timer: ReturnType<typeof setInterval> | null = null;
  private tickCount = 0;
  private framePending = false;

  constructor() {
    this.timer = setInterval(() => this.tick(), 100);
  }

  dispose(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  // ------------------------------------------------------------------ fault control (dev panel only)
  setFault<K extends keyof FixtureFaults>(key: K, value: FixtureFaults[K]): void {
    this.faults[key] = value;
    if (key === "backendDown") {
      this.setShell({ backend: value ? "restarting" : "ready", last_error: value ? this.downError() : null });
      if (!value) {
        this.emit({
          type: "hello",
          contract: "qorgau.v1",
          contract_version: "1.0.0",
          backend_version: "fixture-0.1.0",
          server_time: nowIso(),
        });
        if (this.active) this.emit({ type: "session_state", session: clone(this.active) });
        this.emit({ type: "health", report: this.healthReport() });
      }
    }
    if (key === "cameraLost") this.emit({ type: "health", report: this.healthReport() });
  }

  // ------------------------------------------------------------------ helpers
  private t(): number {
    return performance.now() - this.origin;
  }

  private nextId(prefix: string): string {
    this.idSeq += 1;
    return `fx-${prefix}-${this.idSeq}`;
  }

  private downError(): ApiErrorBody {
    return shellErr("INTERNAL", "backend_unavailable", "Local backend is not available (FIXTURE: simulated)", true);
  }

  private gate(name: Gated): ApiErrorBody | null {
    if (this.shell.exam_mode_active && BLOCKED_IN_EXAM.has(name)) {
      return shellErr("INVALID_STATE", "exam_mode_active", `${name} is not available during the exam`);
    }
    if (OPERATOR_ONLY.has(name) && !this.shell.operator_unlocked) {
      return shellErr("INVALID_STATE", "operator_locked", `${name} requires the operator (teacher) unlock`);
    }
    return null;
  }

  private async respond<T>(fn: () => T | ApiErrorBody, latency = 140): Promise<BridgeResult<T>> {
    await sleep(latency);
    if (this.faults.backendDown) return { ok: false, error: this.downError() };
    const r = fn();
    if (r && typeof r === "object" && "code" in r && "retryable" in r && "message" in r) {
      return { ok: false, error: r as ApiErrorBody };
    }
    return { ok: true, data: clone(r as T) };
  }

  private setShell(patch: Partial<ShellState>): void {
    this.shell = { ...this.shell, ...patch };
    const snap = clone(this.shell);
    this.shellListeners.forEach((l) => l(snap));
  }

  private emit(message: StreamPayload): void {
    if (this.faults.backendDown) return;
    // A05 semantics: the stream never knows reviews or materials (A08 does) → pending / [] on the wire.
    if (message.type === "incident") {
      message = { ...message, change: { ...message.change, incident: { ...message.change.incident, review_status: "pending", evidence_ids: [] } } };
    }
    this.seq += 1;
    const env: StreamEnvelope = {
      contract: "qorgau.v1",
      seq: this.seq,
      sent_at: nowIso(),
      session_id: this.active?.session_id ?? null,
      message: clone(message),
    };
    this.eventListeners.forEach((l) => l(env));
  }

  /** FIXTURE: class_state as the C2 uplink would publish it (handoffs/C2/STATUS.md). Not a class server. */
  classState: Record<string, unknown> = {
    type: "class_state",
    connection: "connected",
    server: "fixture-class:8765",
    student_id: "st-fixture",
    locked: false,
    lock_reason_ru: null,
    mic_active: false,
    audio_direction: null,
    exam: { title: "FIXTURE-экзамен" },
    last_command: null,
    message_ru: null,
  };

  emitClassState(patch: Record<string, unknown>): void {
    this.classState = { ...this.classState, ...patch, type: "class_state" };
    if (this.faults.backendDown) return;
    this.seq += 1;
    const env = { contract: "qorgau.v1", seq: this.seq, sent_at: nowIso(), session_id: null, message: clone(this.classState) } as unknown as StreamEnvelope;
    this.eventListeners.forEach((l) => l(env));
  }

  private find(sessionId: string): SessionInfo | ApiErrorBody {
    if (this.active?.session_id === sessionId) return this.active;
    const s = this.sessions.find((x) => x.session_id === sessionId);
    return s ?? err("SESSION_NOT_FOUND", `Сессия ${sessionId} не найдена`);
  }

  private requireActive(sessionId: string, action: string, ...allowed: SessionState[]): SessionInfo | ApiErrorBody {
    const s = this.find(sessionId);
    if ("code" in s) return s;
    if (!allowed.includes(s.state)) {
      return err("INVALID_STATE", `Нельзя выполнить «${action}»: сессия в состоянии '${s.state}'`, false, {
        state: s.state,
        action,
      });
    }
    return s;
  }

  private update(patch: Partial<SessionInfo>): SessionInfo {
    if (!this.active) throw new Error("no active session");
    this.active = { ...this.active, ...patch };
    const i = this.sessions.findIndex((x) => x.session_id === this.active!.session_id);
    if (i >= 0) this.sessions[i] = this.active;
    this.emit({ type: "session_state", session: this.active });
    return this.active;
  }

  private calChanged(): CalibrationState {
    this.cal.updated_at = nowIso();
    if (this.active) {
      this.active = { ...this.active, calibration: clone(this.cal) };
      const i = this.sessions.findIndex((x) => x.session_id === this.active!.session_id);
      if (i >= 0) this.sessions[i] = this.active;
      this.emit({ type: "calibration", session_id: this.active.session_id, calibration: this.cal });
    }
    return this.cal;
  }

  private activeMs(): number {
    const s = this.active;
    if (!s || s.exam_started_t_ms === null) return 0;
    const pausedNow = this.pausedAtMs !== null ? this.t() - this.pausedAtMs : 0;
    return this.t() - s.exam_started_t_ms - s.paused_total_ms - pausedNow;
  }

  private healthReport(): HealthReport {
    const base: HealthReport = clone(fixtures["HealthReport.bootstrap"]);
    const comp = (component: Health["component"], status: Health["status"], code: string, message: string): Health => ({
      component,
      status,
      code,
      message,
      since_t_session_ms: null,
      details: {},
    });
    const capture = this.faults.cameraLost
      ? comp("capture", "error", "camera_disconnected", "FIXTURE: имитация потери источника кадров")
      : comp("capture", "ok", "synthetic_source", "Синтетический источник кадров (FIXTURE, не камера)");
    base.components = [
      comp("backend", "ok", "fixture_backend", "FixtureBridge в памяти браузера (не настоящий backend)"),
      capture,
      comp("phone", "degraded", "fixture_script", "Сценарные наблюдения FIXTURE, не CV"),
      comp("attention", "degraded", "fixture_script", "Сценарные наблюдения FIXTURE, не CV"),
      comp("fusion", "degraded", "fixture_rules", "Сценарные эпизоды FIXTURE"),
      comp("evidence", "degraded", "fixture_memory", "Хранение в памяти вкладки, теряется при перезагрузке"),
      comp("environment", "degraded", "capabilities_unverified", "Защита среды не проверялась (нет оболочки)"),
    ];
    base.overall = this.faults.cameraLost ? "error" : "degraded";
    base.backend_version = "fixture-0.1.0";
    base.active_session_id = this.active && !TERMINAL.includes(this.active.state) ? this.active.session_id : null;
    base.server_time = nowIso();
    return base;
  }

  private buildPreflight(s: SessionInfo): PreflightReport {
    const check = (
      check_id: PreflightCheck["check_id"],
      status: PreflightCheck["status"],
      required: boolean,
      message_code: string,
      message_ru: string,
    ): PreflightCheck => ({ check_id, status, required, message_code, message_ru, details: {} });
    const mode = s.source_mode;
    let checks: PreflightCheck[];
    if (mode === "synthetic") {
      checks = [
        check("backend", "pass", true, "fixture_backend", "FixtureBridge отвечает (не настоящий backend)"),
        this.faults.cameraLost
          ? check("camera", "fail", true, "camera_disconnected", "Источник кадров не отвечает (FIXTURE: имитация)")
          : check("camera", "pass", true, "synthetic_source", "СИНТЕТИЧЕСКИЙ источник кадров (не камера)"),
        check("lighting", "not_run", false, "synthetic_source", "Освещение не оценивается для синтетических кадров"),
        check("phone_model", "warn", false, "synthetic_script", "Сценарные синтетические наблюдения, не CV"),
        check("face_model", "warn", false, "synthetic_script", "Сценарные синтетические наблюдения, не CV"),
        check("fusion", "warn", false, "fixture_rules", "Сценарные эпизоды FIXTURE"),
        check("storage", "warn", false, "fixture_memory", "Данные только в памяти вкладки"),
        check("environment_protection", "not_run", false, "shell_not_reported", "Оболочка не сообщила возможности защиты"),
        check("offline_assets", "pass", false, "bundled", "Интерфейс собран локально, внешних загрузок нет"),
      ];
    } else {
      const why = "FixtureBridge не имеет камеры и CV-модулей — LIVE/REPLAY здесь невозможны";
      checks = [
        check("backend", "pass", true, "fixture_backend", "FixtureBridge отвечает (не настоящий backend)"),
        check("camera", "fail", true, mode === "live" ? "camera_unavailable" : "replay_unavailable", why),
        check("lighting", "not_run", false, "no_frames", "Нет кадров для оценки освещения"),
        check("phone_model", "fail", true, "module_not_integrated", "Модуль телефона (A03) не интегрирован"),
        check("face_model", "fail", true, "module_not_integrated", "Модуль лица (A04) не интегрирован"),
        check("fusion", "fail", true, "module_not_integrated", "Модуль эпизодов (A05) не интегрирован"),
        check("storage", "fail", true, "module_not_integrated", "Хранилище (A08) не интегрировано"),
      ];
      if (mode === "live") {
        checks.push(
          check(
            "environment_protection",
            "fail",
            true,
            "capabilities_unverified",
            "Возможности защиты не проверены на целевой Windows",
          ),
        );
      }
      checks.push(check("offline_assets", "pass", false, "bundled", "Интерфейс собран локально, внешних загрузок нет"));
    }
    const ready = checks.every((c) => !c.required || c.status === "pass");
    return { session_id: s.session_id, source_mode: mode, checks, ready, created_at: nowIso() };
  }

  // ------------------------------------------------------------------ simulation tick (100 ms)
  private tick(): void {
    this.tickCount += 1;
    if (this.faults.backendDown) return;
    const s = this.active;
    if (!s) return;

    // Calibration: samples come from "frames" (3 per 100 ms at 30 fps), never from a UI timer.
    if (s.state === "calibrating" && this.cal.phase === "collecting" && !this.faults.cameraLost) {
      const cur = this.cal.targets.find((x) => x.target === this.cal.current_target && x.state === "collecting");
      if (cur) {
        cur.samples = Math.min(cur.required_samples, cur.samples + 3);
        cur.quality = 0.78 + ((this.tickCount * 7) % 10) / 100;
        if (cur.target === "up" && this.faults.calibrationFailsOnce && !this.calFailedOnce && cur.samples >= 12) {
          this.calFailedOnce = true;
          cur.state = "failed";
          cur.quality = 0.31;
          cur.message_code = "low_quality";
        } else if (cur.samples >= cur.required_samples) {
          cur.state = "ok";
          cur.message_code = null;
        }
        this.calChanged();
      }
    }

    const live = s.state === "running" || s.state === "paused" || s.state === "preflight" || s.state === "calibrating" || s.state === "ready";
    if (!live) return;

    // Camera loss → coverage gap + technical episode while running.
    if (this.faults.cameraLost !== this.cameraGapOpen) {
      this.cameraGapOpen = this.faults.cameraLost;
      const gaps = this.gaps.get(s.session_id) ?? [];
      if (this.cameraGapOpen) gaps.push({ t_start_ms: this.t(), t_end_ms: null, component: "capture", reason: "camera_disconnected" });
      else {
        const g = gaps.find((x) => x.t_end_ms === null && x.component === "capture");
        if (g) g.t_end_ms = this.t();
      }
      this.gaps.set(s.session_id, gaps);
    }

    // Preview + observations at 5 fps.
    if (this.tickCount % 2 === 0 && !this.faults.cameraLost && !this.framePending) void this.produceFrame(s);

    if (s.state === "running" || s.state === "paused") this.advanceScript(s);
    if (this.tickCount % 10 === 0) this.emit({ type: "metrics", metrics: this.metrics(s.session_id) });
  }

  private scene(): { faces: BBox[]; down: boolean; phone: BBox | null; rules: Set<IncidentRule> } {
    const rules = new Set<IncidentRule>();
    if (this.active?.state === "running") {
      const a = this.activeMs();
      for (const w of SCRIPT) if (a >= w.from && a < w.to) rules.add(w.rule);
    }
    const faces: BBox[] = rules.has("face_missing") ? [] : [PRIMARY_FACE];
    if (rules.has("multiple_faces")) faces.push(SECOND_FACE);
    const down = rules.has("gaze_prolonged_down");
    if (down && faces[0]) faces[0] = { ...faces[0], y_min: faces[0].y_min + 0.06, y_max: faces[0].y_max + 0.06 };
    return { faces, down, phone: rules.has("phone_visible") ? PHONE_BOX : null, rules };
  }

  private async produceFrame(s: SessionInfo): Promise<void> {
    this.framePending = true;
    try {
      const t = this.t();
      const frameId = this.frameId++;
      const sc = this.scene();
      const jpeg = await renderFixtureFrame({ frameId, tSessionMs: t, faces: sc.faces, lookingDown: sc.down, phone: sc.phone });
      const cur = this.active;
      if (!jpeg || this.faults.backendDown || !cur || cur.session_id !== s.session_id) return;
      this.frameTimes.push(performance.now());
      const meta: PreviewFrameMeta = {
        session_id: s.session_id,
        frame_id: frameId,
        t_session_ms: t,
        wall_time: nowIso(),
        width: FRAME_W,
        height: FRAME_H,
        mirrored: false,
        source_mode: "synthetic",
        media_type: "image/jpeg",
        byte_length: jpeg.byteLength,
      };
      this.previewListeners.forEach((l) => l(clone(meta), jpeg.slice()));
      if (cur.state === "running") {
        for (const o of this.observations(s.session_id, frameId, t, sc)) this.emit({ type: "observation", observation: o });
        // Keep the latest JPEG of trigger frames as evidence when the session retains media.
        if (s.retain_media) this.captureEvidence(s, frameId, t, jpeg);
      }
    } finally {
      this.framePending = false;
    }
  }

  private observations(
    sessionId: string,
    frameId: number,
    t: number,
    sc: ReturnType<FixtureBridge["scene"]>,
  ): Observation[] {
    const common = {
      session_id: sessionId,
      frame_id: frameId,
      t_session_ms: t,
      wall_time: nowIso(),
      source_mode: "synthetic" as SourceMode,
      status: "ok" as const,
      quality: 0.8,
      quality_flags: ["synthetic"],
      latency_ms: null,
    };
    const att: AttentionObservation = {
      ...common,
      observation_id: `fx-att-${frameId}`,
      producer: producer("attention"),
      kind: "attention",
      face_count: sc.faces.length,
      faces: sc.faces.map((bbox, i) => ({ bbox, confidence: i === 0 ? 0.92 : 0.81, is_primary: i === 0 })),
      primary_face_present: sc.faces.length > 0,
      head_pose: sc.faces.length ? { yaw_deg: 1, pitch_deg: sc.down ? -24 : -2, roll_deg: 0 } : null,
      head_direction: sc.faces.length ? (sc.down ? "down" : "center") : "unknown",
      gaze: sc.faces.length
        ? {
            direction: sc.down ? "down" : "center",
            yaw_deg: null,
            pitch_deg: null,
            confidence: null,
            method: "head_pose_only",
            calibrated: this.cal.phase === "completed",
          }
        : null,
      calibration_id: this.cal.phase === "completed" ? this.cal.calibration_id : null,
      reasons: sc.faces.length ? [] : ["no_face_detected"],
    };
    const phone: PhoneObservation = {
      ...common,
      observation_id: `fx-phone-${frameId}`,
      producer: producer("phone"),
      kind: "phone",
      detections: sc.phone
        ? [
            {
              bbox: sc.phone,
              confidence: 0.71,
              class_name: "cell phone",
              class_index: 67,
              track_id: "fx-trk-1",
              track_quality: null,
              track_age_ms: null,
            },
          ]
        : [],
      signals: [
        {
          name: "phone_visible",
          state: sc.phone ? "present" : "absent",
          confidence: sc.phone ? 0.71 : null,
          track_id: sc.phone ? "fx-trk-1" : null,
          reason: sc.phone ? "detected_in_frame" : "no_detection",
          facts: {},
        },
        {
          name: "possible_screen_capture",
          state: "insufficient_evidence",
          confidence: null,
          track_id: null,
          reason: "camera_side_not_observable",
          facts: {},
        },
      ],
    };
    return [att, phone];
  }

  private captureEvidence(s: SessionInfo, frameId: number, t: number, jpeg: Uint8Array): void {
    for (const inc of this.openByRule.values()) {
      if (inc.evidence_ids.length > 0 || inc.category === "technical") continue;
      const id = this.nextId("ev");
      const item: EvidenceItem = {
        evidence_id: id,
        session_id: s.session_id,
        incident_id: inc.incident_id,
        kind: "snapshot",
        frame_id: frameId,
        t_session_ms: t,
        media_type: "image/jpeg",
        sha256: "0".repeat(64), // FIXTURE: hash not computed
        size_bytes: jpeg.byteLength,
        created_at: nowIso(),
      };
      this.evidence.set(id, { item, bytes: jpeg.slice() });
      inc.evidence_ids = [id];
    }
  }

  private explanation(rule: IncidentRule, durationMs: number): Incident["explanation"] {
    const sec = (durationMs / 1000).toLocaleString("ru-RU", { maximumFractionDigits: 1 });
    const base = { summary_kk: null };
    switch (rule) {
      case "phone_visible":
        return {
          ...base,
          summary_ru: `Телефон виден ${sec} с; требуется проверка преподавателем.`,
          facts: [{ key: "phone_visible_ms", value: durationMs, unit: "ms", label_ru: "Телефон виден" }],
          caveats_ru: ["Обнаружение телефона не доказывает фотографирование экрана.", "FIXTURE: сценарные данные, не CV."],
        };
      case "multiple_faces":
        return {
          ...base,
          summary_ru: `В кадре два лица в течение ${sec} с.`,
          facts: [
            { key: "max_face_count", value: 2, unit: "count", label_ru: "Максимум лиц в кадре" },
            { key: "multiple_faces_ms", value: durationMs, unit: "ms", label_ru: "Длительность" },
          ],
          caveats_ru: ["Лицо на фото/экране может быть принято за второе лицо.", "FIXTURE: сценарные данные, не CV."],
        };
      case "gaze_prolonged_down":
        return {
          ...base,
          summary_ru: `Голова наклонена вниз ${sec} с (оценка по положению головы).`,
          facts: [
            { key: "down_ms", value: durationMs, unit: "ms", label_ru: "Взгляд вниз" },
            { key: "pitch_deg", value: -24, unit: "deg", label_ru: "Наклон головы" },
          ],
          caveats_ru: [
            "Направление взгляда приблизительное. Чтение нижней части экрана или черновик — обычное поведение.",
            "FIXTURE: сценарные данные, не CV.",
          ],
        };
      case "face_missing":
        return {
          ...base,
          summary_ru: `Лицо не обнаружено ${sec} с при работающем источнике кадров.`,
          facts: [{ key: "face_missing_ms", value: durationMs, unit: "ms", label_ru: "Лицо не видно" }],
          caveats_ru: ["Студент мог наклониться или выйти из кадра по уважительной причине.", "FIXTURE: сценарные данные, не CV."],
        };
      case "monitoring_degraded":
        return {
          ...base,
          summary_ru: `Нет кадров от источника ${sec} с — наблюдение в этот период не велось.`,
          facts: [{ key: "gap_ms", value: durationMs, unit: "ms", label_ru: "Пробел наблюдения" }],
          caveats_ru: ["Это технический эпизод, а не поведение студента.", "FIXTURE: имитация сбоя."],
        };
      case "environment_blocked_action": {
        const env = fixtures["EnvironmentObservation.alt_tab_detected"];
        return {
          ...base,
          summary_ru: `Нажато сочетание ${env.detail.shortcut ?? "клавиш"}; оболочка зафиксировала действие.`,
          facts: [
            { key: "shortcut", value: env.detail.shortcut ?? "—", unit: "none", label_ru: "Сочетание" },
            { key: "enforcement", value: env.enforcement, unit: "none", label_ru: "Результат защиты" },
          ],
          caveats_ru: ["Возможности защиты не проверены на целевой Windows.", "FIXTURE: событие из контрактного fixture."],
        };
      }
      default:
        return { ...base, summary_ru: "Эпизод FIXTURE.", facts: [], caveats_ru: ["FIXTURE"] };
    }
  }

  private category(rule: IncidentRule): Incident["category"] {
    if (rule.startsWith("phone") || rule === "possible_screen_capture") return "phone";
    if (rule.startsWith("gaze")) return "attention";
    if (rule === "face_missing" || rule === "multiple_faces") return "presence";
    if (rule.startsWith("environment")) return "environment";
    return "technical";
  }

  private openIncident(s: SessionInfo, rule: IncidentRule, priority: ReviewPriority, closed = false): Incident {
    const t = this.t();
    const inc: Incident = {
      incident_id: this.nextId("inc"),
      session_id: s.session_id,
      rule_id: rule,
      category: this.category(rule),
      state: closed ? "closed" : "open",
      priority,
      t_start_ms: t,
      t_end_ms: closed ? t : null,
      wall_start: nowIso(),
      wall_end: closed ? nowIso() : null,
      duration_ms: 0,
      source_mode: "synthetic",
      max_confidence: rule === "phone_visible" ? 0.71 : null,
      mean_quality: rule === "monitoring_degraded" ? null : 0.8,
      explanation: this.explanation(rule, 0),
      observation_ids: [],
      observation_count: 0,
      trigger_frame_id: rule === "monitoring_degraded" || rule.startsWith("environment") ? null : this.frameId,
      related_incident_ids: [],
      evidence_ids: [],
      rule_version: "fixture-rules-1",
      config_version: "fixture-config-1",
      end_reason: closed ? "condition_cleared" : null,
      update_seq: 1,
      review_status: "pending",
    };
    const list = this.incidents.get(s.session_id) ?? [];
    list.push(inc);
    this.incidents.set(s.session_id, list);
    if (!closed) this.openByRule.set(rule, inc);
    this.emit({ type: "incident", change: { change: closed ? "closed" : "opened", incident: inc } });
    return inc;
  }

  private touch(inc: Incident, closeReason: IncidentEndReason | null): void {
    const t = this.t();
    inc.duration_ms = t - inc.t_start_ms;
    inc.observation_count = Math.round(inc.duration_ms / 200);
    inc.explanation = this.explanation(inc.rule_id, inc.duration_ms);
    inc.update_seq += 1;
    if (closeReason) {
      inc.state = "closed";
      inc.t_end_ms = t;
      inc.wall_end = nowIso();
      inc.end_reason = closeReason;
      this.openByRule.delete(inc.rule_id);
    }
    this.emit({ type: "incident", change: { change: closeReason ? "closed" : "updated", incident: inc } });
  }

  private advanceScript(s: SessionInfo): void {
    const running = s.state === "running";
    const sc = this.scene();
    const wanted = new Map<IncidentRule, ReviewPriority>();
    if (running) for (const w of SCRIPT) if (sc.rules.has(w.rule) && !this.faults.cameraLost) wanted.set(w.rule, w.priority);
    if (running && this.faults.cameraLost) wanted.set("monitoring_degraded", "low");

    for (const [rule, inc] of [...this.openByRule]) {
      if (!wanted.has(rule)) this.touch(inc, running ? "condition_cleared" : "session_paused");
      else if (this.tickCount % 5 === 0) this.touch(inc, null);
    }
    for (const [rule, prio] of wanted) if (!this.openByRule.has(rule)) this.openIncident(s, rule, prio);

    if (running && !this.envEventDone && this.activeMs() >= ENV_EVENT_AT) {
      this.envEventDone = true;
      const src = fixtures["EnvironmentObservation.alt_tab_detected"];
      const obs: EnvironmentObservation = {
        ...clone(src),
        observation_id: this.nextId("env"),
        session_id: s.session_id,
        t_session_ms: this.t(),
        wall_time: nowIso(),
        producer: producer("environment"),
      };
      this.emit({ type: "observation", observation: obs });
      const inc = this.openIncident(s, "environment_blocked_action", "medium", true);
      inc.observation_ids = [obs.observation_id];
      inc.observation_count = 1;
    }
  }

  private closeAll(reason: IncidentEndReason): void {
    for (const inc of [...this.openByRule.values()]) this.touch(inc, reason);
  }

  private metrics(sessionId: string): RuntimeMetrics {
    const now = performance.now();
    this.frameTimes = this.frameTimes.filter((x) => now - x <= 5000);
    const fps = this.frameTimes.length / 5;
    const consumer = (name: string) => ({
      name: `fixture.${name}`,
      processed_fps: fps,
      frames_processed: this.frameId,
      frames_skipped: 0,
      errors: 0,
      frame_age_ms_p50: null,
      frame_age_ms_p95: null,
      process_ms_p50: null,
      process_ms_p95: null,
    });
    return {
      session_id: sessionId,
      t_session_ms: this.t(),
      window_s: 5,
      capture_fps: fps,
      frames_captured: this.frameId,
      frames_dropped: 0,
      consumers: [consumer("phone"), consumer("attention")],
      e2e_latency_ms_p50: null,
      e2e_latency_ms_p95: null,
    };
  }

  private endSession(final: "finished" | "aborted"): SessionInfo {
    this.closeAll(final === "finished" ? "session_finished" : "session_aborted");
    if (this.active) {
      for (const g of this.gaps.get(this.active.session_id) ?? []) if (g.t_end_ms === null) g.t_end_ms = this.t();
    }
    if (this.pausedAtMs !== null && this.active) {
      this.active = { ...this.active, paused_total_ms: this.active.paused_total_ms + (this.t() - this.pausedAtMs) };
      this.pausedAtMs = null;
    }
    const s = this.update({ state: final, finished_at: nowIso() });
    this.setShell({ mode: "normal", exam_mode_active: false, session_id: null });
    return s;
  }

  // ================================================================== QorgauBridge: shell
  async getShellState(): Promise<ShellState> {
    return clone(this.shell);
  }

  onShellState(listener: (state: ShellState) => void): Unsubscribe {
    this.shellListeners.add(listener);
    return () => this.shellListeners.delete(listener);
  }

  getEnvironmentCapabilities(): Promise<BridgeResult<EnvironmentCapabilities>> {
    // Like the shell: the matrix is "not measured yet" for a moment after start (retryable).
    this.capsAskedAt ??= performance.now();
    if (performance.now() - this.capsAskedAt < 1500) {
      return this.respond<EnvironmentCapabilities>(() => shellErr("NOT_FOUND", "enforcement_error", "Environment capabilities are not measured yet", true));
    }
    return this.respond(() => this.caps);
  }

  async operatorUnlock(pin: string): Promise<BridgeResult<ShellState>> {
    await sleep(250);
    if (!/^[0-9A-Za-z]{4,64}$/.test(pin)) return { ok: false, error: shellErr("INVALID_ARGUMENT", "invalid_argument", "pin: 4-64 letters/digits") };
    if (Date.now() < this.pinLockedUntil) {
      return { ok: false, error: shellErr("UNAUTHORIZED", "operator_pin_rate_limited", "Too many attempts, wait and retry", true) };
    }
    if (pin !== this.operatorPin) {
      this.pinFailures += 1;
      if (this.pinFailures >= 5) this.pinLockedUntil = Date.now() + 30_000;
      return { ok: false, error: shellErr("UNAUTHORIZED", "operator_pin_wrong", "Wrong operator PIN") };
    }
    this.pinFailures = 0;
    this.setShell({ operator_unlocked: true });
    return { ok: true, data: clone(this.shell) };
  }

  async operatorLock(): Promise<ShellState> {
    this.setShell({ operator_unlocked: false });
    return clone(this.shell);
  }

  async requestEmergencyExit(reason: string): Promise<BridgeResult<ShellState>> {
    await sleep(120);
    // The shell's emergency exit works even when the backend is unreachable: restrictions are released locally.
    this.setShell({ mode: "releasing" });
    if (this.active && !TERMINAL.includes(this.active.state) && !this.faults.backendDown) {
      console.warn("[fixture] emergency exit:", reason);
      this.endSession("aborted");
    }
    this.setShell({ mode: "normal", exam_mode_active: false });
    return { ok: true, data: clone(this.shell) };
  }

  // ================================================================== backend lifecycle
  health(): Promise<BridgeResult<HealthReport>> {
    return this.respond(() => this.healthReport());
  }

  listSessions(): Promise<BridgeResult<SessionInfo[]>> {
    return this.respond(() => this.gate("listSessions") ?? [...this.sessions].reverse());
  }

  createSession(body: SessionCreate): Promise<BridgeResult<SessionInfo>> {
    return this.respond(() => {
      if (this.active && !TERMINAL.includes(this.active.state)) {
        return err("SESSION_ACTIVE", "Уже есть незавершённая сессия", false, { session_id: this.active.session_id });
      }
      if (!body.consent.accepted) return err("INVALID_ARGUMENT", "Требуется согласие участника");
      this.origin = performance.now();
      this.frameId = 0;
      this.frameTimes = [];
      this.openByRule.clear();
      this.envEventDone = false;
      this.calFailedOnce = false;
      this.cameraGapOpen = false;
      this.pausedAtMs = null;
      this.cal = emptyCalibration();
      this.preflightReport = null;
      const sid = `fx-session-${String(this.sessions.length + 1).padStart(4, "0")}`;
      const s: SessionInfo = {
        session_id: sid,
        state: "created",
        source: clone(body.source),
        source_mode: body.source.mode,
        exam_id: body.exam_id,
        student_label: body.student_label,
        retain_media: body.retain_media,
        created_at: nowIso(),
        started_at: null,
        finished_at: null,
        exam_started_t_ms: null,
        paused_total_ms: 0,
        calibration: clone(this.cal),
        contract_version: "1.0.0",
        backend_version: "fixture-0.1.0",
        last_error: null,
      };
      this.sessions.push(s);
      this.active = s;
      this.answers.set(sid, new Map());
      this.incidents.set(sid, []);
      this.gaps.set(sid, []);
      this.setShell({ mode: "preflight", session_id: sid });
      this.emit({ type: "session_state", session: s });
      return s;
    }, 220);
  }

  getSession(sessionId: string): Promise<BridgeResult<SessionInfo>> {
    return this.respond(() => this.find(sessionId), 60);
  }

  runPreflight(sessionId: string): Promise<BridgeResult<PreflightReport>> {
    return this.respond(() => {
      const s = this.requireActive(sessionId, "preflight", "created", "preflight");
      if ("code" in s) return s;
      this.preflightReport = this.buildPreflight(s);
      this.update({ state: "preflight" });
      return this.preflightReport;
    }, 700);
  }

  calibrationStart(sessionId: string): Promise<BridgeResult<CalibrationState>> {
    return this.respond(() => {
      const s = this.requireActive(sessionId, "начать калибровку", "preflight", "calibrating", "ready");
      if ("code" in s) return s;
      if (!this.preflightReport?.ready) return err("PREFLIGHT_FAILED", "Обязательные проверки не пройдены");
      this.cal = emptyCalibration();
      this.cal.calibration_id = this.nextId("cal");
      this.cal.phase = "collecting";
      this.cal.message_code = "fixture_calibration";
      this.calFailedOnce = false;
      this.update({ state: "calibrating" });
      return this.calChanged();
    });
  }

  calibrationTarget(sessionId: string, target: CalibrationTarget): Promise<BridgeResult<CalibrationState>> {
    return this.respond(() => {
      const s = this.requireActive(sessionId, "выбрать точку калибровки", "calibrating");
      if ("code" in s) return s;
      if (this.cal.phase !== "collecting") return err("INVALID_STATE", "Калибровка не в фазе сбора");
      this.cal.targets = this.cal.targets.map((t) =>
        t.target === target ? { ...t, state: "collecting", samples: 0, quality: null, message_code: null } : t,
      );
      this.cal.current_target = target;
      return this.calChanged();
    }, 80);
  }

  calibrationState(sessionId: string): Promise<BridgeResult<CalibrationState>> {
    return this.respond(() => {
      const s = this.find(sessionId);
      if ("code" in s) return s;
      return this.active?.session_id === sessionId ? this.cal : s.calibration;
    }, 40);
  }

  calibrationFinish(sessionId: string): Promise<BridgeResult<CalibrationState>> {
    return this.respond(() => {
      const s = this.requireActive(sessionId, "завершить калибровку", "calibrating");
      if ("code" in s) return s;
      const ok = this.cal.targets.every((t) => t.state === "ok");
      this.cal.phase = ok ? "completed" : "failed";
      this.cal.message_code = ok ? "fixture_calibration" : "targets_incomplete";
      this.cal.current_target = null;
      const cal = this.calChanged();
      if (ok) this.update({ state: "ready" });
      return cal;
    });
  }

  calibrationCancel(sessionId: string): Promise<BridgeResult<CalibrationState>> {
    return this.respond(() => {
      const s = this.requireActive(sessionId, "отменить калибровку", "calibrating");
      if ("code" in s) return s;
      this.cal = emptyCalibration();
      this.cal.phase = "cancelled";
      const cal = this.calChanged();
      this.update({ state: "preflight" });
      return cal;
    });
  }

  calibrationSkip(sessionId: string, body: CalibrationSkipRequest): Promise<BridgeResult<CalibrationState>> {
    return this.respond(() => {
      const s = this.requireActive(sessionId, "пропустить калибровку", "preflight", "calibrating");
      if ("code" in s) return s;
      if (!this.preflightReport?.ready) return err("PREFLIGHT_FAILED", "Обязательные проверки не пройдены");
      if (!body.reason.trim() || body.reason.length > 200) return err("INVALID_ARGUMENT", "Причина пропуска: 1–200 символов");
      this.cal = { ...this.cal, phase: "skipped", current_target: null, message_code: "skipped_by_operator" };
      const cal = this.calChanged();
      this.update({ state: "ready" });
      return cal;
    });
  }

  startExam(sessionId: string): Promise<BridgeResult<SessionInfo>> {
    return this.respond(() => {
      const s = this.requireActive(sessionId, "начать экзамен", "ready");
      if ("code" in s) return s;
      if (!this.preflightReport?.ready) return err("PREFLIGHT_FAILED", "Обязательные проверки не пройдены");
      if (this.cal.phase !== "completed" && this.cal.phase !== "skipped") {
        return err("INVALID_STATE", "Калибровка должна быть завершена или явно пропущена");
      }
      const info = this.update({ state: "running", started_at: nowIso(), exam_started_t_ms: this.t() });
      this.setShell({ mode: "exam", exam_mode_active: true, operator_unlocked: false });
      return info;
    }, 300);
  }

  pauseExam(sessionId: string, body: PauseRequest): Promise<BridgeResult<SessionInfo>> {
    return this.respond(() => {
      const denied = this.gate("pauseExam");
      if (denied) return denied;
      const s = this.requireActive(sessionId, "пауза", "running");
      if ("code" in s) return s;
      console.info("[fixture] pause:", body.reason);
      this.pausedAtMs = this.t();
      const info = this.update({ state: "paused" });
      this.closeAll("session_paused");
      this.setShell({ exam_mode_active: false });
      return info;
    });
  }

  resumeExam(sessionId: string): Promise<BridgeResult<SessionInfo>> {
    return this.respond(() => {
      const denied = this.gate("resumeExam");
      if (denied) return denied;
      const s = this.requireActive(sessionId, "продолжить", "paused");
      if ("code" in s) return s;
      const paused = this.pausedAtMs !== null ? this.t() - this.pausedAtMs : 0;
      this.pausedAtMs = null;
      this.setShell({ exam_mode_active: true });
      return this.update({ state: "running", paused_total_ms: s.paused_total_ms + paused });
    });
  }

  finishExam(sessionId: string): Promise<BridgeResult<SessionInfo>> {
    return this.respond(() => {
      const s = this.find(sessionId);
      if ("code" in s) return s;
      if (TERMINAL.includes(s.state)) return s; // idempotent
      return this.endSession("finished");
    }, 400);
  }

  abortExam(sessionId: string, body: AbortRequest): Promise<BridgeResult<SessionInfo>> {
    return this.respond(() => {
      const s = this.find(sessionId);
      if ("code" in s) return s;
      if (TERMINAL.includes(s.state)) return s;
      console.warn("[fixture] abort:", body.reason);
      return this.endSession("aborted");
    }, 250);
  }

  getExam(sessionId: string): Promise<BridgeResult<ExamDefinition>> {
    return this.respond(() => {
      const s = this.find(sessionId);
      if ("code" in s) return s;
      const exam: ExamDefinition = clone(fixtures["ExamDefinition.demo_min"]);
      // Fixture exam extended locally so the student screen shows progress over several questions (labelled fixture).
      exam.questions.push(
        {
          question_id: "q3",
          kind: "multi_choice",
          prompt: "Какие из перечисленных устройств являются устройствами ввода? (несколько ответов)",
          options: [
            { option_id: "a", text: "Клавиатура" },
            { option_id: "b", text: "Монитор" },
            { option_id: "c", text: "Мышь" },
            { option_id: "d", text: "Принтер" },
          ],
          max_length: null,
        },
        {
          question_id: "q4",
          kind: "single_choice",
          prompt: "Какое расширение обычно у файла с исходным кодом на Python?",
          options: [
            { option_id: "a", text: ".js" },
            { option_id: "b", text: ".py" },
            { option_id: "c", text: ".exe" },
          ],
          max_length: null,
        },
      );
      return exam;
    }, 160);
  }

  // ================================================================== storage / review / report
  saveAnswer(sessionId: string, questionId: string, body: AnswerUpsert): Promise<BridgeResult<AnswerRecord>> {
    return this.respond(() => {
      const s = this.find(sessionId);
      if ("code" in s) return s;
      if (s.state !== "running") return err("INVALID_STATE", "Ответы принимаются только во время экзамена", false, { state: s.state });
      if (this.faults.answerSaveFails) return err("STORAGE_ERROR", "Не удалось записать ответ (FIXTURE: имитация)", true);
      const m = this.answers.get(sessionId) ?? new Map<string, AnswerRecord>();
      const prev = m.get(questionId);
      if (prev && prev.client_seq >= body.client_seq) return prev; // last writer by client_seq
      const rec: AnswerRecord = { session_id: sessionId, question_id: questionId, value: body.value, client_seq: body.client_seq, saved_at: nowIso() };
      m.set(questionId, rec);
      this.answers.set(sessionId, m);
      return rec;
    }, this.faults.slowSaves ? 200 + Math.random() * 1300 : 180);
  }

  listAnswers(sessionId: string): Promise<BridgeResult<AnswerRecord[]>> {
    return this.respond(() => [...(this.answers.get(sessionId)?.values() ?? [])]);
  }

  listIncidents(sessionId: string): Promise<BridgeResult<Incident[]>> {
    return this.respond(() => {
      const denied = this.gate("listIncidents");
      if (denied) return denied;
      const s = this.find(sessionId);
      if ("code" in s) return s;
      return [...(this.incidents.get(sessionId) ?? [])].sort((a, b) => a.t_start_ms - b.t_start_ms);
    });
  }

  getIncident(sessionId: string, incidentId: string): Promise<BridgeResult<IncidentDetail>> {
    return this.respond(() => {
      const denied = this.gate("getIncident");
      if (denied) return denied;
      const inc = (this.incidents.get(sessionId) ?? []).find((x) => x.incident_id === incidentId);
      if (!inc) return err("NOT_FOUND", `Эпизод ${incidentId} не найден`);
      return {
        incident: inc,
        reviews: this.reviews.get(incidentId) ?? [],
        evidence: inc.evidence_ids.flatMap((id) => {
          const e = this.evidence.get(id);
          return e ? [e.item] : [];
        }),
      };
    });
  }

  addReview(sessionId: string, incidentId: string, body: HumanReviewCreate): Promise<BridgeResult<HumanReview>> {
    return this.respond(() => {
      const denied = this.gate("addReview");
      if (denied) return denied;
      const inc = (this.incidents.get(sessionId) ?? []).find((x) => x.incident_id === incidentId);
      if (!inc) return err("NOT_FOUND", `Эпизод ${incidentId} не найден`);
      if (!body.operator.trim()) return err("INVALID_ARGUMENT", "Укажите проверяющего");
      const list = this.reviews.get(incidentId) ?? [];
      const rev: HumanReview = {
        review_id: this.nextId("rev"),
        session_id: sessionId,
        incident_id: incidentId,
        decision: body.decision,
        comment: body.comment,
        operator: body.operator,
        created_at: nowIso(),
        supersedes_review_id: list.at(-1)?.review_id ?? null,
      };
      list.push(rev);
      this.reviews.set(incidentId, list);
      inc.review_status = body.decision;
      inc.update_seq += 1;
      this.emit({ type: "incident", change: { change: "updated", incident: inc } });
      return rev;
    });
  }

  getEvidence(sessionId: string, evidenceId: string): Promise<BridgeResult<EvidenceBlob>> {
    return this.respond(() => {
      const denied = this.gate("getEvidence");
      if (denied) return denied;
      const e = this.evidence.get(evidenceId);
      if (!e || e.item.session_id !== sessionId) return err("NOT_FOUND", "Материал не найден");
      return { media_type: e.item.media_type, bytes: e.bytes };
    });
  }

  getSummary(sessionId: string): Promise<BridgeResult<SessionSummary>> {
    return this.respond(() => {
      const denied = this.gate("getSummary");
      if (denied) return denied;
      const s = this.find(sessionId);
      if ("code" in s) return s;
      const incs = this.incidents.get(sessionId) ?? [];
      const byRule: Record<string, number> = {};
      const byDecision: Record<string, number> = {};
      for (const i of incs) {
        byRule[i.rule_id] = (byRule[i.rule_id] ?? 0) + 1;
        byDecision[i.review_status] = (byDecision[i.review_status] ?? 0) + 1; // A08 includes "pending"
      }
      const gaps = clone(this.gaps.get(sessionId) ?? []);
      const startT = s.exam_started_t_ms;
      const endT = TERMINAL.includes(s.state) && s.finished_at && s.started_at
        ? (startT ?? 0) + (Date.parse(s.finished_at) - Date.parse(s.started_at))
        : this.t();
      const capGapMs = gaps
        .filter((g) => g.component === "capture" && startT !== null)
        .reduce((acc, g) => acc + Math.max(0, (g.t_end_ms ?? endT) - Math.max(g.t_start_ms, startT ?? 0)), 0);
      const observed = startT === null ? 0 : Math.max(0, endT - startT - s.paused_total_ms - capGapMs);
      return {
        session: s,
        observed_ms: observed,
        paused_ms: s.paused_total_ms,
        gaps,
        incidents_total: incs.length,
        incidents_by_rule: byRule,
        reviews_by_decision: byDecision,
        limitations_ru: [
          "FIXTURE: данные FixtureBridge в памяти браузера — не настоящий backend и не CV.",
          "Синтетический источник кадров; точность распознавания не измерялась.",
          "Защита среды не проверялась (нет оболочки Electron).",
        ],
      };
    });
  }

  exportReport(sessionId: string, format: "html" | "json"): Promise<BridgeResult<SavedExport>> {
    return this.respond(() => {
      const denied = this.gate("exportReport");
      if (denied) return denied;
      const s = this.find(sessionId);
      if ("code" in s) return s;
      const incs = this.incidents.get(sessionId) ?? [];
      if (format === "html") {
        const esc = (v: string) => v.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
        const rows = incs
          .map((i) => `<tr><td>${esc(i.rule_id)}</td><td>${(i.duration_ms / 1000).toFixed(1)} s</td><td>${esc(i.review_status)}</td></tr>`)
          .join("");
        const html = `<!doctype html><meta charset="utf-8"><title>FIXTURE ${esc(sessionId)}</title><h1>FIXTURE report — not an A08 report</h1><p>${esc(sessionId)} · synthetic</p><table>${rows}</table>`;
        return this.download(`${sessionId}.fixture.html`, html, "text/html");
      }
      const payload = {
        fixture: true,
        note: "FIXTURE export from FixtureBridge — not a backend report",
        session: s,
        incidents: this.incidents.get(sessionId) ?? [],
        reviews: [...this.reviews.values()].flat().filter((r) => r.session_id === sessionId),
        answers: [...(this.answers.get(sessionId)?.values() ?? [])],
      };
      return this.download(`${sessionId}.fixture.json`, JSON.stringify(payload, null, 2), "application/json");
    });
  }

  private download(file_name: string, text: string, type: string): SavedExport {
    const url = URL.createObjectURL(new Blob([text], { type }));
    const a = document.createElement("a");
    a.href = url;
    a.download = file_name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    return { file_name, saved: true };
  }

  deleteSession(sessionId: string): Promise<BridgeResult<{ deleted: true }>> {
    return this.respond(() => {
      const denied = this.gate("deleteSession");
      if (denied) return denied;
      const s = this.find(sessionId);
      if ("code" in s) return s;
      if (!TERMINAL.includes(s.state)) return err("SESSION_ACTIVE", "Нельзя удалить активную сессию");
      this.sessions = this.sessions.filter((x) => x.session_id !== sessionId);
      for (const inc of this.incidents.get(sessionId) ?? []) {
        this.reviews.delete(inc.incident_id);
        for (const id of inc.evidence_ids) this.evidence.delete(id);
      }
      this.incidents.delete(sessionId);
      this.answers.delete(sessionId);
      this.gaps.delete(sessionId);
      if (this.active?.session_id === sessionId) this.active = null;
      return { deleted: true as const };
    });
  }

  // ================================================================== streams
  subscribeEvents(listener: (envelope: StreamEnvelope) => void): Unsubscribe {
    this.eventListeners.add(listener);
    queueMicrotask(() => {
      if (this.faults.backendDown) return;
      this.seq += 1;
      listener({
        contract: "qorgau.v1",
        seq: this.seq,
        sent_at: nowIso(),
        session_id: null,
        message: { type: "hello", contract: "qorgau.v1", contract_version: "1.0.0", backend_version: "fixture-0.1.0", server_time: nowIso() },
      });
    });
    return () => this.eventListeners.delete(listener);
  }

  subscribePreview(listener: (meta: PreviewFrameMeta, jpeg: Uint8Array) => void): Unsubscribe {
    this.previewListeners.add(listener);
    return () => this.previewListeners.delete(listener);
  }
}
