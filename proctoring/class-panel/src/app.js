// @ts-check
// Qorgau Class — teacher panel (T02). Entry point.
// Adapter choice is explicit and fixed for the page: config.json → `adapter`, overridable by ?adapter=.
// DEMO and REAL data are never mixed; switching requires a reload.
import { createDemoAdapter } from "./adapters/demo.js";
import { createRealAdapter } from "./adapters/real.js";
import { ModuleRegistry } from "./extensions.js";
import { PanelStore } from "./store.js";
import { createDemoPanel } from "./ui/demoPanel.js";
import { h } from "./ui/dom.js";
import { createDrawer, historyModule } from "./ui/drawer.js";
import { createGrid } from "./ui/grid.js";
import { createHeader } from "./ui/header.js";
import { createQueue } from "./ui/queue.js";
import { createSessionBar } from "./ui/session.js";

export const PANEL_VERSION = "0.1.0";
export const CONTRACT = "qorgau.class.v1";

async function loadConfig() {
  /** @type {{ adapter?: string, demo?: { students?: number, seed?: number, rate?: string } }} */
  let cfg = {};
  try {
    const r = await fetch("./config.json", { cache: "no-store" });
    if (r.ok) cfg = await r.json();
  } catch {
    /* no config: defaults below */
  }
  const q = new URLSearchParams(location.search);
  const adapter = q.get("adapter") ?? cfg.adapter ?? "demo";
  const num = (/** @type {string|null} */ v, /** @type {number} */ d) => (v !== null && /^\d{1,3}$/.test(v) ? Number(v) : d);
  return {
    adapter: adapter === "real" ? "real" : adapter === "demo" ? "demo" : "invalid",
    students: Math.min(100, num(q.get("students"), cfg.demo?.students ?? 30)),
    seed: num(q.get("seed"), cfg.demo?.seed ?? 7),
    rate: /** @type {"calm"|"normal"|"busy"} */ (["calm", "normal", "busy"].includes(q.get("rate") ?? "") ? q.get("rate") : cfg.demo?.rate ?? "normal"),
    debug: q.get("debug") === "1",
  };
}

async function main() {
  const root = /** @type {HTMLElement} */ (document.getElementById("app"));
  const cfg = await loadConfig();
  if (cfg.adapter === "invalid") {
    root.replaceChildren(h("p", { class: "fatal" }, ["Неизвестный адаптер в config.json или адресе (ожидается demo или real)."]));
    return;
  }
  const store = new PanelStore();
  const demo = cfg.adapter === "demo" ? createDemoAdapter({ students: cfg.students, seed: cfg.seed, rate: cfg.rate }) : null;
  const adapter = demo ?? createRealAdapter();
  document.documentElement.dataset.mode = adapter.kind;

  const live = h("div", { class: "sr-only", role: "status", "aria-live": "polite" });
  // At most one announcement per 3 s (100 students must not flood a screen reader). Messages that arrive
  // inside the window are collected (≤ 3, newest kept) and read together when it ends — none is dropped silently.
  let lastAnnounce = 0;
  /** @type {string[]} */
  let pendingTexts = [];
  /** @type {ReturnType<typeof setTimeout>|null} */
  let announceTimer = null;
  const announce = (/** @type {string} */ text) => {
    const t = Date.now();
    const wait = 3000 - (t - lastAnnounce);
    if (wait <= 0 && !announceTimer) {
      lastAnnounce = t;
      live.textContent = text;
      return;
    }
    if (!pendingTexts.includes(text)) pendingTexts = [...pendingTexts, text].slice(-3);
    if (!announceTimer) {
      announceTimer = setTimeout(() => {
        announceTimer = null;
        lastAnnounce = Date.now();
        if (pendingTexts.length) live.textContent = pendingTexts.join(". ");
        pendingTexts = [];
      }, Math.max(0, wait));
    }
  };

  const registry = new ModuleRegistry();
  registry.register(historyModule(store));
  const drawer = createDrawer(store, registry, { mode: adapter.kind, getIncidents: (id) => adapter.getIncidents(id) });
  const open = (/** @type {string} */ id, /** @type {HTMLElement} */ from) => drawer.open(id, from);
  const grid = createGrid(store, { demo: adapter.kind === "demo", onOpen: open });
  const queue = createQueue(store, { onOpen: open, announce });
  const header = createHeader(store, {
    mode: adapter.kind,
    modeLabel: adapter.kind === "demo" ? "DEMO · тестовые данные" : "Пульт преподавателя",
    onRetry: () => adapter.retry(),
    onPreview: (on) => grid.setShowPreview(on),
  });
  const sessionBar = createSessionBar(adapter.kind);

  /** @type {Array<HTMLElement|null>} */
  const nodes = [
    h("a", { class: "skip", href: "#grid-start" }, ["К карточкам студентов"]),
    adapter.kind === "demo" ? h("div", { class: "demo-strip", role: "note" }, ["Демонстрация интерфейса · студенты, события и изображения созданы для теста"]) : null,
    header.root,
    sessionBar.root,
    h("main", { class: "layout" }, [h("div", { class: "layout-main", id: "grid-start", tabindex: "-1" }, [grid.root]), queue.root]),
    drawer.root,
    demo ? createDemoPanel(demo, announce) : null,
    live,
  ];
  root.replaceChildren(...nodes.filter((x) => x !== null));

  /** @type {Record<string, number[]>} */
  const parts = { header: [], grid: [], queue: [] };
  store.subscribe(() => {
    if (!cfg.debug) {
      header.render();
      grid.render();
      queue.render();
      return;
    }
    let t = performance.now();
    header.render();
    parts.header.push(performance.now() - t);
    t = performance.now();
    grid.render();
    parts.grid.push(performance.now() - t);
    t = performance.now();
    queue.render();
    parts.queue.push(performance.now() - t);
    for (const k of Object.keys(parts)) if ((parts[k]?.length ?? 0) > 200) parts[k]?.shift();
  });

  // Extension point for other roles (history / commands / audio modules)
  const pre = /** @type {any} */ (window).QorgauClassPanelModules;
  /** @type {any} */ (window).QorgauClassPanel = Object.freeze({
    version: PANEL_VERSION,
    contract: CONTRACT,
    mode: adapter.kind,
    registerStudentModule: (/** @type {any} */ m) => registry.register({ ...m, builtin: false }),
  });
  if (Array.isArray(pre)) for (const m of pre) {
    try {
      registry.register({ ...m, builtin: false });
    } catch (err) {
      console.error("class-panel: module rejected", err);
    }
  }

  adapter.start(store);
  const ticker = setInterval(() => store.tick(), 1000);
  window.addEventListener("pagehide", () => {
    clearInterval(ticker);
    adapter.stop();
    grid.destroy();
    sessionBar.destroy();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && !drawer.openId && !(e.target instanceof HTMLInputElement) && !(e.target instanceof HTMLSelectElement)) {
      e.preventDefault();
      header.searchInput.focus();
    }
  });
  if (cfg.debug) {
    /** @type {any} */ (window).__classPanel = {
      store,
      adapter,
      parts,
      metrics: () => ({
        cards: grid.count,
        flushCount: store.flushCount,
        lastFlushMs: store.lastFlushMs,
        domNodes: document.getElementsByTagName("*").length,
      }),
    };
  }
}

main().catch((err) => {
  console.error(err);
  const root = document.getElementById("app");
  if (root) root.textContent = "Панель не запустилась. Подробности — в консоли разработчика.";
});
