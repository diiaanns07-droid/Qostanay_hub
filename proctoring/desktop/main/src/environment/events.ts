// Environment events -> backend (owner: A06). POST /v1/sessions/{sid}/environment/events.
// client_seq is strictly increasing per shell run (backend dedup key), so retries are safe.
// Bounded queue; a full queue drops the oldest events and says so (never silently).
import type {
  EnforcementResult,
  EnforcementScope,
  EnvironmentAction,
  EnvironmentDetail,
  EnvironmentEventAck,
  EnvironmentEventIn,
} from "@contracts/qorgau-v1.generated";
import { BackendClient } from "../backend/client";
import { logger } from "../log";

const log = logger("env-events");
const MECHANISM_RE = /^[a-z0-9_.:-]{1,64}$/;
const PROCESS_RE = /^[A-Za-z0-9 ._()-]{1,64}$/;

export interface EnvEventInput {
  action: EnvironmentAction;
  enforcement: EnforcementResult;
  mechanism: string;
  scope: EnforcementScope;
  detail?: Partial<EnvironmentDetail>;
}

interface Pending {
  sessionId: string;
  event: EnvironmentEventIn;
}

export function sanitizeDetail(d: Partial<EnvironmentDetail> | undefined): EnvironmentDetail {
  const process_name = d?.process_name && PROCESS_RE.test(d.process_name) ? d.process_name : null;
  const shortcut = typeof d?.shortcut === "string" ? d.shortcut.slice(0, 32) : null;
  const duration_ms = typeof d?.duration_ms === "number" && d.duration_ms >= 0 && Number.isFinite(d.duration_ms) ? d.duration_ms : null;
  return { process_name, shortcut, duration_ms };
}

export interface EventSink {
  emit(e: EnvEventInput): void;
}

export class EnvironmentEventQueue implements EventSink {
  private seq = 0;
  private queue: Pending[] = [];
  private timer: NodeJS.Timeout | null = null;
  private inflight: Promise<void> | null = null;
  private failures = 0;
  dropped = 0;
  sent = 0;

  constructor(
    private readonly client: BackendClient | null,
    private readonly sessionId: () => string | null,
    private readonly opts: { maxQueue?: number; flushDelayMs?: number; now?: () => Date } = {},
  ) {}

  /** Last assigned client_seq (tests). */
  get lastSeq(): number {
    return this.seq;
  }

  get pending(): number {
    return this.queue.length;
  }

  emit(e: EnvEventInput): void {
    const sid = this.sessionId();
    if (!sid) {
      log.debug(`no bound session, not reported: ${e.action}`);
      return;
    }
    if (!MECHANISM_RE.test(e.mechanism)) {
      log.error(`invalid mechanism name ${JSON.stringify(e.mechanism)}`);
      return;
    }
    this.seq += 1;
    const event: EnvironmentEventIn = {
      action: e.action,
      enforcement: e.enforcement,
      mechanism: e.mechanism,
      scope: e.scope,
      client_seq: this.seq,
      client_wall_time: (this.opts.now?.() ?? new Date()).toISOString(),
      detail: sanitizeDetail(e.detail),
    };
    this.queue.push({ sessionId: sid, event });
    const max = this.opts.maxQueue ?? 1000;
    if (this.queue.length > max) {
      const n = this.queue.length - max;
      this.queue.splice(0, n);
      this.dropped += n;
      log.warn(`environment event queue full: dropped ${n} oldest (total ${this.dropped})`);
    }
    this.schedule(this.opts.flushDelayMs ?? 150);
  }

  private schedule(delay: number): void {
    if (this.timer || !this.client) return;
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.flush();
    }, delay);
  }

  /** Send everything queued (in batches of <= 100 per session). Resolves when done or failed once. */
  async flush(): Promise<void> {
    if (!this.client) return;
    if (this.inflight) return this.inflight;
    this.inflight = this.flushLoop().finally(() => {
      this.inflight = null;
    });
    return this.inflight;
  }

  private async flushLoop(): Promise<void> {
    const client = this.client!;
    while (this.queue.length > 0) {
      const sid = this.queue[0]!.sessionId;
      const batch: Pending[] = [];
      for (const p of this.queue) {
        if (p.sessionId !== sid || batch.length >= 100) break;
        batch.push(p);
      }
      const r = await client.json<EnvironmentEventAck>("POST", BackendClient.path("sessions", sid, "environment", "events"), {
        session_id: sid,
        events: batch.map((b) => b.event),
      });
      if (r.ok) {
        this.queue.splice(0, batch.length);
        this.sent += batch.length;
        this.failures = 0;
        continue;
      }
      const code = r.error.code;
      if (code === "INVALID_STATE" || code === "SESSION_NOT_FOUND" || code === "SESSION_MISMATCH" || code === "INVALID_ARGUMENT") {
        // session is over / unknown, or the batch itself is invalid: retrying cannot help
        log.warn(`dropping ${batch.length} environment event(s) for ${sid}: ${code}`);
        this.queue.splice(0, batch.length);
        this.dropped += batch.length;
        continue;
      }
      this.failures += 1;
      log.warn(`environment events not delivered (${code}); retry #${this.failures}`);
      this.schedule(Math.min(10_000, 250 * 2 ** this.failures));
      return;
    }
  }
}
