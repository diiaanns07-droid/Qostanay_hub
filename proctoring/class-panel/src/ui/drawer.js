// @ts-check
// Student card (dialog). Shows only delivered data; slots for history / commands / audio come from the
// module registry (src/extensions.js). Keyboard: focus moves in, Tab is trapped, Escape closes and focus
// returns to the element that opened it.
import {
  ago,
  CAMERA_LABEL,
  DECISION_LABEL,
  displayName,
  EXAM_STATE_LABEL,
  normalizeIncident,
  PRIORITY_LABEL,
  ZONE_LABEL,
} from "../model.js";
import { SLOT_TITLE, SLOTS } from "../extensions.js";
import { h, setText, svg } from "./dom.js";
import { ICON } from "./icons.js";

/** @typedef {import("../store.js").PanelStore} PanelStore */
/** @typedef {import("../extensions.js").ModuleRegistry} ModuleRegistry */

/**
 * @param {PanelStore} store
 * @param {ModuleRegistry} registry
 * @param {{ mode: "demo"|"real", getIncidents: (id: string) => Promise<{ok:true, incidents: unknown[]} | {ok:false, error:string}> }} o
 */
export function createDrawer(store, registry, o) {
  const overlay = h("div", { class: "drawer-overlay", hidden: true });
  const dialog = h("div", { class: "drawer", role: "dialog", "aria-modal": "true", "aria-labelledby": "dr-title", tabindex: "-1" });
  overlay.append(dialog);
  /** @type {string|null} */
  let openId = null;
  /** @type {HTMLElement|null} */
  let returnTo = null;
  /** @type {Array<() => void>} */
  let cleanups = [];
  /** @type {null | (() => void)} */
  let renderStatus = null;

  overlay.addEventListener("mousedown", (e) => {
    if (e.target === overlay) close();
  });
  dialog.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      e.preventDefault();
      close();
      return;
    }
    if (e.key !== "Tab") return;
    const f = focusables();
    if (f.length === 0) return;
    const first = /** @type {HTMLElement} */ (f[0]);
    const last = /** @type {HTMLElement} */ (f[f.length - 1]);
    if (e.shiftKey && (document.activeElement === first || document.activeElement === dialog)) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  });

  function focusables() {
    return /** @type {HTMLElement[]} */ ([
      ...dialog.querySelectorAll("button:not([disabled]), [href], input:not([disabled]), select, textarea, [tabindex]:not([tabindex='-1'])"),
    ]).filter((el) => !el.closest("[hidden]"));
  }

  /** @param {string} label @param {HTMLElement} value */
  const row = (label, value) => h("div", { class: "kv" }, [h("dt", {}, [label]), h("dd", {}, [value])]);

  /** @param {string} id @param {HTMLElement} from */
  function open(id, from) {
    if (openId) close(false);
    const x = store.derived(id);
    if (!x) return;
    openId = id;
    returnTo = from;
    dialog.replaceChildren();
    const v0 = x.entry.view;

    const title = h("h2", { id: "dr-title" }, [displayName(v0)]);
    const sub = h("p", { class: "dr-sub" }, [v0.computerName ?? "компьютер: нет данных", " · id ", v0.id]);
    const closeBtn = h("button", { type: "button", class: "icon-btn", "aria-label": "Закрыть карточку" }, [svg(ICON.close)]);
    closeBtn.addEventListener("click", () => close());

    const zoneBox = h("div", { class: "dr-zone" });
    const kv = h("dl", { class: "dr-kv" });
    const media = h("div", { class: "dr-media" });
    const ackBtn = h("button", { type: "button", class: "btn" }, ["Отметить просмотренным"]);
    ackBtn.addEventListener("click", () => store.acknowledge(id));

    renderStatus = () => {
      const y = store.derived(id);
      if (!y) {
        setText(zoneBox, "Студент больше не в списке сервера.");
        return;
      }
      const v = y.entry.view;
      const d = y.d;
      zoneBox.className = `dr-zone z-${d.zone}`;
      zoneBox.replaceChildren(
        h("div", { class: "dr-zone-head" }, [svg(ICON[d.zone]), h("strong", {}, [ZONE_LABEL[d.zone]])]),
        d.reasons.length
          ? h("ul", { class: "dr-reasons" }, d.reasons.map((r) => h("li", {}, [r])))
          : h("p", { class: "muted" }, ["Причины не переданы."]),
        h("p", { class: "dr-zone-note" }, [
          d.zone === "grey"
            ? "Серый — недостаточно данных, а не «низкий риск»."
            : "Зона — приоритет проверки из анализа на компьютере студента (A05), не вывод о нарушении.",
        ]),
      );
      const now = store.now();
      const yesNo = (/** @type {boolean|null} */ b, /** @type {string} */ yes, /** @type {string} */ no) => (b === null ? "нет данных" : b ? yes : no);
      kv.replaceChildren(
        row("Связь", h("span", {}, [d.link === "online" ? "на связи" : d.link === "offline" ? "нет связи" : "неизвестно (нет связи панели с сервером)"])),
        row("Последний статус", h("span", {}, [v.lastStatusAt === null ? "не получен" : ago(v.lastStatusAt, now)])),
        row("Камера", h("span", {}, [v.camera === null ? "нет данных" : CAMERA_LABEL[v.camera]])),
        row("Наблюдение", h("span", {}, [v.monitoring === null ? "нет данных" : v.monitoring === "ok" ? "полное" : "неполное"])),
        row("Этап", h("span", {}, [v.examState ? EXAM_STATE_LABEL[v.examState] : "нет данных"])),
        row("Эпизоды", h("span", {}, [
          v.incidentsTotal === null ? "нет данных" : String(v.incidentsTotal),
          v.byPriority ? ` (высокий: ${v.byPriority.high}, средний: ${v.byPriority.medium}, низкий: ${v.byPriority.low})` : "",
        ])),
        row("Без решения преподавателя", h("span", {}, [v.unreviewed === null ? "нет данных (сервер не передаёт)" : String(v.unreviewed)])),
        row("Последнее событие", h("span", {}, [v.lastEventAt === null ? "нет" : ago(v.lastEventAt, now)])),
        row("Экран студента", h("span", {}, [yesNo(v.locked, "заблокирован преподавателем", "не заблокирован")])),
        row("Микрофон", h("span", {}, [yesNo(v.micActive, "включён преподавателем", "выключен")])),
      );
      const p = y.entry.preview;
      if (p) {
        const fresh = !d.stale && v.camera === "ok";
        const img = /** @type {HTMLImageElement|null} */ (media.querySelector("img"));
        if (!img) media.replaceChildren(h("img", { src: p.url, alt: `Превью с компьютера: ${displayName(v)}` }), h("span", { class: "media-note" }));
        else if (img.getAttribute("src") !== p.url) img.src = p.url;
        media.classList.toggle("stale", !fresh);
        setText(/** @type {HTMLElement} */ (media.querySelector(".media-note")), fresh ? (p.at ? `кадр ${ago(p.at, now)}` : "") : "превью устарело");
      } else {
        media.replaceChildren(h("p", { class: "muted" }, ["Превью не получено."]));
      }
      ackBtn.hidden = !y.inQueue;
    };
    renderStatus();
    const unsub = store.subscribe(() => renderStatus?.());
    cleanups.push(() => unsub());

    const slots = SLOTS.map((slot) => {
      const box = h("section", { class: `dr-slot slot-${slot}`, "aria-labelledby": `slot-${slot}` });
      const mods = registry.forSlot(slot);
      box.append(h("h3", { id: `slot-${slot}` }, [mods[0]?.title ?? SLOT_TITLE[slot]]));
      if (mods.length === 0) {
        box.append(
          h("p", { class: "slot-empty" }, [
            `Место для модуля «${SLOT_TITLE[slot]}». Модуль подключается через точку расширения панели (registerStudentModule, слот "${slot}") и в T02 не реализован.`,
          ]),
        );
      }
      for (const m of mods) {
        const mountEl = h("div", { class: "slot-body", "data-module": m.id });
        box.append(mountEl);
        try {
          const un = m.mount(mountEl, {
            studentId: id,
            mode: o.mode,
            getStudent: () => store.students.get(id)?.view ?? null,
            subscribe: (fn) => store.subscribe(fn),
            getIncidents: () => o.getIncidents(id),
          });
          if (typeof un === "function") cleanups.push(un);
        } catch (err) {
          mountEl.replaceChildren(h("p", { class: "slot-error" }, [`Модуль «${m.title}» не запустился.`]));
          console.error("class-panel module failed", m.id, err);
        }
      }
      return box;
    });

    dialog.append(
      h("header", { class: "dr-head" }, [h("div", {}, [title, sub]), closeBtn]),
      o.mode === "demo" ? h("p", { class: "demo-note" }, ["DEMO: данные этой карточки имитированы."]) : null,
      h("div", { class: "dr-grid" }, [h("div", { class: "dr-col" }, [zoneBox, media, ackBtn]), h("div", { class: "dr-col" }, [kv])]),
      ...slots,
    );
    overlay.hidden = false;
    document.body.classList.add("modal-open");
    closeBtn.focus();
  }

  function close(restore = true) {
    if (!openId) return;
    for (const c of cleanups) {
      try {
        c();
      } catch (err) {
        console.error(err);
      }
    }
    cleanups = [];
    renderStatus = null;
    openId = null;
    overlay.hidden = true;
    document.body.classList.remove("modal-open");
    dialog.replaceChildren();
    if (restore && returnTo && document.contains(returnTo)) returnTo.focus();
    returnTo = null;
  }

  return {
    root: overlay,
    open,
    close,
    get openId() {
      return openId;
    },
  };
}

/**
 * Built-in history module: episodes of the student from the adapter (GET /api/teacher/students/{id}/incidents
 * in real mode) merged with episodes received live. Read-only: decisions belong to a review module.
 * @param {PanelStore} store
 */
export function historyModule(store) {
  return {
    id: "builtin-episodes",
    slot: /** @type {const} */ ("history"),
    title: "Эпизоды (только просмотр)",
    builtin: true,
    /** @param {HTMLElement} el @param {import("../extensions.js").ModuleContext} ctx */
    mount(el, ctx) {
      const status = h("p", { class: "muted", role: "status" }, ["Загружаем эпизоды…"]);
      const list = h("ol", { class: "ep-list" });
      el.append(status, list);
      let alive = true;
      /** @type {Map<string, NonNullable<ReturnType<typeof normalizeIncident>>>} */
      let loaded = new Map();
      const draw = () => {
        const merged = new Map(loaded);
        for (const i of store.incidentsOf(ctx.studentId)) merged.set(i.id, { ...(merged.get(i.id) ?? {}), ...i });
        const items = [...merged.values()].sort((a, b) => (b.startAt ?? 0) - (a.startAt ?? 0)).slice(0, 50);
        list.replaceChildren(
          ...items.map((i) =>
            h("li", { class: `ep ep-${i.priority ?? "none"}` }, [
              h("div", { class: "ep-top" }, [
                h("span", { class: "ep-time" }, [i.startAt ? new Date(i.startAt).toLocaleTimeString("ru-RU") : "время —"]),
                h("span", { class: "ep-prio" }, [i.priority ? PRIORITY_LABEL[i.priority] : "приоритет —"]),
                h("span", { class: "ep-state" }, [i.state === "open" ? "идёт" : i.state === "closed" ? "закончился" : ""]),
              ]),
              h("div", { class: "ep-text" }, [i.explanation ?? i.rule ?? "без описания"]),
              h("div", { class: "ep-meta" }, [
                i.decision ? DECISION_LABEL[i.decision] : "Решение преподавателя не принято",
                i.clipAvailable ? " · есть клип" : "",
              ]),
            ]),
          ),
        );
        if (items.length === 0 && status.textContent === "") setText(status, "Эпизодов нет.");
      };
      ctx.getIncidents().then((r) => {
        if (!alive) return;
        if (r.ok) {
          loaded = new Map();
          for (const raw of r.incidents) {
            const n = normalizeIncident(raw);
            if (n) loaded.set(n.id, n);
          }
          setText(status, "");
        } else setText(status, `Эпизоды не загружены: ${r.error}`);
        draw();
      });
      const unsub = ctx.subscribe(draw);
      return () => {
        alive = false;
        unsub();
      };
    },
  };
}
