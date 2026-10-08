// @ts-check
// DEMO adapter (T02) — SIMULATED class for developing and checking the panel without a class server.
// Every value here is invented by the simulation and is labelled DEMO in the UI. It is never mixed with
// real data: the adapter is chosen once at start-up and the page has to be reloaded to switch.
//
// The simulation plays BOTH sides of qorgau.class.v1 so the panel's normal code path is exercised:
//   * students send `status` every ~2 s (with jitter) and `incident` on episodes; previews every ~2 s;
//   * a student's `zone` imitates A05 zone-rule-1 (red: ≥1 high or ≥3 medium; yellow: ≥1 medium or ≥3 low);
//   * the "server" marks a silent student `connected:false` after 15 s (protocol §3.2).
// Previews are abstract drawings with a DEMO watermark, never images of people.

/** @typedef {import("../store.js").PanelStore} PanelStore */

/** Deterministic PRNG (mulberry32). @param {number} seed */
export function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const RULES = [
  { rule_id: "phone_visible", category: "phone", priority: "medium", text: "Телефон в кадре" },
  { rule_id: "phone_raised", category: "phone", priority: "high", text: "Телефон поднят к экрану" },
  { rule_id: "multiple_faces", category: "presence", priority: "high", text: "Второе лицо в кадре" },
  { rule_id: "face_missing", category: "presence", priority: "medium", text: "Лицо не в кадре" },
  { rule_id: "gaze_prolonged_down", category: "attention", priority: "low", text: "Долгий взгляд вниз" },
  { rule_id: "gaze_prolonged_side", category: "attention", priority: "low", text: "Долгий взгляд в сторону" },
  { rule_id: "environment_escape", category: "environment", priority: "medium", text: "Переход в другое окно" },
];

const RATES = { calm: 0.004, normal: 0.012, busy: 0.04 }; // episode probability per student per 500 ms tick
const HUES = [200, 160, 30, 280, 340, 100, 220, 10];

/**
 * @typedef {object} SimStudent
 * @property {string} id
 * @property {string} computer
 * @property {string} label
 * @property {"idle"|"preflight"|"calibrating"|"running"|"paused"|"finished"} exam
 * @property {"ok"|"busy"|"off"|"unknown"} camera
 * @property {boolean} online           the student app is connected (simulation truth)
 * @property {boolean} serverSaysOnline what the simulated server reports
 * @property {number} lastStatusSent
 * @property {number} nextStatusAt
 * @property {number} offlineUntil
 * @property {number} offlineSince
 * @property {Array<{incident_id:string, rule_id:string, category:string, priority:"low"|"medium"|"high", state:"open"|"closed", t_start_wall:string, duration_ms:number, explanation_ru:string, clip_available:boolean, decision: null|"confirmed"|"dismissed"|"needs_followup"}>} incidents
 * @property {number} lastEventAt
 * @property {number} hue
 * @property {boolean} phoneShown
 */

/**
 * @param {{ students?: number, seed?: number, rate?: keyof typeof RATES, now?: () => number,
 *           setTimer?: (fn: () => void, ms: number) => unknown, clearTimer?: (t: unknown) => void }} [opts]
 */
export function createDemoAdapter(opts = {}) {
  const now = opts.now ?? (() => Date.now());
  const setTimer = opts.setTimer ?? ((fn, ms) => setInterval(fn, ms));
  const clearTimer = opts.clearTimer ?? ((t) => clearInterval(/** @type {any} */ (t)));
  let rand = rng(opts.seed ?? 7);
  let count = opts.students ?? 30;
  /** @type {keyof typeof RATES} */
  let rate = opts.rate ?? "normal";
  /** @type {SimStudent[]} */
  let sims = [];
  /** @type {PanelStore|null} */
  let store = null;
  /** @type {unknown} */
  let timer = null;
  let serverDown = false;
  let failLoads = 0;
  let incSeq = 0;
  let previewsOn = true;

  /** @param {number} n */
  function build(n) {
    const t = now();
    sims = [];
    for (let i = 0; i < n; i++) {
      const num = String(i + 1).padStart(n >= 100 ? 3 : 2, "0");
      const r = rand();
      /** @type {SimStudent} */
      const s = {
        id: `demo-${num}`,
        computer: `ПК-${num}`,
        label: `Студент ${num}`,
        exam: r < 0.06 ? "preflight" : r < 0.09 ? "finished" : "running",
        camera: rand() < 0.05 ? "off" : "ok",
        online: true,
        serverSaysOnline: true,
        lastStatusSent: t - Math.floor(rand() * 2000),
        nextStatusAt: t + Math.floor(rand() * 2000),
        offlineUntil: 0,
        offlineSince: 0,
        incidents: [],
        lastEventAt: 0,
        hue: HUES[i % HUES.length] ?? 200,
        phoneShown: false,
      };
      // some history so the class does not start uniformly green
      const pre = rand();
      const k = pre < 0.55 ? 0 : pre < 0.8 ? 1 : pre < 0.93 ? 2 : 4;
      for (let j = 0; j < k; j++) addIncident(s, t - Math.floor(rand() * 20 * 60_000), true);
      sims.push(s);
    }
    // one student starts disconnected when the class is big enough to show it
    if (n >= 10) disconnect(sims[Math.floor(n / 3)], t - 20_000, 60_000);
  }

  /** @param {SimStudent} s @param {number} at @param {boolean} [closed] */
  function addIncident(s, at, closed = false) {
    const def = RULES[Math.floor(rand() * RULES.length)] ?? RULES[0];
    incSeq += 1;
    const decided = closed && rand() < 0.5;
    const inc = {
      incident_id: `demo-inc-${incSeq}`,
      rule_id: def.rule_id,
      category: def.category,
      priority: /** @type {"low"|"medium"|"high"} */ (def.priority),
      state: /** @type {"open"|"closed"} */ (closed ? "closed" : "open"),
      t_start_wall: new Date(at).toISOString(),
      duration_ms: closed ? 2000 + Math.floor(rand() * 9000) : 0,
      explanation_ru: `${def.text} (DEMO, имитация)`,
      clip_available: rand() < 0.5,
      decision: /** @type {null|"confirmed"|"dismissed"|"needs_followup"} */ (decided ? (rand() < 0.7 ? "dismissed" : "needs_followup") : null),
    };
    s.incidents.push(inc);
    s.lastEventAt = Math.max(s.lastEventAt, at);
    return inc;
  }

  /** zone like A05 zone-rule-1 (simulated source; the panel never computes this itself) @param {SimStudent} s */
  function simZone(s) {
    const by = { low: 0, medium: 0, high: 0 };
    for (const i of s.incidents) by[i.priority] += 1;
    const reasons = s.incidents
      .slice()
      .sort((a, b) => ({ high: 0, medium: 1, low: 2 })[a.priority] - ({ high: 0, medium: 1, low: 2 })[b.priority])
      .slice(0, 3)
      .map((i) => `${i.explanation_ru.replace(" (DEMO, имитация)", "")} — ${new Date(i.t_start_wall).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}`);
    if (by.high >= 1 || by.medium >= 3) return { zone: "red", reasons, by };
    if (by.medium >= 1 || by.low >= 3) return { zone: "yellow", reasons, by };
    if (s.exam !== "running" && s.exam !== "finished") return { zone: "grey", reasons: ["Экзамен ещё не начат"], by };
    return { zone: "green", reasons: ["Эпизодов нет, наблюдение полное"], by };
  }

  /** @param {SimStudent} s */
  function statusMessage(s) {
    const z = simZone(s);
    return {
      student_id: s.id,
      computer_name: s.computer,
      student_label: s.label,
      exam_state: s.exam,
      camera: s.camera,
      monitoring: s.camera === "ok" ? "ok" : "degraded",
      zone: z.zone,
      zone_reasons_ru: z.reasons,
      incidents_total: s.incidents.length,
      incidents_by_priority: z.by,
      incidents_unreviewed: s.incidents.filter((i) => i.decision === null).length,
      locked: false,
      mic_active: false,
      connected: s.serverSaysOnline,
      last_status_at: new Date(s.lastStatusSent).toISOString(),
      last_event_at: s.lastEventAt ? new Date(s.lastEventAt).toISOString() : null,
    };
  }

  /** @param {SimStudent|undefined} s @param {number} at @param {number} forMs */
  function disconnect(s, at, forMs) {
    if (!s) return;
    s.online = false;
    s.offlineSince = at;
    s.offlineUntil = at + forMs;
  }

  /** @param {SimStudent} s */
  function previewSvg(s) {
    const phone = s.phoneShown
      ? `<rect x="196" y="120" width="34" height="60" rx="5" fill="#334155"/><rect x="200" y="126" width="26" height="44" rx="2" fill="#94a3b8"/>`
      : "";
    const svg =
      `<svg xmlns="http://www.w3.org/2000/svg" width="320" height="240" viewBox="0 0 320 240">` +
      `<rect width="320" height="240" fill="hsl(${s.hue} 22% 86%)"/>` +
      `<rect y="170" width="320" height="70" fill="hsl(${s.hue} 18% 74%)"/>` +
      `<ellipse cx="160" cy="100" rx="38" ry="46" fill="hsl(${s.hue} 12% 62%)"/>` +
      `<path d="M92 240 C96 170 224 170 228 240 Z" fill="hsl(${s.hue} 14% 56%)"/>${phone}` +
      `<text x="10" y="22" font-family="sans-serif" font-size="15" font-weight="700" fill="#7c2d12">DEMO</text>` +
      `<text x="310" y="230" text-anchor="end" font-family="sans-serif" font-size="13" fill="#1f2937">${s.computer}</text></svg>`;
    return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
  }

  function step() {
    if (!store) return;
    const t = now();
    for (const s of sims) {
      // connection changes
      if (!s.online && t >= s.offlineUntil && s.offlineUntil > 0) {
        s.online = true;
        s.serverSaysOnline = true;
        s.nextStatusAt = t;
      }
      if (!s.online && s.serverSaysOnline && t - s.lastStatusSent > 15_000) {
        s.serverSaysOnline = false;
        if (!serverDown) store.studentUpdate({ student_id: s.id, connected: false });
      }
      if (!s.online) continue;
      // episodes
      if (s.exam === "running" && rand() < RATES[rate]) {
        const inc = addIncident(s, t);
        s.phoneShown = inc.category === "phone";
        if (!serverDown) store.incident({ ...inc, student_id: s.id });
        s.nextStatusAt = t; // status "при любом изменении"
      } else if (s.phoneShown && rand() < 0.1) {
        s.phoneShown = false;
      }
      // rare camera changes
      if (s.exam === "running" && rand() < 0.0008) {
        s.camera = s.camera === "ok" ? (rand() < 0.5 ? "off" : "busy") : "ok";
        s.nextStatusAt = t;
      }
      if (t >= s.nextStatusAt) {
        s.lastStatusSent = t;
        s.nextStatusAt = t + 1800 + Math.floor(rand() * 400);
        if (!serverDown) {
          store.studentUpdate(statusMessage(s));
          if (previewsOn && s.exam === "running" && s.camera === "ok") store.preview(s.id, previewSvg(s), t);
        }
      }
    }
  }

  function loadInitial() {
    if (!store) return;
    if (failLoads > 0) {
      failLoads -= 1;
      store.setConnection({ status: "error", detail: "DEMO: имитация ошибки сервера при загрузке списка (500).", retryAt: null });
      return;
    }
    store.snapshot(sims.filter((s) => s.online).map(statusMessage).concat(sims.filter((s) => !s.online).map((s) => ({ ...statusMessage(s), connected: false }))));
    for (const s of sims) {
      for (const i of s.incidents) store.incident({ ...i, student_id: s.id });
      if (s.online && s.exam === "running" && s.camera === "ok") store.preview(s.id, previewSvg(s), now());
    }
    store.setConnection({ status: "live", detail: "" });
  }

  const api = {
    kind: /** @type {const} */ ("demo"),
    label: "DEMO — имитация класса",
    /** @param {PanelStore} s */
    start(s) {
      store = s;
      build(count);
      s.setConnection({ status: "loading", detail: "DEMO: имитация загрузки списка…" });
      setTimeout(loadInitial, 350);
      timer = setTimer(step, 500);
    },
    stop() {
      if (timer) clearTimer(timer);
      timer = null;
      store = null;
    },
    retry() {
      if (!store) return;
      store.setConnection({ status: "loading", detail: "DEMO: повторная загрузка…" });
      setTimeout(loadInitial, 250);
    },
    /** @param {string} id */
    async getIncidents(id) {
      await new Promise((r) => setTimeout(r, 120));
      const s = sims.find((x) => x.id === id);
      if (!s) return { ok: /** @type {const} */ (false), error: "DEMO: студент не найден." };
      return { ok: /** @type {const} */ (true), incidents: s.incidents.map((i) => ({ ...i, student_id: id })) };
    },
    // ------------------------------------------------------------ DEMO controls (only in the DEMO panel)
    demo: {
      /** @param {number} n */
      setCount(n) {
        count = n;
        rand = rng((opts.seed ?? 7) + n);
        build(n);
        if (store) {
          store.acked.clear();
          store.incidents.clear();
          loadInitial();
        }
      },
      /** @param {keyof typeof RATES} r */
      setRate(r) {
        rate = r;
      },
      /** @param {boolean} down */
      setServerDown(down) {
        serverDown = down;
        if (!store) return;
        if (down) store.setConnection({ status: "reconnecting", detail: "DEMO: имитация потери связи панели с сервером класса.", retryAt: null, attempts: 1 });
        else loadInitial();
      },
      disconnectOne() {
        const live = sims.filter((s) => s.online && s.exam === "running");
        const s = live[Math.floor(rand() * live.length)];
        if (s) disconnect(s, now(), 45_000);
        return s?.label ?? null;
      },
      /** @param {string} id */
      burst(id) {
        const s = sims.find((x) => x.id === id) ?? sims[0];
        if (!s || !store) return;
        const inc = addIncident(s, now());
        s.phoneShown = inc.category === "phone";
        store.incident({ ...inc, student_id: s.id });
        s.nextStatusAt = now();
      },
      failNextLoad() {
        failLoads = 1;
      },
      /** @param {boolean} on */
      setPreviews(on) {
        previewsOn = on;
      },
      get state() {
        return { count, rate, serverDown, previewsOn };
      },
    },
  };
  return api;
}
