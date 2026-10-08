// @ts-check
// T03 — episode history, clip player and teacher decisions for ONE student (qorgau.class.v1).
// Plugs into the T02 class panel registry (slot "history") or the T03 DEV host page:
//   import { createReviewModule } from "./review-module.js";
//   window.QorgauClassPanel.registerStudentModule(createReviewModule());
// Uses only teacher endpoints of the same origin (cookie auth of the class server):
//   GET  /api/teacher/students/{id}/incidents        episodes + clip state + decisions + zone after review
//   GET  /api/teacher/clips/{incident_id}?student_id= clip (HTTP Range -> seeking in <video>)
//   POST /api/teacher/students/{id}/decision          {incident_id, decision, note_ru}
//   POST /api/teacher/students/{id}/commands          {kind: "request_clip", payload: {incident_id}}  (protocol §3.2/§5)
// DOM is built with textContent only (no innerHTML): names, comments and explanations are never markup.

export const MODULE_ID = "t03-review";

const RULE_LABEL = {
  phone_visible: "Телефон в кадре",
  phone_raised: "Телефон поднят",
  possible_screen_capture: "Телефон направлен на экран",
  gaze_prolonged_down: "Долгий взгляд вниз",
  gaze_prolonged_side: "Долгий взгляд в сторону",
  face_missing: "Лицо не видно в кадре",
  multiple_faces: "Второе лицо в кадре",
  environment_blocked_action: "Ограниченное действие в экзамене",
  environment_escape: "Выход из окна экзамена",
  monitoring_degraded: "Наблюдение было неполным",
  background_speech: "Возможная речь рядом",
  headphones_visible: "Видны наушники",
};
const PRIORITY = {
  high: { label: "высокий приоритет проверки", icon: "▲" },
  medium: { label: "средний приоритет проверки", icon: "◆" },
  low: { label: "низкий приоритет проверки", icon: "●" },
};
const DECISION = {
  confirmed: { label: "Подтверждено", button: "Подтвердить" },
  dismissed: { label: "Отклонено", button: "Отклонить" },
  needs_followup: { label: "Недостаточно данных", button: "Недостаточно данных" },
};
const CATEGORY = { phone: "телефон", attention: "внимание", presence: "присутствие", environment: "среда экзамена", technical: "техника", audio: "звук" };
const ZONE = { red: "Проверить в первую очередь", yellow: "Требует внимания", grey: "Недостаточно данных", green: "Без замечаний" };
const SOURCE = {
  live: "камера, живая сессия",
  replay: "воспроизведение записи",
  synthetic: "СИНТЕТИКА",
  test: "ТЕСТОВЫЙ КЛИП",
  unspecified: "источник не указан",
};
const EVENT_SOURCE = { live: "Камера (заявлено клиентом)", synthetic: "СИНТЕТИЧЕСКИЙ ЭПИЗОД", replay: "ВОСПРОИЗВЕДЕНИЕ ЗАПИСИ", unknown: "ИСТОЧНИК НЕИЗВЕСТЕН" };
const eventSource = (e) => e.origin === "simulated" ? EVENT_SOURCE.synthetic : EVENT_SOURCE[e.source_mode] ?? EVENT_SOURCE.unknown;

/**
 * @param {string} tag
 * @param {Record<string, any>} [attrs]
 * @param {(Node|string|null|undefined|false)[]} [children]
 */
function h(tag, attrs = {}, children = []) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = String(v);
    else if (k === "style") Object.assign(el.style, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k in el && typeof v !== "string") /** @type {any} */ (el)[k] = v;
    else el.setAttribute(k, String(v));
  }
  for (const c of children) if (c !== null && c !== undefined && c !== false) el.append(typeof c === "string" ? document.createTextNode(c) : c);
  return el;
}

function clock(iso) {
  const t = Date.parse(iso);
  return Number.isNaN(t) ? "—" : new Date(t).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
function dateTime(iso) {
  const t = Date.parse(iso);
  return Number.isNaN(t) ? "—" : new Date(t).toLocaleString("ru-RU");
}
/** @param {number|null|undefined} ms */
function duration(ms) {
  if (typeof ms !== "number") return "—";
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1).replace(".", ",")} с`;
  const m = Math.floor(s / 60);
  return `${m} мин ${String(Math.round(s - m * 60)).padStart(2, "0")} с`;
}
const ruleLabel = (/** @type {string} */ r) => RULE_LABEL[r] ?? r.replaceAll("_", " ");

function ensureStyles() {
  if (document.querySelector(`link[data-module="${MODULE_ID}"]`)) return;
  document.head.append(h("link", { rel: "stylesheet", href: new URL("./review.css", import.meta.url).href, "data-module": MODULE_ID }));
}

/**
 * @param {{ base?: string, fetchImpl?: typeof fetch, pollMs?: number, idlePollMs?: number }} [opts]
 */
export function createReviewModule(opts = {}) {
  return {
    id: MODULE_ID,
    slot: /** @type {const} */ ("history"),
    title: "История эпизодов и клипы",
    /** @param {HTMLElement} el @param {{ studentId: string }} ctx */
    mount(el, ctx) {
      return mountReview(el, ctx.studentId, {
        base: opts.base ?? "",
        f: opts.fetchImpl ?? ((...a) => fetch(...a)),
        pollMs: opts.pollMs ?? 2000,
        idlePollMs: opts.idlePollMs ?? 5000,
      });
    },
  };
}

/**
 * @param {HTMLElement} root
 * @param {string} studentId
 * @param {{ base: string, f: typeof fetch, pollMs: number, idlePollMs: number }} o
 */
function mountReview(root, studentId, o) {
  ensureStyles();
  const sid = encodeURIComponent(studentId);
  /** @type {any[]} */
  let episodes = [];
  /** @type {any} */
  let review = null;
  /** @type {string|null} */
  let selected = null;
  let stopped = false;
  /** @type {ReturnType<typeof setTimeout>|null} */
  let timer = null;
  let detailKey = "";
  let listKey = "";
  /** notices that must survive a re-render of the detail panel: incident_id -> {clip?, decision?} */
  /** @type {Map<string, {clip?: string, decision?: string}>} */
  const notices = new Map();
  const notice = (/** @type {string} */ id, /** @type {"clip"|"decision"} */ kind, /** @type {string} */ text) => {
    notices.set(id, { ...(notices.get(id) ?? {}), [kind]: text });
  };

  const status = h("p", { class: "t3-status", role: "status", "aria-live": "polite" }, ["Загружаем историю…"]);
  const summary = h("div", { class: "t3-summary" });
  const timeline = h("div", { class: "t3-timeline", role: "group", "aria-label": "Шкала эпизодов" });
  const list = h("ol", { class: "t3-list", "aria-label": "Эпизоды по времени" });
  const detail = h("section", { class: "t3-detail", "aria-live": "polite" });
  root.replaceChildren(
    h("div", { class: "t3-review", "data-student": studentId }, [
      status,
      summary,
      timeline,
      h("div", { class: "t3-body" }, [h("div", { class: "t3-col-list" }, [list]), detail]),
    ]),
  );

  async function api(path, init) {
    const r = await o.f(`${o.base}${path}`, { credentials: "same-origin", ...init, headers: { Accept: "application/json", ...(init?.headers ?? {}) } });
    let body = null;
    try {
      body = await r.json();
    } catch {
      body = null;
    }
    if (!r.ok) {
      const msg = body?.error?.message_ru ?? (r.status === 401 ? "Нужен вход преподавателя." : `Сервер ответил ${r.status}.`);
      throw Object.assign(new Error(msg), { status: r.status, code: body?.error?.code });
    }
    return body;
  }

  async function load() {
    try {
      const body = await api(`/api/teacher/students/${sid}/incidents`);
      if (stopped) return;
      if (!body || !Array.isArray(body.incidents)) throw new Error("Формат ответа сервера не совпадает с ожидаемым.");
      episodes = body.incidents;
      review = body.review ?? null;
      status.textContent = episodes.length ? `Эпизодов: ${episodes.length}` : "Эпизодов пока нет.";
      status.classList.remove("t3-error");
      if (selected === null && episodes.length) selected = episodes[0].incident_id;
      render();
    } catch (err) {
      if (stopped) return;
      status.textContent = `История недоступна: ${err instanceof Error ? err.message : String(err)}`;
      status.classList.add("t3-error");
    } finally {
      schedule();
    }
  }

  function schedule() {
    if (stopped) return;
    if (timer) clearTimeout(timer);
    const busy = episodes.some((e) => e.clip?.state === "loading" || e.state === "open");
    timer = setTimeout(load, busy ? o.pollMs : o.idlePollMs);
  }

  function render() {
    renderSummary();
    const key = JSON.stringify(episodes.map((e) => [e.incident_id, e.state, e.duration_ms, e.decision, e.clip?.state, e.priority])) + selected;
    if (key !== listKey) {
      listKey = key;
      renderTimeline();
      renderList();
    }
    renderDetail();
  }

  function renderSummary() {
    if (!review) {
      summary.replaceChildren();
      return;
    }
    const zoneBadge = (/** @type {string|null} */ z) =>
      h("span", { class: `t3-zone t3-zone-${z ?? "none"}` }, [z ? ZONE[z] ?? z : "нет данных"]);
    summary.replaceChildren(
      h("div", { class: "t3-zones" }, [
        h("span", {}, ["По сигналам компьютера студента: "]),
        zoneBadge(review.reported_zone),
        h("span", {}, [" · С учётом решений преподавателя: "]),
        zoneBadge(review.zone_after_review),
      ]),
      h("p", { class: "t3-muted" }, [
        `Отклонено и не учитывается: ${review.dismissed_excluded ?? 0}. Не проверено: ${review.unreviewed ?? 0}. `,
        "Зона — очередь проверки, а не вывод о нарушении.",
      ]),
      review.reasons_ru?.length ? h("ul", { class: "t3-reasons" }, review.reasons_ru.map((r) => h("li", {}, [String(r)]))) : null,
    );
  }

  function renderTimeline() {
    if (!episodes.length) {
      timeline.replaceChildren();
      return;
    }
    const starts = episodes.map((e) => Date.parse(e.t_start_wall));
    const ends = episodes.map((e, i) => starts[i] + (e.duration_ms ?? 0));
    const t0 = Math.min(...starts);
    const t1 = Math.max(...ends, t0 + 60_000);
    const span = t1 - t0;
    const bars = episodes.map((e, i) => {
      const left = ((starts[i] - t0) / span) * 100;
      const width = Math.max(((e.duration_ms ?? 0) / span) * 100, 1.2);
      const p = PRIORITY[e.priority] ?? PRIORITY.low;
      const label = `${clock(e.t_start_wall)} ${ruleLabel(e.rule_id)}, ${p.label}, ${duration(e.duration_ms)}${e.decision ? `, ${DECISION[e.decision]?.label}` : ""}`;
      return h(
        "button",
        {
          type: "button",
          class: `t3-bar t3-p-${e.priority}${e.decision === "dismissed" ? " t3-dismissed" : ""}${e.incident_id === selected ? " t3-selected" : ""}`,
          style: { left: `${left}%`, width: `${width}%` },
          title: label,
          "aria-label": label,
          "aria-pressed": e.incident_id === selected ? "true" : "false",
          onclick: () => select(e.incident_id),
        },
        [p.icon],
      );
    });
    timeline.replaceChildren(
      h("div", { class: "t3-track" }, bars),
      h("div", { class: "t3-axis" }, [h("span", {}, [clock(new Date(t0).toISOString())]), h("span", {}, [clock(new Date(t1).toISOString())])]),
    );
  }

  function clipBadge(e) {
    const s = e.clip?.state;
    const text = { available: "клип доступен", loading: "клип загружается", unavailable: "клип недоступен", not_requested: "клип не запрошен" }[s] ?? "";
    return text ? h("span", { class: `t3-badge t3-clip-${s}` }, [text]) : null;
  }

  function renderList() {
    list.replaceChildren(
      ...episodes.map((e) => {
        const p = PRIORITY[e.priority] ?? PRIORITY.low;
        return h("li", {}, [
          h(
            "button",
            {
              type: "button",
              class: `t3-item${e.incident_id === selected ? " t3-selected" : ""}${e.decision === "dismissed" ? " t3-dismissed" : ""}`,
              "aria-current": e.incident_id === selected ? "true" : undefined,
              onclick: () => select(e.incident_id),
            },
            [
              h("span", { class: "t3-time" }, [clock(e.t_start_wall)]),
              h("span", { class: `t3-prio t3-p-${e.priority}`, "aria-label": p.label, title: p.label }, [p.icon]),
              h("span", { class: "t3-rule" }, [ruleLabel(e.rule_id)]),
              h("span", { class: "t3-dur" }, [e.state === "open" ? "идёт" : duration(e.duration_ms)]),
              e.decision ? h("span", { class: `t3-badge t3-dec-${e.decision}` }, [DECISION[e.decision]?.label ?? e.decision]) : h("span", { class: "t3-badge t3-dec-none" }, ["не проверено"]),
              clipBadge(e),
              h("span", { class: "t3-badge t3-source" }, [eventSource(e)]),
            ],
          ),
        ]);
      }),
    );
  }

  function select(id) {
    if (id !== selected) notices.clear();
    selected = id;
    listKey = "";
    render();
  }

  function renderDetail() {
    const e = episodes.find((x) => x.incident_id === selected);
    if (!e) {
      detailKey = "";
      detail.replaceChildren(h("p", { class: "t3-muted" }, [episodes.length ? "Выберите эпизод." : ""]));
      return;
    }
    // re-render only when something visible changed: a playing <video> is never recreated by polling
    const key = JSON.stringify([e.incident_id, e.state, e.duration_ms, e.explanation_ru, e.clip, e.decision_history]);
    if (key === detailKey) return;
    const keepVideo = detail.querySelector("video");
    const sameClip = keepVideo && keepVideo.dataset.sha === e.clip?.sha256;
    detailKey = key;
    const p = PRIORITY[e.priority] ?? PRIORITY.low;
    detail.replaceChildren(
      h("h4", {}, [ruleLabel(e.rule_id)]),
      h("p", { class: "t3-expl" }, [e.explanation_ru || "Описание не передано."]),
      h("p", { class: "t3-test-label" }, [eventSource(e)]),
      h("dl", { class: "t3-facts" }, [
        h("dt", {}, ["Начало"]), h("dd", {}, [dateTime(e.t_start_wall)]),
        h("dt", {}, ["Длительность"]), h("dd", {}, [e.state === "open" ? `идёт (${duration(e.duration_ms)})` : duration(e.duration_ms)]),
        h("dt", {}, ["Приоритет"]), h("dd", {}, [`${p.icon} ${p.label}`]),
        h("dt", {}, ["Категория"]), h("dd", {}, [CATEGORY[e.category] ?? e.category]),
        h("dt", {}, ["Сессия источника"]), h("dd", {}, [e.source_session_id ?? "не передана"]),
      ]),
      clipArea(e, sameClip ? keepVideo : null),
      decisionArea(e),
    );
  }

  function clipUrl(e) {
    return `${o.base}/api/teacher/clips/${encodeURIComponent(e.incident_id)}?student_id=${sid}`;
  }

  function clipArea(e, reuseVideo) {
    const c = e.clip ?? {};
    const box = h("div", { class: `t3-clip t3-clip-${c.state ?? "none"}` });
    if (c.state === "available" || c.state === "unavailable") notices.set(e.incident_id, { ...(notices.get(e.incident_id) ?? {}), clip: undefined });
    const note = h("p", { class: "t3-clip-msg", role: "status" }, [notices.get(e.incident_id)?.clip ?? ""]);
    if (c.state === "available") {
      const src = SOURCE[c.source] ?? SOURCE.unspecified;
      const testLike = c.source === "test" || c.source === "synthetic";
      if (testLike) box.append(h("p", { class: "t3-test-label" }, [`${src}: не запись реального студента`]));
      if (c.browser_playable) {
        const video = reuseVideo ?? h("video", { controls: true, preload: "metadata", src: clipUrl(e), "data-sha": c.sha256 });
        video.addEventListener("error", () => {
          note.textContent = "Браузер не смог воспроизвести клип. Его можно скачать по ссылке ниже.";
        });
        box.append(video);
      } else {
        box.append(h("p", {}, [`Клип получен (${c.codec_label ?? c.media_type}), но браузер его не воспроизводит. Скачайте файл.`]));
      }
      box.append(
        note,
        h("p", { class: "t3-muted" }, [
          `Источник: ${src}. ${c.duration_s ? `Длина ${String(c.duration_s).replace(".", ",")} с. ` : ""}SHA-256 ${String(c.sha256).slice(0, 16)}…`,
        ]),
        h("a", { href: clipUrl(e), download: "", class: "t3-link" }, ["Скачать клип"]),
      );
      return box;
    }
    if (c.state === "loading") {
      box.append(h("p", { class: "t3-loading" }, [`Клип загружается… (запрошен в ${clock(c.requested_at)})`]));
      return box;
    }
    if (c.state === "unavailable") {
      box.append(h("p", {}, [`Клип недоступен: ${c.reason_ru ?? "причина не указана"}`]));
    } else {
      box.append(h("p", {}, ["Клип не запрашивался. Видео передаётся с компьютера студента только по запросу преподавателя."]));
    }
    if (c.can_request) {
      const btn = h("button", { type: "button", class: "t3-btn", onclick: () => requestClip(e, btn, note) }, [
        c.state === "unavailable" ? "Запросить снова" : "Запросить клип",
      ]);
      box.append(btn);
    }
    box.append(note);
    return box;
  }

  async function requestClip(e, btn, note) {
    btn.disabled = true;
    note.textContent = "Отправляем запрос…";
    try {
      await api(`/api/teacher/students/${sid}/commands`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind: "request_clip", payload: { incident_id: e.incident_id } }),
      });
      note.textContent = "Запрос отправлен.";
      notice(e.incident_id, "clip", "Запрос отправлен студенту.");
      await load();
    } catch (err) {
      note.textContent = `Не удалось запросить клип: ${err instanceof Error ? err.message : String(err)}`;
      btn.disabled = false;
    }
  }

  function decisionArea(e) {
    /** @type {string|null} */
    let choice = e.decision;
    const msg = h("p", { class: "t3-dec-msg", role: "status" }, [notices.get(e.incident_id)?.decision ?? ""]);
    const note = h("textarea", { class: "t3-note", maxlength: "1000", rows: "3", "aria-label": "Комментарий преподавателя", placeholder: "Комментарий (необязательно)" });
    const buttons = Object.entries(DECISION).map(([value, d]) =>
      h(
        "button",
        {
          type: "button",
          class: `t3-choice t3-dec-${value}`,
          "aria-pressed": choice === value ? "true" : "false",
          onclick: (/** @type {Event} */ ev) => {
            choice = value;
            for (const b of buttons) b.setAttribute("aria-pressed", b === ev.currentTarget ? "true" : "false");
          },
        },
        [d.button],
      ),
    );
    const save = h("button", { type: "button", class: "t3-btn t3-save" }, ["Сохранить решение"]);
    save.addEventListener("click", async () => {
      if (!choice) {
        msg.textContent = "Выберите решение.";
        return;
      }
      save.disabled = true;
      msg.textContent = "Сохраняем…";
      try {
        await api(`/api/teacher/students/${sid}/decision`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ incident_id: e.incident_id, decision: choice, note_ru: note.value }),
        });
        msg.textContent = "Решение сохранено.";
        notice(e.incident_id, "decision", "Решение сохранено.");
        await load();
      } catch (err) {
        msg.textContent = `Решение не сохранено: ${err instanceof Error ? err.message : String(err)}`;
        save.disabled = false;
      }
    });
    const history = (e.decision_history ?? []).slice().reverse();
    return h("div", { class: "t3-decision" }, [
      h("h5", {}, [e.decision ? `Решение преподавателя: ${DECISION[e.decision]?.label}` : "Решение преподавателя: не принято"]),
      h("div", { class: "t3-choices", role: "group", "aria-label": "Решение" }, buttons),
      note,
      save,
      msg,
      history.length
        ? h("div", { class: "t3-history" }, [
            h("h5", {}, ["Журнал решений"]),
            h(
              "ol",
              { reversed: true },
              history.map((d, i) =>
                h("li", {}, [
                  h("span", { class: "t3-time" }, [dateTime(d.created_at)]),
                  " ",
                  h("strong", {}, [DECISION[d.decision]?.label ?? d.decision]),
                  ` · ${d.operator}`,
                  i < history.length - 1 || d.supersedes ? h("span", { class: "t3-muted" }, [d.supersedes ? " · изменило предыдущее" : ""]) : null,
                  d.note_ru ? h("p", { class: "t3-note-text" }, [d.note_ru]) : null,
                ]),
              ),
            ),
          ])
        : null,
    ]);
  }

  load();
  return () => {
    stopped = true;
    if (timer) clearTimeout(timer);
  };
}
