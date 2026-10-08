// @ts-check
// "Требуют внимания": a separate queue built ONLY from delivered data (zone red/yellow from the source,
// no link during an exam, camera not working, grey during an exam). Not a score — each entry says why.
// The teacher can mark an entry "просмотрено": it comes back when something new happens.
// Order is stable: new entries are inserted, the whole list re-sorts only with the grid's slow cadence.
import { ago, displayName } from "../model.js";
import { h, setAttr, setText, svg } from "./dom.js";
import { ICON } from "./icons.js";

/** @typedef {import("../store.js").PanelStore} PanelStore */

/**
 * @param {PanelStore} store
 * @param {{ onOpen: (id: string, from: HTMLElement) => void, announce: (text: string) => void }} o
 */
export function createQueue(store, o) {
  const count = h("span", { class: "q-count" });
  const list = h("ol", { class: "q-list" });
  const empty = h("p", { class: "q-empty" });
  const root = h("aside", { class: "queue", "aria-labelledby": "q-title" }, [
    h("div", { class: "q-head" }, [h("h2", { id: "q-title" }, ["Требуют внимания "]), count]),
    h("p", { class: "q-note" }, ["Очередь строится только по данным: зона из анализа на компьютере студента, связь и камера. Решение принимает преподаватель."]),
    list,
    empty,
  ]);
  /** @type {string[]} */
  let order = [];
  let lastSort = 0;
  /** @type {Map<string, HTMLElement>} */
  const items = new Map();
  let first = true;

  /** @param {string} id */
  function item(id) {
    let li = items.get(id);
    if (li) return li;
    const icon = h("span", { class: "q-ico" });
    const name = h("span", { class: "q-name" });
    const why = h("span", { class: "q-why" });
    const src = h("span", { class: "q-src" });
    const when = h("span", { class: "q-when" });
    const open = h("button", { type: "button", class: "q-open" }, [icon, h("span", { class: "q-text" }, [name, why, src, when])]);
    const ack = h("button", { type: "button", class: "q-ack", title: "Убрать из очереди до нового события" }, ["Просмотрено"]);
    open.addEventListener("click", () => o.onOpen(id, open));
    ack.addEventListener("click", () => {
      store.acknowledge(id);
      // keep keyboard focus inside the queue
      const next = /** @type {HTMLElement|null} */ (li?.nextElementSibling?.querySelector(".q-open") ?? li?.previousElementSibling?.querySelector(".q-open") ?? null);
      queueMicrotask(() => (next ?? /** @type {HTMLElement} */ (root.querySelector("h2"))).focus?.());
    });
    li = h("li", { class: "q-item", "data-id": id }, [open, ack]);
    items.set(id, li);
    return li;
  }

  function render() {
    const now = store.now();
    const current = store.queue();
    const ids = current.map((x) => x.entry.view.id);
    const idSet = new Set(ids);
    const added = ids.filter((id) => !order.includes(id));
    if (store.lastReorderAt !== lastSort) {
      order = ids; // adopt the sorted order together with the grid
      lastSort = store.lastReorderAt;
    } else {
      order = order.filter((id) => idSet.has(id));
      for (const id of added) {
        const pos = ids.indexOf(id);
        // insert next to its sorted neighbour so urgent entries do not land at the bottom
        const before = ids.slice(0, pos).reverse().find((x) => order.includes(x));
        order.splice(before ? order.indexOf(before) + 1 : 0, 0, id);
      }
    }
    for (const [id, li] of items) if (!idSet.has(id)) {
      li.remove();
      items.delete(id);
    }
    const byId = new Map(current.map((x) => [x.entry.view.id, x]));
    order.forEach((id, i) => {
      const x = byId.get(id);
      if (!x) return;
      const li = item(id);
      if (list.children[i] !== li) list.insertBefore(li, list.children[i] ?? null);
      const v = x.entry.view;
      const top = x.reasons.reduce((a, b) => (b.rank < a.rank ? b : a));
      const z = top.key === "zone-red" ? "red" : top.key === "zone-yellow" ? "yellow" : "grey";
      li.className = `q-item z-${z}`;
      const iconEl = /** @type {HTMLElement} */ (li.querySelector(".q-ico"));
      if (iconEl.dataset.k !== z) {
        iconEl.dataset.k = z;
        iconEl.replaceChildren(svg(ICON[z]));
      }
      setText(/** @type {HTMLElement} */ (li.querySelector(".q-name")), `${displayName(v)}${v.computerName && v.computerName !== displayName(v) ? ` · ${v.computerName}` : ""}`);
      setText(/** @type {HTMLElement} */ (li.querySelector(".q-why")), x.reasons.map((r) => r.text).join(" · "));
      // the first reason as delivered by the source (A05 wording), only for red/yellow zones
      setText(/** @type {HTMLElement} */ (li.querySelector(".q-src")), z !== "grey" && x.d.reasons[0] ? x.d.reasons[0] : "");
      setText(/** @type {HTMLElement} */ (li.querySelector(".q-when")), v.lastEventAt ? `последнее событие ${ago(v.lastEventAt, now)}` : "событий нет");
      setAttr(/** @type {HTMLElement} */ (li.querySelector(".q-open")), "aria-label", `${displayName(v)}: ${x.reasons.map((r) => r.text).join(", ")}. Открыть карточку`);
      setAttr(/** @type {HTMLElement} */ (li.querySelector(".q-ack")), "aria-label", `Отметить просмотренным: ${displayName(v)}`);
    });
    setText(count, String(current.length));
    empty.hidden = current.length > 0;
    setText(empty, !store.loaded ? "Загрузка…" : store.students.size === 0 ? "Студентов пока нет." : "Сейчас никто не требует внимания по полученным данным.");
    if (!first && added.length > 0 && store.feedLive) {
      const names = added.map((id) => byId.get(id)).filter(Boolean).map((x) => displayName(/** @type {any} */ (x).entry.view));
      o.announce(names.length === 1 ? `Требует внимания: ${names[0]}` : `В очереди внимания новых: ${names.length}`);
    }
    if (store.loaded) first = false;
  }

  return { root, render };
}
