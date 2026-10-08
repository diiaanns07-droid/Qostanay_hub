// @ts-check
// Qorgau Class panel (T02) — data model and contract normalisation.
//
// Contract: proctoring/contracts/class/PROTOCOL_v1.md (qorgau.class.v1, frozen 2026-10-08, baseline 65c8c17).
// The panel NEVER computes a risk score. The zone comes from the data:
//   * the student reports `zone` from A05 assess_session_zone (zone-rule-1);
//   * the server sets "grey" when there is no `status` for > 10 s or `camera` != "ok" (protocol §4).
// Grey means "not enough data", never "low risk". An episode is something to CHECK, never a confirmed
// violation; the teacher decides.
//
// Fields marked [C1-request] are not defined by qorgau.class.v1 for the teacher API; the panel reads them
// when the server sends them and otherwise shows "нет данных" (see HANDOFF.md).

/** @typedef {"red"|"yellow"|"grey"|"green"} Zone */
/** @typedef {"idle"|"preflight"|"calibrating"|"running"|"paused"|"finished"} ExamState */
/** @typedef {"ok"|"busy"|"off"|"unknown"} Camera */

/**
 * Normalised student card. `null` = not received / not determined (never a default "ok").
 * @typedef {object} StudentView
 * @property {string} id
 * @property {string|null} computerName
 * @property {string|null} label
 * @property {ExamState|null} examState
 * @property {Camera|null} camera
 * @property {"ok"|"degraded"|null} monitoring
 * @property {Zone|null} zone           zone as delivered (null = nothing delivered yet)
 * @property {string[]} zoneReasons      ≤ 3 Russian reasons from the source
 * @property {number|null} incidentsTotal
 * @property {{low:number, medium:number, high:number}|null} byPriority
 * @property {number|null} unreviewed    [C1-request] episodes without a teacher decision
 * @property {boolean|null} locked       only from data, never assumed
 * @property {boolean|null} micActive
 * @property {boolean|null} connected    [C1-request] server-side connection state
 * @property {number|null} lastStatusAt  ms epoch of the last status (server field [C1-request] or local receipt)
 * @property {number|null} lastEventAt   ms epoch of the last episode/event
 * @property {"server"|"panel"|null} connectedSource  who decided `connected`
 */

export const ZONES = /** @type {const} */ (["red", "yellow", "grey", "green"]);

/** Protocol §4 order: red → yellow → grey → green. */
export const ZONE_RANK = { red: 0, yellow: 1, grey: 2, green: 3 };

/** Labels of A05 zone-rule-1 (shared wording). Colour is ALWAYS duplicated by text and icon. */
export const ZONE_LABEL = {
  red: "Проверить в первую очередь",
  yellow: "Требует внимания",
  grey: "Недостаточно данных",
  green: "Без замечаний",
};

export const ZONE_SHORT = { red: "Красный", yellow: "Оранжевый", grey: "Серый", green: "Зелёный" };

export const EXAM_STATE_LABEL = {
  idle: "Ожидает",
  preflight: "Проверка перед экзаменом",
  calibrating: "Калибровка",
  running: "Идёт экзамен",
  paused: "Пауза",
  finished: "Завершил",
};

export const CAMERA_LABEL = { ok: "Камера работает", busy: "Камера занята", off: "Камера выключена", unknown: "Камера: неизвестно" };

/** Protocol: status every 2 s; server marks grey after 10 s without status; ping/pong loss = 15 s. */
export const STATUS_STALE_MS = 10_000;
export const NO_LINK_MS = 15_000;

const EXAM_STATES = ["idle", "preflight", "calibrating", "running", "paused", "finished"];
const CAMERAS = ["ok", "busy", "off", "unknown"];

/** @param {unknown} v */
const str = (v) => (typeof v === "string" && v.length > 0 && v.length <= 200 ? v : null);
/** @param {unknown} v */
const bool = (v) => (typeof v === "boolean" ? v : null);
/** @param {unknown} v */
const count = (v) => (typeof v === "number" && Number.isInteger(v) && v >= 0 && v < 1e6 ? v : null);
/** @param {unknown} v @param {readonly string[]} allowed */
const oneOf = (v, allowed) => (typeof v === "string" && allowed.includes(v) ? v : null);
/** @param {unknown} v */
function time(v) {
  if (typeof v === "number" && Number.isFinite(v) && v > 0) return v;
  if (typeof v === "string") {
    const t = Date.parse(v);
    return Number.isNaN(t) ? null : t;
  }
  return null;
}

/**
 * Read a student card from server data. Accepts the qorgau.class.v1 `status` field names (snake_case),
 * the identity fields of `hello`, and the [C1-request] extras. Anything else is ignored and reported.
 * @param {unknown} raw
 * @param {number} receivedAt local receipt time (ms) — used only when the server sends no timestamp
 * @returns {{ view: StudentView, unknownKeys: string[] } | null}
 */
export function normalizeStudent(raw, receivedAt) {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  const r = /** @type {Record<string, unknown>} */ (raw);
  const id = str(r.student_id) ?? str(r.id);
  if (!id) return null;
  const known = new Set([
    "student_id", "id", "computer_name", "student_label", "exam_state", "camera", "monitoring", "zone", "zone_reasons_ru",
    "incidents_total", "incidents_by_priority", "locked", "mic_active", "connected", "last_status_at", "last_event_at",
    "incidents_unreviewed", "app_version", "type", "v", "msg_id", "sent_at",
  ]);
  const unknownKeys = Object.keys(r).filter((k) => !known.has(k));
  const reasons = Array.isArray(r.zone_reasons_ru) ? r.zone_reasons_ru.filter((x) => typeof x === "string").slice(0, 3).map((x) => x.slice(0, 200)) : [];
  let byPriority = null;
  if (typeof r.incidents_by_priority === "object" && r.incidents_by_priority !== null) {
    const p = /** @type {Record<string, unknown>} */ (r.incidents_by_priority);
    const low = count(p.low), medium = count(p.medium), high = count(p.high);
    if (low !== null && medium !== null && high !== null) byPriority = { low, medium, high };
  }
  const serverConnected = bool(r.connected);
  const lastStatusAt = time(r.last_status_at) ?? (r.exam_state !== undefined || r.zone !== undefined ? receivedAt : null);
  /** @type {StudentView} */
  const view = {
    id,
    computerName: str(r.computer_name),
    label: str(r.student_label),
    examState: /** @type {ExamState|null} */ (oneOf(r.exam_state, EXAM_STATES)),
    camera: /** @type {Camera|null} */ (oneOf(r.camera, CAMERAS)),
    monitoring: /** @type {"ok"|"degraded"|null} */ (oneOf(r.monitoring, ["ok", "degraded"])),
    zone: /** @type {Zone|null} */ (oneOf(r.zone, ZONES)),
    zoneReasons: reasons,
    incidentsTotal: count(r.incidents_total),
    byPriority,
    unreviewed: count(r.incidents_unreviewed),
    locked: bool(r.locked),
    micActive: bool(r.mic_active),
    connected: serverConnected,
    connectedSource: serverConnected === null ? null : "server",
    lastStatusAt,
    lastEventAt: time(r.last_event_at),
  };
  return { view, unknownKeys };
}

/**
 * Merge a partial update into the previous view: only fields present in the new data replace old ones.
 * @param {StudentView|undefined} prev
 * @param {StudentView} next
 * @param {Record<string, unknown>} raw
 * @returns {StudentView}
 */
export function mergeStudent(prev, next, raw) {
  if (!prev) return next;
  /** @type {StudentView} */
  const out = { ...prev };
  const has = (/** @type {string} */ k) => Object.prototype.hasOwnProperty.call(raw, k);
  if (has("computer_name")) out.computerName = next.computerName;
  if (has("student_label")) out.label = next.label;
  if (has("exam_state")) out.examState = next.examState;
  if (has("camera")) out.camera = next.camera;
  if (has("monitoring")) out.monitoring = next.monitoring;
  if (has("zone")) out.zone = next.zone;
  if (has("zone_reasons_ru")) out.zoneReasons = next.zoneReasons;
  if (has("incidents_total")) out.incidentsTotal = next.incidentsTotal;
  if (has("incidents_by_priority")) out.byPriority = next.byPriority;
  if (has("incidents_unreviewed")) out.unreviewed = next.unreviewed;
  if (has("locked")) out.locked = next.locked;
  if (has("mic_active")) out.micActive = next.micActive;
  if (has("connected")) {
    out.connected = next.connected;
    out.connectedSource = next.connectedSource;
  }
  if (next.lastStatusAt !== null) out.lastStatusAt = Math.max(prev.lastStatusAt ?? 0, next.lastStatusAt);
  if (next.lastEventAt !== null) out.lastEventAt = Math.max(prev.lastEventAt ?? 0, next.lastEventAt);
  return out;
}

/**
 * What the panel DISPLAYS for a student at `now`. The zone is the delivered zone; the only adjustment is the
 * protocol rule that missing/old data is grey (the server applies it too — repeating it here keeps the screen
 * honest when the server or the teacher connection itself is lost).
 * @param {StudentView} s
 * @param {number} now
 * @param {boolean} feedLive false while the panel has no connection to the server
 */
export function displayState(s, now, feedLive) {
  const ageMs = s.lastStatusAt === null ? null : Math.max(0, now - s.lastStatusAt);
  /** @type {"online"|"offline"|"unknown"} */
  let link;
  if (!feedLive) link = "unknown";
  else if (s.connected !== null) link = s.connected ? "online" : "offline";
  else if (ageMs === null) link = "unknown";
  else link = ageMs > NO_LINK_MS ? "offline" : "online";
  // no link = no current data, even if the last status was recent (the server decides `connected`)
  const stale = !feedLive || ageMs === null || ageMs > STATUS_STALE_MS || link === "offline";
  /** @type {Zone} */
  let zone = s.zone ?? "grey";
  /** @type {string[]} */
  const greyWhy = [];
  if (s.zone === null) greyWhy.push("Зона ещё не получена");
  if (stale) {
    if (!feedLive) greyWhy.push("Нет связи панели с сервером");
    else if (ageMs === null) greyWhy.push("Статус не получен");
    else if (link === "offline" && ageMs <= STATUS_STALE_MS) greyWhy.push("Сервер сообщил: нет связи");
    else greyWhy.push(`Нет статуса ${Math.round(ageMs / 1000)} с`);
  }
  if (s.camera !== null && s.camera !== "ok") greyWhy.push(CAMERA_LABEL[s.camera]);
  if (s.camera === null && s.zone !== null && !stale) greyWhy.push("Состояние камеры не получено");
  if (stale || (s.camera !== null && s.camera !== "ok")) zone = "grey";
  const reasons = zone === "grey" ? [...greyWhy, ...(s.zone === "grey" ? s.zoneReasons : [])].slice(0, 3) : s.zoneReasons;
  return { zone, reasons, link, ageMs, stale };
}

/**
 * Why a student is in the attention queue (empty = not in the queue). Rules use only delivered data:
 * the zone (red/yellow from the source), no link during an exam, a camera that is not working.
 * @param {StudentView} s
 * @param {ReturnType<typeof displayState>} d
 * @returns {{ key: string, text: string, rank: number }[]}
 */
export function attentionReasons(s, d) {
  /** @type {{ key: string, text: string, rank: number }[]} */
  const out = [];
  if (d.zone === "red") out.push({ key: "zone-red", text: ZONE_LABEL.red, rank: 0 });
  if (d.zone === "yellow") out.push({ key: "zone-yellow", text: ZONE_LABEL.yellow, rank: 1 });
  const inExam = s.examState === "running" || s.examState === "paused" || s.examState === "calibrating";
  if (d.link === "offline" && (inExam || s.examState === null)) out.push({ key: "link", text: "Нет связи со студентом", rank: 2 });
  if (s.camera !== null && s.camera !== "ok" && inExam) out.push({ key: "camera", text: CAMERA_LABEL[s.camera], rank: 2 });
  if (d.zone === "grey" && inExam && d.link !== "offline" && !(s.camera !== null && s.camera !== "ok")) {
    out.push({ key: "grey", text: ZONE_LABEL.grey, rank: 2 });
  }
  return out;
}

/**
 * Normalise an episode (`incident`, protocol §3.1). Unknown/missing fields stay null.
 * @param {unknown} raw
 */
export function normalizeIncident(raw) {
  if (typeof raw !== "object" || raw === null) return null;
  const r = /** @type {Record<string, unknown>} */ (raw);
  const id = str(r.incident_id);
  if (!id) return null;
  return {
    id,
    studentId: str(r.student_id),
    rule: str(r.rule_id),
    category: str(r.category),
    priority: /** @type {"low"|"medium"|"high"|null} */ (oneOf(r.priority, ["low", "medium", "high"])),
    state: /** @type {"open"|"closed"|null} */ (oneOf(r.state, ["open", "closed"])),
    startAt: time(r.t_start_wall),
    durationMs: typeof r.duration_ms === "number" && r.duration_ms >= 0 ? r.duration_ms : null,
    explanation: typeof r.explanation_ru === "string" ? r.explanation_ru.slice(0, 500) : null,
    clipAvailable: bool(r.clip_available),
    decision: /** @type {"confirmed"|"dismissed"|"needs_followup"|null} */ (oneOf(r.decision, ["confirmed", "dismissed", "needs_followup"])),
  };
}

export const PRIORITY_LABEL = { high: "высокий приоритет проверки", medium: "средний приоритет проверки", low: "низкий приоритет проверки" };
export const DECISION_LABEL = { confirmed: "Преподаватель подтвердил", dismissed: "Отклонено преподавателем", needs_followup: "Нужна дополнительная проверка" };

/** "12 с назад", "3 мин назад", "—" */
export function ago(ms, now) {
  if (ms === null) return "—";
  const s = Math.max(0, Math.round((now - ms) / 1000));
  if (s < 5) return "только что";
  if (s < 60) return `${s} с назад`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m} мин назад`;
  return `${Math.round(m / 60)} ч назад`;
}

/** @param {StudentView} s */
export function displayName(s) {
  return s.label ?? s.computerName ?? `Студент ${s.id.slice(0, 8)}`;
}

/** Natural sort key for computer names like "PC-7" < "PC-12". @param {string} a @param {string} b */
export function naturalCompare(a, b) {
  return a.localeCompare(b, "ru", { numeric: true, sensitivity: "base" });
}
