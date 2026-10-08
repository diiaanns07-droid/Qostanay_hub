// Shell-side errors expressed as contract ApiErrorBody (owner: A06).
// Shell-specific causes keep a contract ErrorCode and add details.shell_code.
import type { BridgeResult } from "@contracts/bridge";
import type { ApiErrorBody, ErrorCode } from "@contracts/qorgau-v1.generated";

export type ShellCode =
  | "invalid_argument"
  | "untrusted_sender"
  | "backend_unavailable"
  | "backend_bad_response"
  | "operator_locked"
  | "operator_pin_not_configured"
  | "operator_pin_wrong"
  | "operator_pin_rate_limited"
  | "session_not_bound"
  | "exam_mode_active"
  | "enforcement_error"
  | "save_cancelled";

export function shellError(
  code: ErrorCode,
  shellCode: ShellCode,
  message: string,
  retryable = false,
  extra: Record<string, string | number | boolean> = {},
): ApiErrorBody {
  return { code, message: message.slice(0, 1000), retryable, details: { shell_code: shellCode, ...extra } };
}

export function fail<T>(error: ApiErrorBody): BridgeResult<T> {
  return { ok: false, error };
}

export function ok<T>(data: T): BridgeResult<T> {
  return { ok: true, data };
}

export const backendUnavailable = (why: string): ApiErrorBody =>
  shellError("INTERNAL", "backend_unavailable", `Local backend is not available: ${why}`, true);
