// Holds stream-fed state outside React so high-rate observations re-render only the components that need them.
// Two channels: "main" (incidents, health, metrics, calibration, session) and "obs" (per-frame observations).
//
// Incident merge rules (A05 stream vs A08 storage):
// * The incident BODY (timing, explanation, state) is taken from whichever source has the higher update_seq.
// * review_status and evidence_ids come from REST (A08) once REST has reported them: the stream (A05) always
//   carries review_status "pending" and evidence_ids [] and must never roll back a recorded review.
// * Session-scoped messages are accepted only for the bound session; with no session nothing is accepted.
import { useSyncExternalStore } from "react";
import type {
  ApiErrorBody,
  AttentionObservation,
  CalibrationState,
  EnvironmentObservation,
  Health,
  HealthReport,
  Incident,
  PhoneObservation,
  ReviewStatus,
  RuntimeMetrics,
  SessionInfo,
  StreamEnvelope,
} from "@contracts/qorgau-v1.generated";
import { parseClassState, type ClassState } from "./classState";

type Channel = "main" | "obs";

const RING = 48;
const ANCHOR_MAX_AGE_MS = 30_000;

interface RestMeta {
  review_status: ReviewStatus;
  evidence_ids: string[];
}

export class LiveStore {
  sessionId: string | null = null;
  private bodies = new Map<string, Incident>();
  private rest = new Map<string, RestMeta>();
  health: HealthReport | null = null;
  /** Capture transitions are HealthObservations; they need not emit a full HealthReport. */
  captureHealth: Health | null = null;
  metrics: RuntimeMetrics | null = null;
  calibration: CalibrationState | null = null;
  attention: AttentionObservation | null = null;
  phone: PhoneObservation | null = null;
  private attRing: AttentionObservation[] = [];
  private phoneRing: PhoneObservation[] = [];
  envEvents: EnvironmentObservation[] = [];
  streamError: ApiErrorBody | null = null;
  /** Class-mode state from the C2 uplink (not session-scoped; survives reset()). */
  classState: ClassState | null = null;
  helloAt: number | null = null;
  lastMessageAt: number | null = null;
  seqGaps = 0;
  /** Incremented when the stream changed an incident: REST (A08) should be re-read when allowed. */
  restStaleSeq = 0;
  private lastSeq = 0;
  private anchor: { t: number; perf: number } | null = null;
  private wallOffsets: number[] = [];
  private versions: Record<Channel, number> = { main: 0, obs: 0 };
  private listeners: Record<Channel, Set<() => void>> = { main: new Set(), obs: new Set() };
  private subscribers: Record<Channel, (l: () => void) => () => void> = {
    main: (l) => this.addListener("main", l),
    obs: (l) => this.addListener("obs", l),
  };

  /** Called with session snapshots from the stream; App merges them. */
  onSession: ((s: SessionInfo) => void) | null = null;
  /** Called when the stream lost messages (seq gap) or reconnected: REST refetch needed. */
  onResync: (() => void) | null = null;

  subscribe = (ch: Channel) => this.subscribers[ch];
  version = (ch: Channel) => this.versions[ch];

  private addListener(ch: Channel, l: () => void): () => void {
    this.listeners[ch].add(l);
    return () => {
      this.listeners[ch].delete(l);
    };
  }

  private bump(ch: Channel): void {
    this.versions[ch] += 1;
    this.listeners[ch].forEach((l) => l());
  }

  reset(sessionId: string | null): void {
    this.sessionId = sessionId;
    this.bodies.clear();
    this.rest.clear();
    this.metrics = null;
    this.captureHealth = null;
    this.calibration = null;
    this.attention = null;
    this.phone = null;
    this.attRing = [];
    this.phoneRing = [];
    this.envEvents = [];
    this.streamError = null;
    this.anchor = null;
    this.restStaleSeq = 0;
    this.bump("main");
    this.bump("obs");
  }

  // ------------------------------------------------------------------ incidents
  private merged(id: string): Incident | undefined {
    const b = this.bodies.get(id);
    if (!b) return undefined;
    const r = this.rest.get(id);
    return r ? { ...b, review_status: r.review_status, evidence_ids: r.evidence_ids } : b;
  }

  get(id: string): Incident | undefined {
    return this.merged(id);
  }

  incidents(): Incident[] {
    return [...this.bodies.keys()].map((id) => this.merged(id)!).sort((a, b) => a.t_start_ms - b.t_start_ms);
  }

  /** True when review/evidence fields of this incident come from storage (A08), not only from the stream. */
  hasRest(id: string): boolean {
    return this.rest.has(id);
  }

  private upsertBody(i: Incident): boolean {
    if (!this.sessionId || i.session_id !== this.sessionId) return false;
    const prev = this.bodies.get(i.incident_id);
    if (prev && prev.update_seq >= i.update_seq) return false; // idempotent by (incident_id, update_seq)
    this.bodies.set(i.incident_id, i);
    return true;
  }

  /** Authoritative data from REST (listIncidents/getIncident/addReview follow-up). */
  applyRest(list: Incident[]): void {
    let changed = false;
    for (const i of list) {
      if (!this.sessionId || i.session_id !== this.sessionId) continue;
      changed = this.upsertBody(i) || changed;
      const prev = this.rest.get(i.incident_id);
      if (!prev || prev.review_status !== i.review_status || prev.evidence_ids.join() !== i.evidence_ids.join()) {
        this.rest.set(i.incident_id, { review_status: i.review_status, evidence_ids: [...i.evidence_ids] });
        changed = true;
      }
    }
    if (changed) this.bump("main");
  }

  // ------------------------------------------------------------------ observations by frame
  /** Observation of this kind for exactly this frame, or the nearest older one (with its lag). */
  observationFor<K extends "attention" | "phone">(
    kind: K,
    frameId: number,
    tSessionMs: number,
  ): { obs: (K extends "attention" ? AttentionObservation : PhoneObservation) | null; exact: boolean; lagMs: number | null } {
    const ring = (kind === "attention" ? this.attRing : this.phoneRing) as Array<AttentionObservation | PhoneObservation>;
    let best: AttentionObservation | PhoneObservation | null = null;
    for (let k = ring.length - 1; k >= 0; k--) {
      const o = ring[k]!;
      if (o.frame_id === frameId) return { obs: o as never, exact: true, lagMs: 0 };
      if (o.frame_id !== null && o.frame_id < frameId && (!best || (best.frame_id ?? -1) < o.frame_id)) best = o;
    }
    return { obs: (best as never) ?? null, exact: false, lagMs: best ? tSessionMs - best.t_session_ms : null };
  }

  // ------------------------------------------------------------------ clocks
  /** Session clock estimate (ms) from the latest server sample, or null if none is recent. */
  sessionNow(): number | null {
    if (!this.anchor) return null;
    const age = performance.now() - this.anchor.perf;
    if (age > ANCHOR_MAX_AGE_MS) return null;
    return this.anchor.t + age;
  }

  /** Server wall clock estimate (ms since epoch); falls back to the local clock. */
  serverNow(): number {
    if (this.wallOffsets.length === 0) return Date.now();
    const s = [...this.wallOffsets].sort((a, b) => a - b);
    return Date.now() + s[Math.floor(s.length / 2)]!;
  }

  private sampleWall(iso: string): void {
    const t = Date.parse(iso);
    if (Number.isNaN(t)) return;
    this.wallOffsets = [...this.wallOffsets.slice(-9), t - Date.now()];
  }

  private sampleSessionT(t: number | null | undefined): void {
    if (typeof t !== "number" || !Number.isFinite(t)) return;
    const now = performance.now();
    // Keep the most advanced estimate: late/out-of-order samples never move the clock backwards.
    if (this.anchor && this.anchor.t + (now - this.anchor.perf) > t + 250) return;
    this.anchor = { t, perf: now };
  }

  // ------------------------------------------------------------------ stream
  ingest(env: StreamEnvelope): void {
    this.lastMessageAt = Date.now();
    this.sampleWall(env.sent_at);
    const m = env.message;
    if (m.type === "hello") {
      this.lastSeq = env.seq;
      const reconnect = this.helloAt !== null;
      this.helloAt = Date.now();
      this.streamError = null;
      this.bump("main");
      if (reconnect) this.onResync?.();
      return;
    }
    const isClassState = (m as { type: string }).type === "class_state";
    if (isClassState && (!Number.isSafeInteger(env.seq) || env.seq <= this.lastSeq)) return;
    if (this.lastSeq > 0 && env.seq > this.lastSeq + 1) {
      this.seqGaps += 1;
      this.onResync?.();
    }
    this.lastSeq = env.seq;
    if (isClassState) {
      const cs = parseClassState(m);
      if (cs) {
        this.classState = cs;
        this.bump("main");
      }
      return;
    }
    const mine = (sid: string | null | undefined) => !!this.sessionId && sid === this.sessionId;
    switch (m.type) {
      case "session_state":
        if (!mine(m.session.session_id)) return;
        this.onSession?.(m.session);
        return;
      case "incident":
        if (this.upsertBody(m.change.incident)) {
          this.restStaleSeq += 1;
          this.bump("main");
        }
        return;
      case "health":
        this.health = m.report;
        this.sampleWall(m.report.server_time);
        this.bump("main");
        return;
      case "metrics":
        if (!mine(m.metrics.session_id)) return;
        this.metrics = m.metrics;
        this.sampleSessionT(m.metrics.t_session_ms);
        this.bump("main");
        return;
      case "calibration":
        if (!mine(m.session_id)) return;
        this.calibration = m.calibration;
        this.bump("main");
        return;
      case "observation": {
        const o = m.observation;
        if (!mine(o.session_id)) return;
        this.sampleSessionT(o.t_session_ms);
        if (o.kind === "attention") {
          this.attention = o;
          this.attRing = [...this.attRing.slice(-(RING - 1)), o];
        } else if (o.kind === "phone") {
          this.phone = o;
          this.phoneRing = [...this.phoneRing.slice(-(RING - 1)), o];
        } else if (o.kind === "environment") {
          this.envEvents = [...this.envEvents.slice(-49), o];
          this.bump("main");
        } else if (o.kind === "health" && o.health.component === "capture") {
          this.captureHealth = o.health;
          this.bump("main");
        }
        this.bump("obs");
        return;
      }
      case "error":
        this.streamError = m.error;
        this.bump("main");
        return;
    }
  }
}

export function useLive(store: LiveStore, ch: Channel = "main"): number {
  return useSyncExternalStore(store.subscribe(ch), () => store.version(ch));
}
