// Helpers around BridgeResult: a rejected promise or a thrown error becomes an ApiErrorBody,
// so every screen handles exactly one error shape.
import type { BridgeResult } from "@contracts/bridge";
import type { ApiErrorBody } from "@contracts/qorgau-v1.generated";

export type Result<T> = BridgeResult<T>;

export function bridgeFailure(message: string): ApiErrorBody {
  return { code: "INTERNAL", message, retryable: true, details: { source: "renderer" } };
}

export async function call<T>(p: Promise<BridgeResult<T>> | (() => Promise<BridgeResult<T>>)): Promise<BridgeResult<T>> {
  try {
    const r = await (typeof p === "function" ? p() : p);
    if (!r || typeof r !== "object" || !("ok" in r)) {
      return { ok: false, error: bridgeFailure("Оболочка вернула некорректный ответ") };
    }
    return r;
  } catch (e) {
    return { ok: false, error: bridgeFailure(e instanceof Error ? e.message : "Вызов оболочки завершился ошибкой") };
  }
}
