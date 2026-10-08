// Qorgau Exam — renderer <-> shell bridge contract v1 (HAND-WRITTEN, owner: A01).
//
// Exposed by A06 preload as `window.qorgau` via contextBridge.exposeInMainWorld("qorgau", bridge).
// Consumed by A07 renderer. A07 may implement a FixtureBridge with the same interface for
// development; it MUST visibly label fixture/synthetic data.
//
// Security rules (normative):
//   * The renderer never sees the backend port/token; Electron main adds the bearer token.
//   * There is NO generic request/exec/fs method. Every method maps to one fixed backend route.
//   * Arguments are validated in main (ids: ^[A-Za-z0-9._:-]{1,128}$, bodies: wire types).
//   * Binary preview arrives as Uint8Array; render via Blob URL / createImageBitmap.

import type {
  AbortRequest,
  AnswerRecord,
  AnswerUpsert,
  ApiErrorBody,
  CalibrationSkipRequest,
  CalibrationState,
  CalibrationTarget,
  EnvironmentCapabilities,
  ExamDefinition,
  HealthReport,
  HumanReview,
  HumanReviewCreate,
  Incident,
  IncidentDetail,
  PauseRequest,
  PreflightReport,
  PreviewFrameMeta,
  SessionCreate,
  SessionInfo,
  SessionSummary,
  StreamEnvelope,
} from "./qorgau-v1.generated";

export const BRIDGE_VERSION = "1.0.0" as const;

/** Result of every bridge call. Errors use the backend ApiErrorBody codes (plus shell codes). */
export type BridgeResult<T> = { ok: true; data: T } | { ok: false; error: ApiErrorBody };

export type Unsubscribe = () => void;

/** Shell (Electron main) state machine, owned by A06. */
export type ShellMode = "normal" | "preflight" | "exam" | "releasing" | "error";
export type BackendProcessState = "starting" | "ready" | "restarting" | "failed" | "stopped";

export interface ShellState {
  mode: ShellMode;
  backend: BackendProcessState;
  /** True while OS/window restrictions are engaged (session RUNNING). */
  exam_mode_active: boolean;
  /** Teacher/operator view unlocked by operatorUnlock(); always false at shell start. */
  operator_unlocked: boolean;
  /** Bound session, or null. */
  session_id: string | null;
  last_error: ApiErrorBody | null;
  shell_version: string;
  platform: string;
}

export interface SavedExport {
  file_name: string;
  /** false when the user cancelled the save dialog. */
  saved: boolean;
}

export interface EvidenceBlob {
  media_type: "image/jpeg" | "video/mp4" | "video/webm";
  bytes: Uint8Array;
}

/** `window.qorgau` */
export interface QorgauBridge {
  readonly bridgeVersion: typeof BRIDGE_VERSION;
  /** "live" only when talking to the real backend through Electron; fixture bridges return "fixture". */
  readonly transport: "electron" | "fixture";

  // --- shell (A06) ---
  getShellState(): Promise<ShellState>;
  onShellState(listener: (state: ShellState) => void): Unsubscribe;
  getEnvironmentCapabilities(): Promise<BridgeResult<EnvironmentCapabilities>>;
  /** Operator PIN check happens in main. Unlock is cleared when a new exam starts. */
  operatorUnlock(pin: string): Promise<BridgeResult<ShellState>>;
  operatorLock(): Promise<ShellState>;
  /** Guaranteed emergency exit: aborts the session and releases every restriction. */
  requestEmergencyExit(reason: string): Promise<BridgeResult<ShellState>>;

  // --- backend lifecycle (A01 routes) ---
  health(): Promise<BridgeResult<HealthReport>>;
  listSessions(): Promise<BridgeResult<SessionInfo[]>>;
  createSession(body: SessionCreate): Promise<BridgeResult<SessionInfo>>;
  getSession(sessionId: string): Promise<BridgeResult<SessionInfo>>;
  runPreflight(sessionId: string): Promise<BridgeResult<PreflightReport>>;
  calibrationStart(sessionId: string): Promise<BridgeResult<CalibrationState>>;
  calibrationTarget(sessionId: string, target: CalibrationTarget): Promise<BridgeResult<CalibrationState>>;
  calibrationState(sessionId: string): Promise<BridgeResult<CalibrationState>>;
  calibrationFinish(sessionId: string): Promise<BridgeResult<CalibrationState>>;
  calibrationCancel(sessionId: string): Promise<BridgeResult<CalibrationState>>;
  calibrationSkip(sessionId: string, body: CalibrationSkipRequest): Promise<BridgeResult<CalibrationState>>;
  startExam(sessionId: string): Promise<BridgeResult<SessionInfo>>;
  /** Operator only (requires operator_unlocked in main). */
  pauseExam(sessionId: string, body: PauseRequest): Promise<BridgeResult<SessionInfo>>;
  resumeExam(sessionId: string): Promise<BridgeResult<SessionInfo>>;
  finishExam(sessionId: string): Promise<BridgeResult<SessionInfo>>;
  abortExam(sessionId: string, body: AbortRequest): Promise<BridgeResult<SessionInfo>>;
  getExam(sessionId: string): Promise<BridgeResult<ExamDefinition>>;

  // --- storage / review / report (A08 routes) ---
  saveAnswer(sessionId: string, questionId: string, body: AnswerUpsert): Promise<BridgeResult<AnswerRecord>>;
  listAnswers(sessionId: string): Promise<BridgeResult<AnswerRecord[]>>;
  listIncidents(sessionId: string): Promise<BridgeResult<Incident[]>>;
  getIncident(sessionId: string, incidentId: string): Promise<BridgeResult<IncidentDetail>>;
  addReview(sessionId: string, incidentId: string, body: HumanReviewCreate): Promise<BridgeResult<HumanReview>>;
  getEvidence(sessionId: string, evidenceId: string): Promise<BridgeResult<EvidenceBlob>>;
  getSummary(sessionId: string): Promise<BridgeResult<SessionSummary>>;
  /** Main fetches the report and shows a native save dialog; the renderer never gets a path. */
  exportReport(sessionId: string, format: "html" | "json"): Promise<BridgeResult<SavedExport>>;
  deleteSession(sessionId: string): Promise<BridgeResult<{ deleted: true }>>;

  // --- streams (main keeps one WebSocket each to the backend and fans out) ---
  subscribeEvents(listener: (envelope: StreamEnvelope) => void): Unsubscribe;
  subscribePreview(listener: (meta: PreviewFrameMeta, jpeg: Uint8Array) => void): Unsubscribe;
}

declare global {
  interface Window {
    qorgau?: QorgauBridge;
  }
}
