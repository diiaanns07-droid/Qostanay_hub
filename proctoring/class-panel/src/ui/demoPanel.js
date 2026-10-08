// @ts-check
// DEMO controls — exist only with the DEMO adapter. Every action changes the SIMULATION, never real data.
import { h } from "./dom.js";

/** @param {ReturnType<typeof import("../adapters/demo.js").createDemoAdapter>} adapter @param {(t: string) => void} announce */
export function createDemoPanel(adapter, announce) {
  const d = adapter.demo;
  const body = h("div", { class: "dp-body", hidden: true });
  const toggle = h("button", { type: "button", class: "dp-toggle", "aria-expanded": "false" }, ["DEMO · управление"]);
  toggle.addEventListener("click", () => {
    const open = body.hidden;
    body.hidden = !open;
    toggle.setAttribute("aria-expanded", String(open));
  });
  const count = h("div", { class: "dp-row", role: "group", "aria-label": "Размер класса" });
  for (const n of [0, 2, 30, 100]) {
    const b = h("button", { type: "button", class: "btn btn-small", "data-count": n }, [n === 0 ? "пустой" : String(n)]);
    b.addEventListener("click", () => {
      d.setCount(n);
      announce(`DEMO: класс из ${n} студентов`);
    });
    count.append(b);
  }
  const rate = /** @type {HTMLSelectElement} */ (
    h("select", { id: "dp-rate" }, [h("option", { value: "calm" }, ["редко"]), h("option", { value: "normal", selected: true }, ["обычно"]), h("option", { value: "busy" }, ["часто"])])
  );
  rate.addEventListener("change", () => d.setRate(/** @type {any} */ (rate.value)));
  const btn = (/** @type {string} */ label, /** @type {() => void} */ fn) => {
    const b = h("button", { type: "button", class: "btn btn-small" }, [label]);
    b.addEventListener("click", fn);
    return b;
  };
  const serverDown = h("button", { type: "button", class: "btn btn-small toggle", "aria-pressed": "false" }, ["Потеря связи с сервером"]);
  serverDown.addEventListener("click", () => {
    const on = serverDown.getAttribute("aria-pressed") !== "true";
    serverDown.setAttribute("aria-pressed", String(on));
    d.setServerDown(on);
  });
  body.append(
    h("p", { class: "dp-note" }, ["Имитация для проверки панели. Не реальные студенты и не реальная производительность камер."]),
    h("div", { class: "dp-label" }, ["Студентов"]),
    count,
    h("label", { class: "field", for: "dp-rate" }, [h("span", {}, ["События"]), rate]),
    h("div", { class: "dp-row" }, [
      btn("Отключить случайного студента", () => {
        const who = d.disconnectOne();
        announce(who ? `DEMO: ${who} отключён на 45 с` : "DEMO: некого отключать");
      }),
      serverDown,
      btn("Событие у первого студента", () => d.burst("")),
      btn("Ошибка при следующей загрузке", () => d.failNextLoad()),
    ]),
  );
  return h("aside", { class: "demo-panel", "aria-label": "Управление имитацией DEMO" }, [toggle, body]);
}
