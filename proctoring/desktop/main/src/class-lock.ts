import type { BrowserWindow } from "electron";
import type { BackendClient } from "./backend/client";
import type { StreamEnvelope } from "@contracts/qorgau-v1.generated";
import { LOCK_RECEIPT_KEYS, parseLockRequest, receiptFor, type LockReceiptResult, type LockRequest } from "../../shared/class-lock";
export { LOCK_ACK_CHANNEL } from "../../shared/class-lock";

/** No kiosk, keyboard hooks, clipboard changes or OS-lock claims. */
export class ClassLockController {
  private request: LockRequest | null = null;
  private effective = false;
  private backendInstanceId: string | null = null;
  private requestSerial = 0;
  private lifecycleSerial = 0;
  private lost: Promise<void> = Promise.resolve();
  private currentState: unknown = null;
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
    this.currentState = value;
    if (request?.request_token !== this.request?.request_token) this.requestSerial++;
    this.request = request;
    this.backendInstanceId = typeof msg.backend_instance_id === "string" ? msg.backend_instance_id : request?.backend_instance_id ?? null;
    this.effective = msg.locked;
    // A child web surface must be detached before the renderer sees a pending lock.
    this.deps.setExamBlocked(msg.locked || request?.locked === true || msg.lock_requested === true);
  }

  reset(): void {
    this.lifecycleSerial++;
    this.requestSerial++;
    this.request = null;
    this.effective = false;
    this.backendInstanceId = null;
    this.currentState = null;
    this.deps.setExamBlocked(true); // backend loss cannot reveal an unverified exam surface
  }

  async rendererLost(): Promise<void> {
    const backendInstanceId = this.backendInstanceId;
    const serial = ++this.lifecycleSerial;
    this.invalidateRendererProof();
    // Keep the scoped backend id until actual backend loss. Its first recovery request may
    // precede the next renderer's listeners; ready() must be able to request a fresh one.
    this.lost = this.lost.then(async () => {
      if (serial !== this.lifecycleSerial || backendInstanceId !== this.backendInstanceId) return;
      if (backendInstanceId) await this.deps.client.json("POST", "/v1/class/lock/lost", { backend_instance_id: backendInstanceId }, 2500);
    }).catch(() => {});
    await this.lost;
  }

  /** Only main's trusted subscription notification calls this; it never acknowledges a lock. */
  async rendererReady(isCurrent: () => boolean): Promise<boolean> {
    const serial = this.lifecycleSerial;
    await this.lost; // never let an older invalidation arrive after the fresh recovery request
    if (serial !== this.lifecycleSerial || !isCurrent()) return false;
    const backendInstanceId = this.backendInstanceId;
    if (!backendInstanceId) return true; // first backend state has not arrived yet
    this.invalidateRendererProof();
    const result = await this.deps.client.json<{ accepted: boolean }>("POST", "/v1/class/lock/lost", { backend_instance_id: backendInstanceId }, 2500);
    return result.ok && result.data.accepted && serial === this.lifecycleSerial && isCurrent();
  }

  private invalidateRendererProof(): void {
    this.currentState = null;
    this.requestSerial++;
    this.request = null;
    this.effective = false;
    this.deps.setExamBlocked(true);
  }

  isCurrentClassState(value: unknown): boolean { return value === this.currentState; }

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
        // Fixed-position CSS bounds exclude classic scrollbars; innerWidth includes them.
        const width = document.documentElement.clientWidth, height = document.documentElement.clientHeight;
        return overlay.getAttribute('data-lock-token') === r.request_token
          && document.querySelector('[data-lock-reason]')?.textContent === r.reason_ru
          && css.visibility === 'visible' && css.display !== 'none' && Number(css.opacity) === 1
          // Windows display scaling rounds innerHeight but preserves fractional DOM bounds.
          && width > 0 && height > 0
          && box.left <= 0 && box.top <= 0 && box.right >= width - 1 && box.bottom >= height - 1
          && overlay.contains(document.elementFromPoint(width / 2, height / 2));
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

/** Retain only current class state for late subscribers, never audio commands/session events. */
export class ClassStateDelivery {
  private latest: StreamEnvelope | null = null;
  private serial = 0;
  private listening = false;
  private recovering = false;
  constructor(private readonly deps: {
    recover: (isCurrent: () => boolean) => Promise<boolean>;
    send: (envelope: StreamEnvelope) => void;
    sessionId: () => string | null;
    currentState: (message: unknown) => boolean;
  }) {}

  /** Backend/session changes invalidate cached state and in-flight recovery; navigation also loses listeners. */
  invalidate(rendererLost = false): void {
    this.serial++;
    this.latest = null;
    this.recovering = false;
    if (rendererLost) this.listening = false;
  }

  consume(envelope: StreamEnvelope): boolean {
    if ((envelope.message as { type: string }).type !== "class_state") return false;
    this.latest = envelope;
    this.replay();
    return true;
  }

  async subscribed(listening: boolean): Promise<void> {
    if (!listening) { this.invalidate(true); return; }
    if (this.listening) { this.replay(); return; }
    this.listening = true;
    this.recovering = true;
    const serial = this.serial;
    const current = () => serial === this.serial && this.listening;
    let recovered = false;
    try { recovered = await this.deps.recover(current); } catch { /* fail closed; future authoritative state may recover */ }
    if (!current()) return;
    this.recovering = false;
    if (recovered) this.replay(); else this.latest = null;
  }

  private replay(): void {
    if (!this.listening || this.recovering || !this.latest) return;
    const msg = this.latest.message as unknown as Record<string, unknown>;
    if (!this.deps.currentState(msg)) return;
    if ((msg.source_session_id ?? null) !== this.deps.sessionId()) return;
    if (msg.lock_request != null) {
      const request = parseLockRequest(msg.lock_request);
      if (!request || Date.parse(request.expires_at) <= Date.now()
        || request.backend_instance_id !== msg.backend_instance_id
        || request.class_session_id !== msg.class_session_id || request.source_session_id !== msg.source_session_id
        || request.student_id !== msg.student_id) return;
    }
    this.deps.send(this.latest);
  }
}
