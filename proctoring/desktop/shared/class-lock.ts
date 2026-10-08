/** Local, scoped overlay receipt. No backend credential or generic IPC is exposed. */
export const LOCK_ACK_CHANNEL = "adal:class-lock:applied";
export interface LockRequest {
  command_id: string;
  student_id: string;
  class_session_id: string | null;
  source_session_id: string | null;
  backend_instance_id: string;
  request_token: string;
  locked: boolean;
  reason_ru: string | null;
  expires_at: string;
  recovery: boolean;
}
export type LockReceipt = Omit<LockRequest, "expires_at" | "recovery"> & { applied: boolean };
export interface LockReceiptResult { accepted: boolean; reason?: string }
export const LOCK_RECEIPT_KEYS = ["command_id", "student_id", "class_session_id", "source_session_id", "backend_instance_id", "request_token", "locked"] as const;

const id = (v: unknown): v is string => typeof v === "string" && /^[A-Za-z0-9._:-]{1,128}$/.test(v);
export function parseLockRequest(value: unknown): LockRequest | null {
  if (!value || typeof value !== "object") return null;
  const r = value as Record<string, unknown>;
  if (![r.command_id, r.student_id, r.backend_instance_id, r.request_token].every(id)
    || ![r.class_session_id, r.source_session_id].every(v => v === null || id(v))
    || typeof r.locked !== "boolean" || typeof r.expires_at !== "string" || !Number.isFinite(Date.parse(r.expires_at))
    || !(r.reason_ru === null || (typeof r.reason_ru === "string" && r.reason_ru.length <= 200))
    || (r.locked && (typeof r.reason_ru !== "string" || !r.reason_ru.trim()))) return null;
  return { command_id: r.command_id as string, student_id: r.student_id as string,
    class_session_id: r.class_session_id as string | null, source_session_id: r.source_session_id as string | null,
    backend_instance_id: r.backend_instance_id as string, request_token: r.request_token as string,
    locked: r.locked, reason_ru: r.reason_ru as string | null, expires_at: r.expires_at, recovery: r.recovery === true };
}
export function receiptFor(request: LockRequest, applied: boolean): LockReceipt {
  const { expires_at: _expiry, recovery: _recovery, ...receipt } = request;
  return { ...receipt, applied };
}
declare global {
  interface Window {
    qorgauLock?: { confirmApplied(receipt: LockReceipt): Promise<LockReceiptResult> };
  }
}
