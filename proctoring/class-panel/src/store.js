// @ts-check
// Panel state (T02). Adapters push contract data in; the UI reads derived, display-ready state out.
// Updates are coalesced: many events → one flush per animation frame (or per tick in tests).
// Card ORDER is stable on purpose: it changes on a slow cadence (REORDER_MS), immediately only when the
// teacher changes sort/filter, and quickly (≥ ESCALATE_MIN_MS apart) when a student's zone rank changes.
import {
  attentionReasons,
  displayName,
  displayState,
  mergeStudent,
  naturalCompare,
  normalizeIncident,
  normalizeStudent,
  ZONE_RANK,
} from "./model.js";

/** @typedef {import("./model.js").StudentView} StudentView */
/** @typedef {import("./model.js").Zone} Zone */
/** @typedef {ReturnType<typeof import("./model.js").normalizeIncident>} Incident */
/** @typedef {"loading"|"live"|"reconnecting"|"error"|"auth"|"forbidden"} FeedStatus */
/** @typedef {{ status: FeedStatus, detail: string, since: number, retryAt: number|null, attempts: number }} Connection */
/** @typedef {"priority"|"recent"|"name"|"computer"} SortMode */

export const REORDER_MS = 4000;
export const ESCALATE_MIN_MS = 1500;
export const FLASH_MS = 2500;
const MAX_INCIDENTS_PER_STUDENT = 200;

/**
 * @typedef {object} Entry
 * @property {StudentView} view
 * @property {string[]} unknownKeys
 * @property {{ url: string, at: number|null } | null} preview
 * @property {number} flashUntil       highlight a fresh event without moving the card
 * @property {number} lastRank         zone rank used for the current order
 * @property {string} sig              last rendered signature (for change detection)
 */

export class PanelStore {
  /** @param {{ now?: () => number, schedule?: (fn: () => void) => void }} [opts] */
  constructor(opts = {}) {
    this.now = opts.now ?? (() => Date.now());
    this.schedule = opts.schedule ?? ((fn) => requestAnimationFrame(() => fn()));
    /** @type {Map<string, Entry>} */
    this.students = new Map();
    /** @type {Map<string, Map<string, NonNullable<Incident>>>} */
    this.incidents = new Map();
    /** @type {Connection} */
    this.connection = { status: "loading", detail: "", since: this.now(), retryAt: null, attempts: 0 };
    /** @type {string[]} */
    this.order = [];
    this.orderFrozen = false;
    this.lastReorderAt = 0;
    /** @type {{ zones: Set<Zone>, link: "all"|"online"|"offline", query: string, sort: SortMode }} */
    this.filter = { zones: new Set(), link: "all", query: "", sort: "priority" };
    /** teacher's "просмотрено": id → signature of the attention state that was acknowledged */
    /** @type {Map<string, string>} */
    this.acked = new Map();
    /** diagnostics about contract coverage (real mode) */
    this.diag = { unknownKeys: new Set(), droppedMessages: 0, lastSnapshotAt: /** @type {number|null} */ (null) };
    this.loaded = false;
    /** @type {Set<() => void>} */
    this.listeners = new Set();
    /** @type {{ cards: Set<string>, order: boolean, all: boolean }} */
    this.pending = { cards: new Set(), order: false, all: true };
    this.flushScheduled = false;
    this.flushCount = 0;
    this.lastFlushMs = 0;
  }

  /** @param {() => void} fn */
  subscribe(fn) {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  // ---------------------------------------------------------------- adapter sink
  /** Full list (initial load or resync). Students missing from the list are removed. @param {unknown[]} list */
  snapshot(list) {
    const now = this.now();
    const seen = new Set();
    for (const raw of list) {
      const id = this.upsert(raw, now, false);
      if (id) seen.add(id);
    }
    for (const id of [...this.students.keys()]) if (!seen.has(id)) this.students.delete(id);
    this.loaded = true;
    this.diag.lastSnapshotAt = now;
    this.rebuildOrder(true);
    this.mark({ all: true });
  }

  /** One student_update / status. @param {unknown} raw */
  studentUpdate(raw) {
    const id = this.upsert(raw, this.now(), true);
    if (!id) {
      this.diag.droppedMessages += 1;
      return;
    }
    if (!this.order.includes(id)) this.rebuildOrder(true);
    this.mark({ card: id });
  }

  /**
   * @param {unknown} raw
   * @param {number} now
   * @param {boolean} flashOnChange
   * @returns {string|null}
   */
  upsert(raw, now, flashOnChange) {
    const n = normalizeStudent(raw, now);
    if (!n) return null;
    for (const k of n.unknownKeys) this.diag.unknownKeys.add(k);
    const prev = this.students.get(n.view.id);
    const view = mergeStudent(prev?.view, n.view, /** @type {Record<string, unknown>} */ (raw));
    const zoneChanged = !!prev && prev.view.zone !== view.zone;
    const eventChanged = !!prev && view.lastEventAt !== null && view.lastEventAt !== prev.view.lastEventAt;
    /** @type {Entry} */
    const entry = prev ?? { view, unknownKeys: [], preview: null, flashUntil: 0, lastRank: 99, sig: "" };
    entry.view = view;
    entry.unknownKeys = n.unknownKeys;
    if (flashOnChange && (zoneChanged || eventChanged)) entry.flashUntil = now + FLASH_MS;
    this.students.set(view.id, entry);
    return view.id;
  }

  /** @param {unknown} raw */
  incident(raw) {
    const inc = normalizeIncident(raw);
    if (!inc || !inc.studentId) {
      this.diag.droppedMessages += 1;
      return;
    }
    let m = this.incidents.get(inc.studentId);
    if (!m) {
      m = new Map();
      this.incidents.set(inc.studentId, m);
    }
    m.set(inc.id, { ...(m.get(inc.id) ?? {}), ...inc });
    if (m.size > MAX_INCIDENTS_PER_STUDENT) m.delete(m.keys().next().value ?? "");
    const e = this.students.get(inc.studentId);
    if (e) {
      const t = inc.startAt ?? this.now();
      if (e.view.lastEventAt === null || t > e.view.lastEventAt) e.view = { ...e.view, lastEventAt: t };
      e.flashUntil = this.now() + FLASH_MS;
      this.mark({ card: inc.studentId });
    }
  }

  /** @param {string} studentId @param {string} url @param {number|null} at */
  preview(studentId, url, at) {
    const e = this.students.get(studentId);
    if (!e) return;
    e.preview = { url, at };
    this.mark({ card: studentId });
  }

  /** @param {Partial<Connection> & { status: FeedStatus }} c */
  setConnection(c) {
    const now = this.now();
    const changed = c.status !== this.connection.status;
    this.connection = {
      status: c.status,
      detail: c.detail ?? "",
      since: changed ? now : this.connection.since,
      retryAt: c.retryAt ?? null,
      attempts: c.attempts ?? (changed && c.status === "live" ? 0 : this.connection.attempts),
    };
    this.mark({ all: true });
  }

  // ---------------------------------------------------------------- derived
  get feedLive() {
    return this.connection.status === "live";
  }

  /** @param {string} id */
  derived(id) {
    const e = this.students.get(id);
    if (!e) return null;
    const now = this.now();
    const d = displayState(e.view, now, this.feedLive);
    const reasons = attentionReasons(e.view, d);
    const ackSig = this.attentionSig(e.view, reasons);
    const acked = reasons.length > 0 && this.acked.get(id) === ackSig;
    return { entry: e, d, reasons, acked, inQueue: reasons.length > 0 && !acked, flashing: e.flashUntil > now };
  }

  /** @param {StudentView} v @param {{key:string}[]} reasons */
  attentionSig(v, reasons) {
    return `${reasons.map((r) => r.key).sort().join(",")}|${v.lastEventAt ?? 0}|${v.incidentsTotal ?? 0}`;
  }

  /** Teacher marks the current attention state as seen; it returns when something new happens. @param {string} id */
  acknowledge(id) {
    const x = this.derived(id);
    if (!x || x.reasons.length === 0) return;
    this.acked.set(id, this.attentionSig(x.entry.view, x.reasons));
    this.mark({ card: id });
  }

  counters() {
    let online = 0, offline = 0, unknown = 0;
    /** @type {Record<Zone, number>} */
    const zones = { red: 0, yellow: 0, grey: 0, green: 0 };
    let queue = 0;
    for (const id of this.students.keys()) {
      const x = /** @type {NonNullable<ReturnType<PanelStore["derived"]>>} */ (this.derived(id));
      zones[x.d.zone] += 1;
      if (x.d.link === "online") online += 1;
      else if (x.d.link === "offline") offline += 1;
      else unknown += 1;
      if (x.inQueue && this.feedLive) queue += 1;
    }
    return { total: this.students.size, online, offline, unknown, zones, queue };
  }

  /** Students currently in the attention queue, most urgent first (protocol zone order, then latest event).
   *  Without a live server feed nothing is current, so the queue is empty (the header explains why). */
  queue() {
    /** @type {NonNullable<ReturnType<PanelStore["derived"]>>[]} */
    const items = [];
    if (!this.feedLive) return items;
    for (const id of this.students.keys()) {
      const x = this.derived(id);
      if (x && x.inQueue) items.push(x);
    }
    items.sort((a, b) => {
      const ra = Math.min(...a.reasons.map((r) => r.rank));
      const rb = Math.min(...b.reasons.map((r) => r.rank));
      if (ra !== rb) return ra - rb;
      return (b.entry.view.lastEventAt ?? 0) - (a.entry.view.lastEventAt ?? 0) || naturalCompare(displayName(a.entry.view), displayName(b.entry.view));
    });
    return items;
  }

  /** @param {string} id */
  matchesFilter(id) {
    const x = this.derived(id);
    if (!x) return false;
    const f = this.filter;
    if (f.zones.size > 0 && !f.zones.has(x.d.zone)) return false;
    if (f.link === "online" && x.d.link !== "online") return false;
    if (f.link === "offline" && x.d.link !== "offline") return false;
    const q = f.query.trim().toLocaleLowerCase("ru");
    if (q) {
      const v = x.entry.view;
      const hay = [v.label, v.computerName, v.id].filter(Boolean).join(" ").toLocaleLowerCase("ru");
      if (!hay.includes(q)) return false;
    }
    return true;
  }

  /** @param {string} a @param {string} b */
  compare(a, b) {
    const ea = /** @type {Entry} */ (this.students.get(a));
    const eb = /** @type {Entry} */ (this.students.get(b));
    const now = this.now();
    const byName = () => naturalCompare(displayName(ea.view), displayName(eb.view)) || naturalCompare(a, b);
    switch (this.filter.sort) {
      case "name":
        return byName();
      case "computer":
        return naturalCompare(ea.view.computerName ?? "￿", eb.view.computerName ?? "￿") || byName();
      case "recent":
        return (eb.view.lastEventAt ?? 0) - (ea.view.lastEventAt ?? 0) || byName();
      case "priority":
      default: {
        const za = ZONE_RANK[displayState(ea.view, now, this.feedLive).zone];
        const zb = ZONE_RANK[displayState(eb.view, now, this.feedLive).zone];
        return za - zb || (eb.view.lastEventAt ?? 0) - (ea.view.lastEventAt ?? 0) || byName();
      }
    }
  }

  /** @param {boolean} force */
  rebuildOrder(force) {
    const ids = [...this.students.keys()];
    if (this.orderFrozen && !force) return false;
    let next;
    if (this.orderFrozen) {
      // keep the frozen order, append newcomers, drop removed
      const keep = this.order.filter((id) => this.students.has(id));
      next = [...keep, ...ids.filter((id) => !keep.includes(id)).sort((a, b) => this.compare(a, b))];
    } else {
      next = ids.sort((a, b) => this.compare(a, b));
    }
    const changed = next.length !== this.order.length || next.some((id, i) => id !== this.order[i]);
    this.order = next;
    this.lastReorderAt = this.now();
    for (const id of next) {
      const e = this.students.get(id);
      if (e) e.lastRank = ZONE_RANK[displayState(e.view, this.lastReorderAt, this.feedLive).zone];
    }
    if (changed) this.pending.order = true;
    return changed;
  }

  /** Called on a timer: slow periodic reorder + faster reorder when a zone rank changed. */
  tick() {
    const now = this.now();
    if (this.orderFrozen || this.filter.sort !== "priority") {
      if (!this.orderFrozen && now - this.lastReorderAt >= REORDER_MS) this.rebuildOrder(false);
    } else {
      let rankChanged = false;
      for (const e of this.students.values()) {
        if (ZONE_RANK[displayState(e.view, now, this.feedLive).zone] !== e.lastRank) {
          rankChanged = true;
          break;
        }
      }
      if ((rankChanged && now - this.lastReorderAt >= ESCALATE_MIN_MS) || now - this.lastReorderAt >= REORDER_MS) this.rebuildOrder(false);
    }
    this.mark({ all: true }); // time-dependent texts ("12 с назад", staleness) — cheap, change-detected per card
  }

  /** @param {Partial<PanelStore["filter"]>} f */
  setFilter(f) {
    this.filter = { ...this.filter, ...f };
    if (f.sort !== undefined) this.rebuildOrder(true);
    this.mark({ all: true });
  }

  /** @param {boolean} frozen */
  setFrozen(frozen) {
    this.orderFrozen = frozen;
    if (!frozen) this.rebuildOrder(true);
    this.mark({ all: true });
  }

  // ---------------------------------------------------------------- change propagation
  /** @param {{ card?: string, all?: boolean }} c */
  mark(c) {
    if (c.all) this.pending.all = true;
    if (c.card) this.pending.cards.add(c.card);
    if (!this.flushScheduled) {
      this.flushScheduled = true;
      this.schedule(() => this.flush());
    }
  }

  flush() {
    this.flushScheduled = false;
    const t0 = typeof performance !== "undefined" ? performance.now() : 0;
    this.listeners.forEach((l) => l());
    this.pending = { cards: new Set(), order: false, all: false };
    this.flushCount += 1;
    if (typeof performance !== "undefined") this.lastFlushMs = performance.now() - t0;
  }

  /** @param {string} studentId */
  incidentsOf(studentId) {
    return [...(this.incidents.get(studentId)?.values() ?? [])].sort((a, b) => (b.startAt ?? 0) - (a.startAt ?? 0));
  }
}
