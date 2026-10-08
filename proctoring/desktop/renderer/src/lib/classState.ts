// class_state stream message from the C2 uplink (handoffs/C2/STATUS.md). Additive, not in qorgau.v1 StreamPayload,
// so it is parsed defensively here; anything malformed is ignored (never shown half-valid).
import { parseLockRequest, type LockRequest } from "../../../shared/class-lock";
export type ClassConnection = "connecting" | "connected" | "reconnecting" | "rejected" | "stopped";

export interface ClassState {
  connection: ClassConnection;
  server: string;
  student_id: string | null;
  locked: boolean;
  lock_reason_ru: string | null;
  lock_request?: LockRequest | null;
  lock_state?: "requested" | "applied" | "failed" | "unconfirmed";
  lock_error_ru?: string | null;
  mic_active: boolean;
  audio_direction: "listen" | "talk" | "both" | null;
  exam: { title?: string } | null;
  message_ru: string | null;
  computer_name: string | null; // not sent by C2 yet (request in handoffs/A07/STUDENT.md)
}

const CONNECTIONS: readonly string[] = ["connecting", "connected", "reconnecting", "rejected", "stopped"];
const str = (v: unknown, max = 300): string | null => (typeof v === "string" && v.length > 0 ? v.slice(0, max) : null);

/** Optional local metadata added by Electron to the existing health bridge result. */
export function parseClassHealth(report: unknown): { computerName: string | null; configured: boolean | null } {
  const o = report && typeof report === "object" ? report as Record<string, unknown> : {};
  return { computerName: str(o.computer_name, 64), configured: typeof o.class_configured === "boolean" ? o.class_configured : null };
}

export function parseClassState(m: unknown): ClassState | null {
  if (typeof m !== "object" || m === null) return null;
  const o = m as Record<string, unknown>;
  if (o.type !== "class_state" || typeof o.connection !== "string" || !CONNECTIONS.includes(o.connection)) return null;
  // Missing or mistyped flags must never turn a current lock/banner off.
  if (typeof o.locked !== "boolean" || typeof o.mic_active !== "boolean" || typeof o.server !== "string") return null;
  const request = o.lock_request == null ? null : parseLockRequest(o.lock_request);
  if (o.lock_request != null && (!request || request.student_id !== o.student_id
    || request.backend_instance_id !== o.backend_instance_id || request.class_session_id !== o.class_session_id
    || request.source_session_id !== o.source_session_id)) return null;
  const dir = o.audio_direction;
  return {
    connection: o.connection as ClassConnection,
    server: str(o.server, 260) ?? "",
    student_id: str(o.student_id, 64),
    locked: o.locked === true,
    lock_reason_ru: str(o.lock_reason_ru, 200),
    lock_request: request,
    lock_state: o.lock_state === "requested" || o.lock_state === "applied" || o.lock_state === "failed" ? o.lock_state : "unconfirmed",
    lock_error_ru: str(o.lock_error_ru, 200),
    mic_active: o.mic_active === true,
    audio_direction: dir === "listen" || dir === "talk" || dir === "both" ? dir : null,
    exam: typeof o.exam === "object" && o.exam !== null ? { title: str((o.exam as Record<string, unknown>).title, 200) ?? undefined } : null,
    message_ru: str(o.message_ru),
    computer_name: str(o.computer_name, 64),
  };
}

export const CONNECTION_RU: Record<ClassConnection, { text: string; tone: "ok" | "warn" | "danger" | "neutral" }> = {
  connected: { text: "подключено", tone: "ok" },
  connecting: { text: "подключение…", tone: "neutral" },
  reconnecting: { text: "переподключение: сервер класса не отвечает", tone: "warn" },
  rejected: { text: "неверный код подключения", tone: "danger" },
  stopped: { text: "связь с классом остановлена", tone: "neutral" },
};
