// Answer autosave (A07). One entry per question; the newest local edit wins by client_seq (A08: last writer by
// client_seq, a stale seq returns the stored record). Guarantees:
// * at most one request in flight per question; an edit made meanwhile is sent right after the response;
// * an answer is "saved" only when the backend acknowledged a client_seq >= the latest local edit;
// * retryable failures (disk/STORAGE_ERROR, connection) retry with backoff; while offline or paused nothing is
//   sent and the entry waits; going online resends every unacknowledged entry;
// * unacknowledged values are stashed in sessionStorage (same app run) so a renderer reload does not lose them;
// * restoring merges server records and the stash by client_seq — no duplicates, newest value wins.
import type { BridgeResult } from "@contracts/bridge";
import type { AnswerRecord, AnswerUpsert, ApiErrorBody } from "@contracts/qorgau-v1.generated";

export type AnswerValue = string | string[];
export type SaveStatus = "saved" | "pending" | "saving" | "waiting" | "error";

interface Entry {
  value: AnswerValue;
  seq: number;
  ackSeq: number;
  inflight: Promise<void> | null;
  error: ApiErrorBody | null;
  attempts: number;
  retry: ReturnType<typeof setTimeout> | null;
  debounce: ReturnType<typeof setTimeout> | null;
}

type Send = (questionId: string, body: AnswerUpsert) => Promise<BridgeResult<AnswerRecord>>;

const MAX_BACKOFF_MS = 15_000;

function stashKey(sessionId: string): string {
  return `qorgau.answers.${sessionId}`;
}

export function readStash(sessionId: string): Record<string, { value: AnswerValue; seq: number }> {
  try {
    const raw = window.sessionStorage.getItem(stashKey(sessionId));
    const v = raw ? (JSON.parse(raw) as unknown) : null;
    return v && typeof v === "object" ? (v as Record<string, { value: AnswerValue; seq: number }>) : {};
  } catch {
    return {};
  }
}

export class AnswerSync {
  private entries = new Map<string, Entry>();
  private seq = 0;
  private online = true;

  constructor(
    private readonly sessionId: string,
    private readonly send: Send,
    private readonly onChange: () => void,
  ) {}

  /** Stops timers (unmount). In-flight requests still settle; idempotent (React StrictMode re-runs effects). */
  dispose(): void {
    for (const e of this.entries.values()) {
      if (e.retry) clearTimeout(e.retry);
      if (e.debounce) clearTimeout(e.debounce);
      e.retry = null;
      e.debounce = null;
    }
  }

  private nextSeq(): number {
    // Strictly increasing within this run and across reloads (server/stash seqs are folded in by restore()).
    this.seq = Math.max(this.seq + 1, Date.now());
    return this.seq;
  }

  private entry(qid: string): Entry {
    let e = this.entries.get(qid);
    if (!e) {
      e = { value: "", seq: 0, ackSeq: 0, inflight: null, error: null, attempts: 0, retry: null, debounce: null };
      this.entries.set(qid, e);
    }
    return e;
  }

  /** Merge stored answers and the local stash. Returns the values to show. */
  restore(records: AnswerRecord[]): Record<string, AnswerValue> {
    for (const r of records) {
      const e = this.entry(r.question_id);
      if (r.client_seq >= e.seq) {
        e.value = r.value;
        e.seq = r.client_seq;
      }
      e.ackSeq = Math.max(e.ackSeq, r.client_seq);
      this.seq = Math.max(this.seq, r.client_seq);
    }
    const stash = readStash(this.sessionId);
    for (const [qid, s] of Object.entries(stash)) {
      if (!s || typeof s.seq !== "number") continue;
      const e = this.entry(qid);
      if (s.seq > e.seq) {
        e.value = s.value;
        e.seq = s.seq;
      }
      this.seq = Math.max(this.seq, s.seq);
    }
    this.writeStash();
    const out: Record<string, AnswerValue> = {};
    for (const [qid, e] of this.entries) out[qid] = e.value;
    void this.flushAll();
    this.onChange();
    return out;
  }

  edit(qid: string, value: AnswerValue, debounceMs: number): void {
    const e = this.entry(qid);
    e.value = value;
    e.seq = this.nextSeq();
    e.error = null;
    e.attempts = 0;
    if (e.retry) clearTimeout(e.retry);
    e.retry = null;
    this.writeStash();
    if (e.debounce) clearTimeout(e.debounce);
    e.debounce = setTimeout(() => {
      e.debounce = null;
      void this.flush(qid);
    }, debounceMs);
    this.onChange();
  }

  /** Online = backend reachable AND the session accepts answers (running). Going online resends everything. */
  setOnline(online: boolean): void {
    if (this.online === online) return;
    this.online = online;
    this.onChange();
    if (online) void this.flushAll();
  }

  flush(qid: string): Promise<void> {
    const e = this.entries.get(qid);
    if (!e) return Promise.resolve();
    if (e.debounce) {
      clearTimeout(e.debounce);
      e.debounce = null;
    }
    if (e.inflight) return e.inflight;
    if (e.ackSeq >= e.seq || !this.online) {
      this.onChange();
      return Promise.resolve();
    }
    const sent = e.seq;
    const body: AnswerUpsert = { value: e.value, client_seq: sent };
    e.error = null;
    const p = this.send(qid, body)
      .catch(
        (err: unknown): BridgeResult<AnswerRecord> => ({
          ok: false,
          error: { code: "INTERNAL", message: err instanceof Error ? err.message : "send failed", retryable: true, details: {} },
        }),
      )
      .then((r) => {
        e.inflight = null;
        if (r.ok) {
          if (r.data.client_seq >= sent) e.ackSeq = Math.max(e.ackSeq, sent);
          e.attempts = 0;
          e.error = null;
        } else {
          e.error = r.error;
          e.attempts += 1;
          if (r.error.retryable && this.online) {
            const delay = Math.min(MAX_BACKOFF_MS, 1000 * 2 ** (e.attempts - 1));
            e.retry = setTimeout(() => {
              e.retry = null;
              void this.flush(qid);
            }, delay);
          }
        }
        this.writeStash();
        this.onChange();
        if (r.ok && e.seq > e.ackSeq) return this.flush(qid); // an edit arrived while this one was in flight
      });
    e.inflight = p;
    this.onChange();
    return p;
  }

  /** Send every unacknowledged answer now and wait until all requests have settled. */
  async flushAll(): Promise<void> {
    await Promise.all([...this.entries.keys()].map((q) => this.flush(q)));
  }

  status(qid: string): { status: SaveStatus; error: ApiErrorBody | null } | null {
    const e = this.entries.get(qid);
    if (!e || (e.seq === 0 && e.ackSeq === 0)) return null;
    if (e.inflight) return { status: "saving", error: null };
    if (e.ackSeq >= e.seq) return { status: "saved", error: null };
    if (e.error) return { status: "error", error: e.error };
    if (!this.online) return { status: "waiting", error: null };
    return { status: "pending", error: null };
  }

  /** Questions whose latest value is not acknowledged by the backend. */
  unsaved(): string[] {
    return [...this.entries].filter(([, e]) => e.ackSeq < e.seq).map(([q]) => q);
  }

  private writeStash(): void {
    try {
      const dirty: Record<string, { value: AnswerValue; seq: number }> = {};
      for (const [qid, e] of this.entries) if (e.ackSeq < e.seq) dirty[qid] = { value: e.value, seq: e.seq };
      if (Object.keys(dirty).length) window.sessionStorage.setItem(stashKey(this.sessionId), JSON.stringify(dirty));
      else window.sessionStorage.removeItem(stashKey(this.sessionId));
    } catch {
      /* storage unavailable: values stay in memory */
    }
  }
}
