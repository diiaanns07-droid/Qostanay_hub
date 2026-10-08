import type { BrowserWindow } from "electron";
import type { BackendClient } from "./backend/client";
import { LOCK_RECEIPT_KEYS, parseLockRequest, receiptFor, type LockReceiptResult, type LockRequest } from "../../shared/class-lock";
export { LOCK_ACK_CHANNEL } from "../../shared/class-lock";

/** No kiosk, keyboard hooks, clipboard changes or OS-lock claims. */
export class ClassLockController {
  private request: LockRequest | null = null;
  private effective = false;
  private requestSerial = 0;
  constructor(private readonly deps: {
    client: Pick<BackendClient, "json">;
    window: () => BrowserWindow | null;
    setExamBlocked: (blocked: boolean) => void;
  }) {}

  consumeClassState(value: unknown): void {
    if (!value || typeof value !== "object") return;
    const msg = value as Record<string, unknown>;
    if (msg.type !== "class_state" || typeof msg.locked !== "boolean") return;
    const request = msg.lock_request == null ? null : parseLockRequest(msg.lock_request);
    if (msg.lock_request != null && !request) return; // malformed input never releases a lock
    if (request?.request_token !== this.request?.request_token) this.requestSerial++;
    this.request = request;
    this.effective = msg.locked;
    // A child web surface must be detached before the renderer sees a pending lock.
    this.deps.setExamBlocked(msg.locked || request?.locked === true);
  }

  reset(): void {
    this.requestSerial++;
    this.request = null;
    this.effective = false;
    this.deps.setExamBlocked(true); // backend loss cannot reveal an unverified exam surface
  }

  async confirmApplied(value: unknown): Promise<LockReceiptResult> {
    const request = this.request;
    if (!request || !value || typeof value !== "object") return { accepted: false, reason: "no_pending_request" };
    const receipt = value as Record<string, unknown>;
    if (LOCK_RECEIPT_KEYS.some(key => receipt[key] !== request[key]) || typeof receipt.applied !== "boolean"
      || receipt.reason_ru !== request.reason_ru || Date.parse(request.expires_at) <= Date.now()) {
      return { accepted: false, reason: "stale_or_mismatched_receipt" };
    }
    const w = this.deps.window();
    if (!w || w.isDestroyed() || w.webContents.isDestroyed() || !w.isVisible()) {
      return { accepted: false, reason: "renderer_unavailable" };
    }
    const serial = this.requestSerial;
    // Independently inspect the main frame after React's painted-state receipt.
    // Only bounded validated request data enters JSON literals (no executable text).
    let rendered = false;
    try {
      rendered = await w.webContents.executeJavaScript(`(() => {
        const r = ${JSON.stringify(request)};
        const app = document.querySelector('[data-adal-app]');
        const overlay = document.querySelector('[data-adal-lock]');
        if (document.visibilityState !== 'visible' || !app) return false;
        if (!r.locked) return !overlay && !app.inert && app.getAttribute('aria-hidden') !== 'true';
        if (!overlay || !app.inert || app.getAttribute('aria-hidden') !== 'true') return false;
        const box = overlay.getBoundingClientRect(), css = getComputedStyle(overlay);
        return overlay.getAttribute('data-lock-token') === r.request_token
          && document.querySelector('[data-lock-reason]')?.textContent === r.reason_ru
          && css.visibility === 'visible' && css.display !== 'none' && Number(css.opacity) === 1
          && box.left <= 0 && box.top <= 0 && box.right >= innerWidth && box.bottom >= innerHeight
          && overlay.contains(document.elementFromPoint(innerWidth / 2, innerHeight / 2));
      })()`) === true;
    } catch { /* renderer was destroyed or replaced */ }
    if (serial !== this.requestSerial) return { accepted: false, reason: "request_changed" };
    const applied = receipt.applied && rendered;
    const response = await this.deps.client.json<LockReceiptResult>("POST", "/v1/class/lock/ack", receiptFor(request, applied), 2500);
    if (!response.ok) return { accepted: false, reason: "backend_unavailable" };
    if (response.data.accepted && applied && serial === this.requestSerial) {
      this.effective = request.locked;
      this.deps.setExamBlocked(this.effective);
    }
    return response.data;
  }
}
