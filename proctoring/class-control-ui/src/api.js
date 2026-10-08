// @ts-check
// HTTP client for /api/teacher/control/* (T04 router) and the DEV simulator endpoints (/sim/*).
// Every call resolves (never throws) to
//   {ok: true, status, data}                        — HTTP 2xx
//   {ok: false, status, error, network: false}      — server answered with an error
//   {ok: false, status: 0, error, network: true}    — no answer (the request may or may not have arrived)
// Teacher identity: the real class server uses its PIN cookie (same origin, credentials included);
// the DEV server reads X-Qorgau-Dev-Teacher, set here only when a DEV teacher is chosen.
import { API_BASE, normalizeError } from "./model.js";

/**
 * @param {{ base?: string, fetchImpl?: typeof fetch, getTeacher?: () => string|null, timeoutMs?: number }} [o]
 */
export function createApi(o = {}) {
  const base = o.base ?? API_BASE;
  const fetchImpl = o.fetchImpl ?? ((/** @type {any} */ url, /** @type {any} */ init) => fetch(url, init));
  const getTeacher = o.getTeacher ?? (() => null);
  const timeoutMs = o.timeoutMs ?? 8000;

  /**
   * @param {string} method @param {string} url @param {unknown} [body]
   */
  async function raw(method, url, body) {
    /** @type {Record<string, string>} */
    const headers = { Accept: "application/json" };
    const teacher = getTeacher();
    if (teacher) headers["X-Qorgau-Dev-Teacher"] = teacher;
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const ctrl = typeof AbortController === "function" ? new AbortController() : null;
    const timer = ctrl ? setTimeout(() => ctrl.abort(), timeoutMs) : null;
    let res;
    try {
      res = await fetchImpl(url, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        credentials: "same-origin",
        cache: "no-store",
        signal: ctrl ? ctrl.signal : undefined,
      });
    } catch {
      return { ok: false, status: 0, network: true, error: { status: 0, code: "network", message: "Нет связи с сервером класса — ответ не получен", field: null, details: {} } };
    } finally {
      if (timer) clearTimeout(timer);
    }
    let data = null;
    try {
      const text = await res.text();
      data = text ? JSON.parse(text) : null;
    } catch {
      data = null;
    }
    if (res.ok) return { ok: true, status: res.status, data };
    return { ok: false, status: res.status, network: false, error: normalizeError(res.status, data) };
  }

  const enc = encodeURIComponent;
  /** @param {Record<string, string|number|null|undefined>} q */
  const qs = (q) => {
    const parts = Object.entries(q).filter(([, v]) => v !== null && v !== undefined && v !== "").map(([k, v]) => `${enc(k)}=${enc(String(v))}`);
    return parts.length ? `?${parts.join("&")}` : "";
  };

  return {
    raw,
    meta: () => raw("GET", `${base}/meta`),
    exams: () => raw("GET", `${base}/exams`),
    exam: (/** @type {string} */ id) => raw("GET", `${base}/exams/${enc(id)}`),
    createExam: (/** @type {unknown} */ body) => raw("POST", `${base}/exams`, body),
    updateExam: (/** @type {string} */ id, /** @type {unknown} */ body) => raw("PATCH", `${base}/exams/${enc(id)}`, body),
    createPolicy: (/** @type {string} */ id, /** @type {unknown} */ body) => raw("POST", `${base}/exams/${enc(id)}/policies`, body),
    updatePolicy: (/** @type {string} */ id, /** @type {string} */ pid, /** @type {unknown} */ body) =>
      raw("PATCH", `${base}/exams/${enc(id)}/policies/${enc(pid)}`, body),
    assign: (/** @type {string} */ id, /** @type {unknown} */ body) => raw("POST", `${base}/exams/${enc(id)}/assignments`, body),
    students: (/** @type {string} */ id) => raw("GET", `${base}/exams/${enc(id)}/students`),
    sendCommand: (/** @type {string} */ id, /** @type {unknown} */ body) => raw("POST", `${base}/exams/${enc(id)}/commands`, body),
    commands: (/** @type {string} */ id, /** @type {string|null} */ studentId) => raw("GET", `${base}/exams/${enc(id)}/commands${qs({ student_id: studentId })}`),
    cancel: (/** @type {string} */ id, /** @type {string} */ cid) => raw("POST", `${base}/exams/${enc(id)}/commands/${enc(cid)}/cancel`, {}),
    journal: (/** @type {string} */ id, /** @type {{student_id?: string|null, limit?: number}} */ q = {}) =>
      raw("GET", `${base}/exams/${enc(id)}/journal${qs({ student_id: q.student_id ?? null, limit: q.limit ?? 200 })}`),
    // DEV simulator (exists only on proctor_classctl.devserver)
    simInfo: () => raw("GET", "/sim/info"),
    simBehaviour: (/** @type {string} */ sid, /** @type {string} */ behaviour) => raw("POST", `/sim/students/${enc(sid)}/behaviour`, { behaviour }),
    simOnline: (/** @type {string} */ sid, /** @type {boolean} */ online) => raw("POST", `/sim/students/${enc(sid)}/online`, { online }),
  };
}

/** @typedef {ReturnType<typeof createApi>} Api */
