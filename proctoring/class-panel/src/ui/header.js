// @ts-check
// Header: mode badge, counters, connection banner, status filters, search, sort, order pin, density.
import { ZONE_LABEL, ZONES } from "../model.js";
import { h, setAttr, setText, svg } from "./dom.js";
import { ICON } from "./icons.js";

/** @typedef {import("../store.js").PanelStore} PanelStore */

/**
 * @param {PanelStore} store
 * @param {{ mode: "demo"|"real", modeLabel: string, onRetry: () => void, onPreview: (on: boolean) => void }} o
 */
export function createHeader(store, o) {
  const cTotal = h("strong", {});
  const cOnline = h("strong", {});
  const cOffline = h("strong", {});
  const cUnknown = h("strong", {});
  const unknownBox = h("span", { class: "ctr ctr-unknown", hidden: true }, [svg(ICON.unknown), " связь неизвестна: ", cUnknown]);
  const counters = h("div", { class: "counters", role: "group", "aria-label": "Счётчики класса" }, [
    h("span", { class: "ctr" }, ["Студентов: ", cTotal]),
    h("span", { class: "ctr ctr-online" }, [svg(ICON.online), " на связи: ", cOnline]),
    h("span", { class: "ctr ctr-offline" }, [svg(ICON.offline), " без связи: ", cOffline]),
    unknownBox,
  ]);

  // connection banner
  const bannerText = h("span", { class: "banner-text" });
  const retryBtn = h("button", { type: "button", class: "btn btn-small", hidden: true }, ["Повторить сейчас"]);
  retryBtn.addEventListener("click", () => o.onRetry());
  const banner = h("div", { class: "banner", role: "status", "aria-live": "polite", hidden: true }, [bannerText, retryBtn]);

  // zone filter chips
  /** @type {Map<string, {btn: HTMLElement, n: HTMLElement}>} */
  const chips = new Map();
  const chipRow = h("div", { class: "chips", role: "group", "aria-label": "Фильтр по статусу" });
  for (const z of ZONES) {
    const n = h("span", { class: "chip-n" });
    const btn = h("button", { type: "button", class: `chip z-${z}`, "aria-pressed": "false", "data-zone": z }, [svg(ICON[z]), h("span", {}, [ZONE_LABEL[z]]), n]);
    btn.addEventListener("click", () => {
      const zones = new Set(store.filter.zones);
      if (zones.has(z)) zones.delete(z);
      else zones.add(z);
      store.setFilter({ zones });
    });
    chips.set(z, { btn, n });
    chipRow.append(btn);
  }
  const clearBtn = h("button", { type: "button", class: "btn btn-link", hidden: true }, ["Сбросить фильтры"]);
  clearBtn.addEventListener("click", () => {
    search.value = "";
    linkSel.value = "all";
    store.setFilter({ zones: new Set(), query: "", link: "all" });
  });
  chipRow.append(clearBtn);

  const search = /** @type {HTMLInputElement} */ (h("input", { type: "search", id: "q", placeholder: "Имя или компьютер", autocomplete: "off", spellcheck: "false" }));
  let qTimer = 0;
  search.addEventListener("input", () => {
    clearTimeout(qTimer);
    qTimer = window.setTimeout(() => store.setFilter({ query: search.value }), 120);
  });
  const linkSel = /** @type {HTMLSelectElement} */ (
    h("select", { id: "link" }, [h("option", { value: "all" }, ["все"]), h("option", { value: "online" }, ["на связи"]), h("option", { value: "offline" }, ["без связи"])])
  );
  linkSel.addEventListener("change", () => store.setFilter({ link: /** @type {any} */ (linkSel.value) }));
  const sortSel = /** @type {HTMLSelectElement} */ (
    h("select", { id: "sort" }, [
      h("option", { value: "priority" }, ["по приоритету проверки"]),
      h("option", { value: "recent" }, ["по последнему событию"]),
      h("option", { value: "name" }, ["по имени"]),
      h("option", { value: "computer" }, ["по номеру компьютера"]),
    ])
  );
  sortSel.addEventListener("change", () => store.setFilter({ sort: /** @type {any} */ (sortSel.value) }));
  const pinBtn = h("button", { type: "button", class: "btn toggle", "aria-pressed": "false", title: "Карточки не меняют порядок, пока закреплено" }, [svg(ICON.pin), " Закрепить порядок"]);
  pinBtn.addEventListener("click", () => {
    const on = pinBtn.getAttribute("aria-pressed") !== "true";
    pinBtn.setAttribute("aria-pressed", String(on));
    store.setFrozen(on);
  });
  const prevBtn = h("button", { type: "button", class: "btn toggle", "aria-pressed": "true" }, ["Превью"]);
  prevBtn.addEventListener("click", () => {
    const on = prevBtn.getAttribute("aria-pressed") !== "true";
    prevBtn.setAttribute("aria-pressed", String(on));
    o.onPreview(on);
  });

  const toolbar = h("div", { class: "toolbar" }, [
    h("label", { class: "field field-search", for: "q" }, [svg(ICON.search), h("span", { class: "sr-only" }, ["Поиск по имени или компьютеру"]), search]),
    h("label", { class: "field", for: "link" }, [h("span", {}, ["Связь"]), linkSel]),
    h("label", { class: "field", for: "sort" }, [h("span", {}, ["Порядок"]), sortSel]),
    pinBtn,
    prevBtn,
  ]);

  const root = h("header", { class: "top" }, [
    h("div", { class: "top-row" }, [
      h("div", { class: "brand" }, [
        h("h1", {}, ["Qorgau · Класс"]),
        h("span", { class: `mode mode-${o.mode}`, title: o.mode === "demo" ? "Все данные на экране имитированы" : "Данные сервера класса" }, [o.modeLabel]),
      ]),
      counters,
    ]),
    banner,
    h("div", { class: "filters" }, [chipRow, toolbar]),
  ]);

  function render() {
    const c = store.counters();
    setText(cTotal, store.loaded ? String(c.total) : "—");
    setText(cOnline, store.loaded && store.feedLive ? String(c.online) : "—");
    setText(cOffline, store.loaded && store.feedLive ? String(c.offline) : "—");
    setText(cUnknown, String(c.unknown));
    unknownBox.hidden = !(store.loaded && c.unknown > 0 && store.feedLive);
    for (const z of ZONES) {
      const chip = /** @type {{btn: HTMLElement, n: HTMLElement}} */ (chips.get(z));
      setText(chip.n, store.loaded ? String(c.zones[z]) : "—");
      setAttr(chip.btn, "aria-pressed", String(store.filter.zones.has(z)));
      setAttr(chip.btn, "aria-label", `${ZONE_LABEL[z]}: ${store.loaded ? c.zones[z] : "нет данных"}. Фильтр ${store.filter.zones.has(z) ? "включён" : "выключен"}`);
    }
    clearBtn.hidden = !(store.filter.zones.size || store.filter.query || store.filter.link !== "all");

    const conn = store.connection;
    const now = store.now();
    let text = "";
    let tone = "info";
    if (conn.status === "loading") text = conn.detail || "Загрузка…";
    else if (conn.status === "reconnecting") {
      const secs = Math.max(0, Math.round((now - conn.since) / 1000));
      const retry = conn.retryAt ? ` Повтор через ${Math.max(0, Math.ceil((conn.retryAt - now) / 1000))} с.` : "";
      text = `Нет связи с сервером класса ${secs} с. Данные на экране устарели — показаны последние полученные, без статусов «на связи».${retry} ${conn.detail}`;
      tone = "warn";
    } else if (conn.status === "error") {
      const retry = conn.retryAt ? ` Повтор через ${Math.max(0, Math.ceil((conn.retryAt - now) / 1000))} с.` : "";
      text = `Не удалось загрузить класс. ${conn.detail}${retry}`;
      tone = "danger";
    } else if (conn.status === "auth" || conn.status === "forbidden") {
      text = conn.detail;
      tone = "danger";
    }
    banner.hidden = conn.status === "live";
    banner.className = `banner banner-${tone}`;
    setText(bannerText, text);
    retryBtn.hidden = !(conn.status === "error" || conn.status === "reconnecting");
  }

  return { root, render, searchInput: search };
}
