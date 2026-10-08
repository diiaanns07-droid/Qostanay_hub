// @ts-check
// SIMULATOR controls (DEV server only): behaviour and online state of each simulated student client.
// Labelled as a simulator everywhere; nothing here reaches a real computer.
import { h, icon, replace, setText } from "../dom.js";
import { EXAM_STATE_RU, SIM_BEHAVIOUR_RU, str } from "../model.js";

/** @param {any} ctx */
export function createSimulatorTab(ctx) {
  const { api, store, announce } = ctx;
  const tbody = h("tbody");
  const status = h("p", { class: "muted", role: "status", "data-testid": "sim-status" });
  const root = h("div", { class: "sim-tab", "data-testid": "sim-panel" }, [
    h("h2", { class: "panel-title" }, [icon("flask", "ico ico-l"), "Симулятор: поведение клиента"]),
    h("p", { class: "sim-warning", role: "note" }, [
      "Это СИМУЛЯТОР студенческих клиентов для проверки интерфейса: успех, задержка, ошибка, отключение. Не реальные компьютеры — экран студента не блокируется, ограничения не включаются.",
    ]),
    status,
    h("div", { class: "table-wrap" }, [
      h("table", { class: "sim-table" }, [
        h("thead", {}, [h("tr", {}, ["Симулированный студент", "Поведение клиента", "В сети", "Состояние в симуляторе"].map((t) => h("th", { scope: "col" }, [t])))]),
        tbody,
      ]),
    ]),
  ]);

  /** @type {Map<string, {tr: HTMLElement, sel: HTMLSelectElement, online: HTMLInputElement, state: HTMLElement, log: HTMLElement}>} */
  const rows = new Map();

  /** @param {any} st */
  function createRow(st) {
    const sid = str(st.student_id);
    const selId = `sim-beh-${sid}`;
    const onId = `sim-on-${sid}`;
    const sel = /** @type {HTMLSelectElement} */ (h("select", { id: selId, "data-sim-behaviour": sid }));
    const online = /** @type {HTMLInputElement} */ (h("input", { type: "checkbox", id: onId, role: "switch", "data-sim-online": sid }));
    const state = h("span", {});
    const log = h("ul", { class: "sim-log" });
    sel.addEventListener("change", async () => {
      const r = await api.simBehaviour(sid, sel.value);
      setText(status, r.ok ? `Симулятор: ${sid} → «${SIM_BEHAVIOUR_RU[/** @type {"success"} */ (sel.value)] ?? sel.value}»` : `Симулятор: ошибка — ${r.error.message}`);
      announce(status.textContent ?? "");
      ctx.pollNow();
    });
    online.addEventListener("change", async () => {
      const r = await api.simOnline(sid, online.checked);
      setText(status, r.ok ? `Симулятор: ${sid} ${online.checked ? "подключён" : "отключён"}` : `Симулятор: ошибка — ${r.error.message}`);
      announce(status.textContent ?? "");
      ctx.pollNow();
    });
    const tr = h("tr", { "data-sim-student": sid }, [
      h("th", { scope: "row" }, [str(st.label)]),
      h("td", {}, [h("label", { class: "sr-only", for: selId }, [`Поведение клиента: ${str(st.label)}`]), sel]),
      h("td", {}, [h("label", { class: "check", for: onId }, [online, h("span", {}, ["в сети"])])]),
      h("td", {}, [state, log]),
    ]);
    return { tr, sel, online, state, log };
  }

  /** @param {import("../app.js").AppState} s */
  function render(s) {
    if (s.tab !== "sim" || !s.sim) return;
    const behaviours = Array.isArray(s.sim.behaviours) ? s.sim.behaviours.map(str) : Object.keys(SIM_BEHAVIOUR_RU);
    const list = Array.isArray(s.sim.students) ? s.sim.students : [];
    for (const st of list) {
      const sid = str(st.student_id);
      let r = rows.get(sid);
      if (!r) {
        r = createRow(st);
        rows.set(sid, r);
        tbody.appendChild(r.tr);
        replace(r.sel, behaviours.map((/** @type {string} */ b) => h("option", { value: b }, [SIM_BEHAVIOUR_RU[/** @type {"success"} */ (b)] ?? b])));
      }
      if (document.activeElement !== r.sel && r.sel.value !== str(st.behaviour)) r.sel.value = str(st.behaviour);
      if (document.activeElement !== r.online) r.online.checked = !!st.connected;
      setText(r.state, `${st.connected ? "в сети" : "не в сети"} · экран ${st.locked ? "заблокирован (в симуляторе)" : "не заблокирован (в симуляторе)"} · этап: ${EXAM_STATE_RU[/** @type {"idle"} */ (st.exam_state)] ?? str(st.exam_state)}`);
      replace(r.log, (Array.isArray(st.log) ? st.log.slice(-3) : []).map((/** @type {unknown} */ x) => h("li", {}, [str(x)])));
    }
  }

  return { root, render };
}
