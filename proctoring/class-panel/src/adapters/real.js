// @ts-check
// REAL adapter (T02): talks ONLY to the teacher endpoints of qorgau.class.v1 (PROTOCOL_v1.md §5), same origin:
//   GET  /api/teacher/students                 initial list + resync after reconnect
//   GET  /api/teacher/students/{id}/incidents  episodes of one student (opened card)
//   WS   /ws/teacher                           student_update | incident | preview | ack
// Teacher auth is a cookie set by the server (protocol §2.6); the panel never sends a PIN or token itself.
// Nothing is invented: a response that does not look like the expected shape is reported, not guessed.

const BACKOFF_MS = [1000, 2000, 4000, 8000, 15000];
const MAX_MSG_BYTES = 256 * 1024; // protocol §3
const B64_RE = /^[A-Za-z0-9+/]+={0,2}$/;
const MAX_PREVIEW_B64 = Math.ceil((30 * 1024 * 4) / 3) + 8; // ≤ 30 KB JPEG (protocol §3.1)

/** @typedef {import("../store.js").PanelStore} PanelStore */

/**
 * @param {{ base?: string, fetchImpl?: typeof fetch, WebSocketImpl?: typeof WebSocket }} [opts]
 */
export function createRealAdapter(opts = {}) {
  const base = opts.base ?? "";
  const f = opts.fetchImpl ?? ((...a) => fetch(...a));
  const WS = opts.WebSocketImpl ?? WebSocket;
  /** @type {PanelStore|null} */
  let store = null;
  /** @type {WebSocket|null} */
  let ws = null;
  let attempts = 0;
  /** @type {ReturnType<typeof setTimeout>|null} */
  let retryTimer = null;
  /** @type {ReturnType<typeof setTimeout>|null} */
  let historyTimer = null;
  let historyLoading = false;
  let historyDirty = false;
  let stopped = false;

  function refreshHistoryMetadata() {
    historyDirty = true;
    if (stopped || historyTimer || historyLoading) return;
    historyTimer = setTimeout(async () => {
      historyTimer = null;
      if (stopped) return;
      historyDirty = false;
      historyLoading = true;
      try { await loadSnapshot(); }
      finally {
        historyLoading = false;
        if (historyDirty && !stopped) refreshHistoryMetadata();
      }
    }, 300);
  }

  /** @param {Response} r */
  function httpProblem(r) {
    if (r.status === 401) {
      return {
        status: /** @type {const} */ ("auth"),
        detail:
          "Нужен вход преподавателя. Введите PIN из окна сервера, чтобы продолжить.",
      };
    }
    if (r.status === 403) {
      return { status: /** @type {const} */ ("forbidden"), detail: "Сервер класса разрешает панель только на компьютере преподавателя (127.0.0.1)." };
    }
    return { status: /** @type {const} */ ("error"), detail: `Сервер класса ответил ${r.status}.` };
  }

  async function loadSnapshot() {
    if (!store || stopped) return false;
    let r;
    try {
      r = await f(`${base}/api/teacher/students`, { credentials: "same-origin", headers: { Accept: "application/json" } });
    } catch {
      scheduleRetry("Сервер класса недоступен.");
      return false;
    }
    if (!r.ok) {
      const p = httpProblem(r);
      if (p.status === "error") scheduleRetry(p.detail);
      else store.setConnection({ status: p.status, detail: p.detail });
      return false;
    }
    let body;
    try {
      body = await r.json();
    } catch {
      scheduleRetry("Ответ сервера класса — не JSON.");
      return false;
    }
    const list = Array.isArray(body) ? body : Array.isArray(body?.students) ? body.students : null;
    if (!list) {
      store.setConnection({ status: "error", detail: "Не удалось прочитать список студентов. Проверьте, что сервер и приложения обновлены." });
      return false;
    }
    if (stopped) return false;
    store.snapshot(list);
    return true;
  }

  function openStream() {
    if (!store || stopped) return;
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const url = base ? base.replace(/^http/, "ws") + "/ws/teacher" : `${proto}//${location.host}/ws/teacher`;
    let socket;
    try {
      socket = new WS(url);
    } catch {
      scheduleRetry("Не удалось открыть поток событий.");
      return;
    }
    ws = socket;
    let opened = false;
    socket.onopen = async () => {
      opened = true;
      // resync after (re)connect: the list may have changed while the stream was down
      const ok = await loadSnapshot();
      if (ok && ws === socket) {
        attempts = 0;
        store?.setConnection({ status: "live", detail: "" });
      }
    };
    socket.onmessage = (ev) => {
      if (typeof ev.data !== "string" || ev.data.length > MAX_MSG_BYTES) {
        if (store) store.diag.droppedMessages += 1;
        return;
      }
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch {
        if (store) store.diag.droppedMessages += 1;
        return;
      }
      handle(msg);
    };
    socket.onclose = (ev) => {
      if (ws !== socket || stopped) return;
      ws = null;
      if (ev.code === 4401 || ev.code === 1008) {
        store?.setConnection({ status: "auth", detail: "Сервер класса закрыл поток: нужен вход преподавателя." });
        return;
      }
      scheduleRetry(opened ? "Поток событий прервался." : "Поток событий недоступен.");
    };
    socket.onerror = () => {
      /* onclose follows */
    };
  }

  /** @param {any} msg */
  function handle(msg) {
    if (!store || typeof msg !== "object" || msg === null) return;
    switch (msg.type) {
      case "student_update": {
        const s = msg.student && typeof msg.student === "object" ? msg.student : msg;
        store.studentUpdate(s);
        return;
      }
      case "incident": {
        const body = msg.incident && typeof msg.incident === "object" ? { ...msg.incident, student_id: msg.incident.student_id ?? msg.student_id } : msg;
        store.incident(body);
        return;
      }
      case "preview": {
        const id = typeof msg.student_id === "string" ? msg.student_id : null;
        const b64 = typeof msg.jpeg_b64 === "string" ? msg.jpeg_b64 : null;
        if (!id || !b64 || b64.length > MAX_PREVIEW_B64 || !B64_RE.test(b64)) {
          store.diag.droppedMessages += 1;
          return;
        }
        const at = typeof msg.frame_wall === "string" ? Date.parse(msg.frame_wall) : NaN;
        store.preview(id, `data:image/jpeg;base64,${b64}`, Number.isNaN(at) ? null : at, msg.origin);
        return;
      }
      case "ack":
        return; // commands are not part of T02; a commands module consumes acks (extension point)
      case "feature_event":
        // Review decisions/clip state alter metadata, not the immutable detector evidence.
        if (msg.feature === "history" && msg.event === "history.changed") refreshHistoryMetadata();
        return;
      default:
        // protocol §3: unknown types are ignored, the connection stays open
        store.diag.droppedMessages += 1;
    }
  }

  /** @param {string} detail */
  function scheduleRetry(detail) {
    if (!store || stopped) return;
    const delay = BACKOFF_MS[Math.min(attempts, BACKOFF_MS.length - 1)] ?? 15000;
    attempts += 1;
    store.setConnection({ status: store.loaded ? "reconnecting" : "error", detail, retryAt: Date.now() + delay, attempts });
    if (retryTimer) clearTimeout(retryTimer);
    retryTimer = setTimeout(() => {
      retryTimer = null;
      connect();
    }, delay);
  }

  function connect() {
    if (stopped) return;
    if (ws) {
      try {
        ws.close();
      } catch {
        /* ignore */
      }
      ws = null;
    }
    openStream();
  }

  return {
    kind: /** @type {const} */ ("real"),
    label: "Сервер класса",
    /** @param {PanelStore} s */
    start(s) {
      store = s;
      stopped = false;
      s.setConnection({ status: "loading", detail: "Подключаемся к серверу класса…" });
      connect();
    },
    stop() {
      stopped = true;
      if (retryTimer) clearTimeout(retryTimer);
      if (historyTimer) clearTimeout(historyTimer);
      historyTimer = null;
      historyDirty = false;
      if (ws) ws.close();
      ws = null;
    },
    retry() {
      attempts = 0;
      if (retryTimer) clearTimeout(retryTimer);
      retryTimer = null;
      connect();
    },
    /** @param {string} id */
    async getIncidents(id) {
      let r;
      try {
        r = await f(`${base}/api/teacher/students/${encodeURIComponent(id)}/incidents`, { credentials: "same-origin" });
      } catch {
        return { ok: /** @type {const} */ (false), error: "Сервер класса недоступен." };
      }
      if (!r.ok) return { ok: /** @type {const} */ (false), error: httpProblem(r).detail };
      try {
        const body = await r.json();
        const list = Array.isArray(body) ? body : Array.isArray(body?.incidents) ? body.incidents : null;
        if (!list) return { ok: /** @type {const} */ (false), error: "Формат списка эпизодов не совпадает с ожидаемым." };
        return { ok: /** @type {const} */ (true), incidents: list };
      } catch {
        return { ok: /** @type {const} */ (false), error: "Ответ сервера класса — не JSON." };
      }
    },
  };
}
