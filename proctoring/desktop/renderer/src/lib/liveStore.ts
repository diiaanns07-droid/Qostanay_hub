// Holds stream-fed state outside React so high-rate observations re-render only the components that need them.
// Two channels: "main" (incidents, health, metrics, calibration, session) and "obs" (per-frame observations).
import { useSyncExternalStore } from "react";
import type {
  ApiErrorBody,
  AttentionObservation,
  CalibrationState,
  EnvironmentObservation,
  HealthReport,
  Incident,
  PhoneObservation,
  RuntimeMetrics,
  SessionInfo,
  StreamEnvelope,
} from "@contracts/qorgau-v1.generated";

type Channel = "main" | "obs";

export class LiveStore {
  sessionId: string | null = null;
  incidents = new Map<string, Incident>();
  health: HealthReport | null = null;
  metrics: RuntimeMetrics | null = null;
  calibration: CalibrationState | null = null;
  attention: AttentionObservation | null = null;
  phone: PhoneObservation | null = null;
  envEvents: EnvironmentObservation[] = [];
  streamError: ApiErrorBody | null = null;
  helloAt: number | null = null;
  lastMessageAt: number | null = null;
  seqGaps = 0;
  private lastSeq = 0;
  private versions: Record<Channel, number> = { main: 0, obs: 0 };
  private listeners: Record<Channel, Set<() => void>> = { main: new Set(), obs: new Set() };

  /** Called with session snapshots from the stream; App merges them. */
  onSession: ((s: SessionInfo) => void) | null = null;
  /** Called when the stream lost messages (seq gap) or reconnected: REST refetch needed. */
  onResync: (() => void) | null = null;

  private subscribers: Record<Channel, (l: () => void) => () => void> = {
    main: (l) => this.addListener("main", l),
    obs: (l) => this.addListener("obs", l),
  };
  subscribe = (ch: Channel) => this.subscribers[ch];
  private addListener(ch: Channel, l: () => void): () => void {
    this.listeners[ch].add(l);
    return () => {
      this.listeners[ch].delete(l);
    };
  }
  version = (ch: Channel) => this.versions[ch];

  private bump(ch: Channel): void {
    this.versions[ch] += 1;
    this.listeners[ch].forEach((l) => l());
  }

  reset(sessionId: string | null): void {
    this.sessionId = sessionId;
    this.incidents.clear();
    this.metrics = null;
    this.calibration = null;
    this.attention = null;
    this.phone = null;
    this.envEvents = [];
    this.streamError = null;
    this.bump("main");
    this.bump("obs");
  }

  replaceIncidents(list: Incident[]): void {
    for (const i of list) this.upsertIncident(i);
    this.bump("main");
  }

  private upsertIncident(i: Incident): boolean {
    if (this.sessionId && i.session_id !== this.sessionId) return false;
    const prev = this.incidents.get(i.incident_id);
    if (prev && prev.update_seq >= i.update_seq) return false; // idempotent by (incident_id, update_seq)
    this.incidents.set(i.incident_id, i);
    return true;
  }

  ingest(env: StreamEnvelope): void {
    this.lastMessageAt = Date.now();
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
    if (this.lastSeq > 0 && env.seq > this.lastSeq + 1) {
      this.seqGaps += 1;
      this.onResync?.();
    }
    this.lastSeq = env.seq;
    const forOther = (sid: string | null | undefined) => !!this.sessionId && !!sid && sid !== this.sessionId;
    switch (m.type) {
      case "session_state":
        if (forOther(m.session.session_id)) return;
        this.onSession?.(m.session);
        return;
      case "incident":
        if (this.upsertIncident(m.change.incident)) this.bump("main");
        return;
      case "health":
        this.health = m.report;
        this.bump("main");
        return;
      case "metrics":
        if (forOther(m.metrics.session_id)) return;
        this.metrics = m.metrics;
        this.bump("main");
        return;
      case "calibration":
        if (forOther(m.session_id)) return;
        this.calibration = m.calibration;
        this.bump("main");
        return;
      case "observation": {
        const o = m.observation;
        if (forOther(o.session_id)) return;
        if (o.kind === "attention") this.attention = o;
        else if (o.kind === "phone") this.phone = o;
        else if (o.kind === "environment") {
          this.envEvents = [...this.envEvents.slice(-49), o];
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
