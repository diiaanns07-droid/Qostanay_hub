// Argument validation for every bridge call (owner: A06). Runs in Electron main BEFORE anything
// reaches the backend. Bodies must carry exactly the contract keys (CONTRACTS.md: TS requests send
// every key); the backend still validates fully with Pydantic. Pure functions, no Electron imports.
import {
  CalibrationTargetValues,
  ReviewDecisionValues,
  SourceModeValues,
  type AbortRequest,
  type AnswerUpsert,
  type CalibrationSkipRequest,
  type CalibrationTarget,
  type HumanReviewCreate,
  type PauseRequest,
  type SessionCreate,
} from "@contracts/qorgau-v1.generated";
import { DESK_SCAN_DURATION, DESK_SCAN_MODES, type DeskScanSkipRequest, type DeskScanStartArgs } from "../../../shared/desk-scan";

export class ValidationError extends Error {
  constructor(
    readonly field: string,
    readonly problem: string,
  ) {
    super(`${field}: ${problem}`);
    this.name = "ValidationError";
  }
}

const ID_RE = /^[A-Za-z0-9._:-]{1,128}$/;
const ISO_AWARE_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,9})?(Z|[+-]\d{2}:\d{2})$/;
/** Hard cap on any serialized argument (backend accepts <= 1 MB; nothing in the bridge needs more). */
export const MAX_ARG_JSON_BYTES = 64 * 1024;

type Obj = Record<string, unknown>;

function isPlainObject(v: unknown): v is Obj {
  if (typeof v !== "object" || v === null || Array.isArray(v)) return false;
  const proto = Object.getPrototypeOf(v);
  return proto === Object.prototype || proto === null;
}

function exactKeys(v: unknown, field: string, keys: readonly string[]): Obj {
  if (!isPlainObject(v)) throw new ValidationError(field, "must be an object");
  const got = Object.keys(v);
  for (const k of got) if (!keys.includes(k)) throw new ValidationError(`${field}.${k}`, "unknown field");
  for (const k of keys) if (!(k in v)) throw new ValidationError(`${field}.${k}`, "missing field");
  return v;
}

function str(v: unknown, field: string, min: number, max: number): string {
  if (typeof v !== "string") throw new ValidationError(field, "must be a string");
  if (v.length < min || v.length > max) throw new ValidationError(field, `length must be ${min}..${max}`);
  return v;
}

function nullableStr(v: unknown, field: string, max: number): string | null {
  return v === null ? null : str(v, field, 0, max);
}

function int(v: unknown, field: string, min: number, max: number): number {
  if (typeof v !== "number" || !Number.isInteger(v) || v < min || v > max) {
    throw new ValidationError(field, `must be an integer ${min}..${max}`);
  }
  return v;
}

function bool(v: unknown, field: string): boolean {
  if (typeof v !== "boolean") throw new ValidationError(field, "must be a boolean");
  return v;
}

function oneOf<T extends string>(v: unknown, field: string, values: readonly T[]): T {
  if (typeof v !== "string" || !(values as readonly string[]).includes(v)) {
    throw new ValidationError(field, `must be one of ${values.join("|")}`);
  }
  return v as T;
}

export function id(v: unknown, field = "id"): string {
  if (typeof v !== "string" || !ID_RE.test(v)) throw new ValidationError(field, "must match ^[A-Za-z0-9._:-]{1,128}$");
  // "." and ".." match the contract pattern but are dot-segments in a URL path: never route them.
  if (v === "." || v === "..") throw new ValidationError(field, "dot segment is not an id");
  return v;
}

export function checkSize(args: unknown[]): void {
  let size: number;
  try {
    size = Buffer.byteLength(JSON.stringify(args) ?? "", "utf8");
  } catch {
    throw new ValidationError("args", "not serializable");
  }
  if (size > MAX_ARG_JSON_BYTES) throw new ValidationError("args", `larger than ${MAX_ARG_JSON_BYTES} bytes`);
}

export function sessionCreate(v: unknown): SessionCreate {
  const o = exactKeys(v, "body", ["source", "exam_id", "student_label", "consent", "retain_media"]);
  const s = exactKeys(o.source, "body.source", ["mode", "camera_index", "replay_id", "width", "height", "fps"]);
  const c = exactKeys(o.consent, "body.consent", ["accepted", "text_version", "accepted_at"]);
  const mode = oneOf(s.mode, "body.source.mode", SourceModeValues);
  const replayId = s.replay_id === null ? null : id(s.replay_id, "body.source.replay_id");
  if (mode === "replay" && replayId === null) throw new ValidationError("body.source.replay_id", "required for replay");
  const acceptedAt = str(c.accepted_at, "body.consent.accepted_at", 20, 40);
  if (!ISO_AWARE_RE.test(acceptedAt)) throw new ValidationError("body.consent.accepted_at", "must be ISO-8601 with offset");
  return {
    source: {
      mode,
      camera_index: int(s.camera_index, "body.source.camera_index", 0, 16),
      replay_id: replayId,
      width: int(s.width, "body.source.width", 160, 1920),
      height: int(s.height, "body.source.height", 120, 1080),
      fps: int(s.fps, "body.source.fps", 1, 60),
    },
    exam_id: id(o.exam_id, "body.exam_id"),
    student_label: nullableStr(o.student_label, "body.student_label", 64),
    consent: {
      accepted: bool(c.accepted, "body.consent.accepted"),
      text_version: str(c.text_version, "body.consent.text_version", 1, 32),
      accepted_at: acceptedAt,
    },
    retain_media: bool(o.retain_media, "body.retain_media"),
  };
}

function reasonBody(v: unknown): { reason: string } {
  const o = exactKeys(v, "body", ["reason"]);
  return { reason: str(o.reason, "body.reason", 1, 200) };
}

export const pauseRequest = (v: unknown): PauseRequest => reasonBody(v);
export const abortRequest = (v: unknown): AbortRequest => reasonBody(v);
export const calibrationSkip = (v: unknown): CalibrationSkipRequest => reasonBody(v);
export const deskScanSkip = (v: unknown): DeskScanSkipRequest => reasonBody(v);

/** A15: {duration_s 5..30, mode laptop|usb}; mode goes to the query string, duration_s to the body. */
export function deskScanStart(v: unknown): DeskScanStartArgs {
  const o = exactKeys(v, "body", ["duration_s", "mode"]);
  return {
    duration_s: int(o.duration_s, "body.duration_s", DESK_SCAN_DURATION.min, DESK_SCAN_DURATION.max),
    mode: oneOf(o.mode, "body.mode", DESK_SCAN_MODES),
  };
}

export function calibrationTarget(v: unknown): CalibrationTarget {
  return oneOf(v, "target", CalibrationTargetValues);
}

export function answerUpsert(v: unknown): AnswerUpsert {
  const o = exactKeys(v, "body", ["value", "client_seq"]);
  let value: string | string[];
  if (Array.isArray(o.value)) {
    if (o.value.length > 64) throw new ValidationError("body.value", "at most 64 options");
    value = o.value.map((x, i) => id(x, `body.value[${i}]`));
  } else {
    value = str(o.value, "body.value", 0, 4000);
  }
  return { value, client_seq: int(o.client_seq, "body.client_seq", 0, Number.MAX_SAFE_INTEGER) };
}

export function humanReviewCreate(v: unknown): HumanReviewCreate {
  const o = exactKeys(v, "body", ["decision", "comment", "operator"]);
  return {
    decision: oneOf(o.decision, "body.decision", ReviewDecisionValues),
    comment: str(o.comment, "body.comment", 0, 2000),
    operator: str(o.operator, "body.operator", 1, 64),
  };
}

export function exportFormat(v: unknown): "html" | "json" {
  return oneOf(v, "format", ["html", "json"] as const);
}

export function pin(v: unknown): string {
  const p = str(v, "pin", 4, 64);
  if (!/^[0-9A-Za-z]+$/.test(p)) throw new ValidationError("pin", "only letters and digits");
  return p;
}

export function emergencyReason(v: unknown): string {
  return str(v, "reason", 1, 200);
}
