// @ts-check
// Card grid: keyed elements, DOM moves only when the store's order changes (slow cadence → no jumping),
// FLIP transition for moves (off with prefers-reduced-motion), roving tabindex with arrow keys,
// previews refreshed only for cards in the viewport.
import { h, prefersReducedMotion, setText } from "./dom.js";
import { createCard, updateCard } from "./card.js";

/** @typedef {import("../store.js").PanelStore} PanelStore */
/** @typedef {import("./card.js").CardEls} CardEls */

/**
 * @param {PanelStore} store
 * @param {{ demo: boolean, onOpen: (id: string, from: HTMLElement) => void }} o
 */
export function createGrid(store, o) {
  const list = h("ul", { class: "grid", role: "list", "aria-label": "Студенты класса" });
  const empty = h("div", { class: "grid-empty", role: "status", hidden: true });
  const root = h("section", { class: "grid-wrap", "aria-label": "Карточки студентов" }, [list, empty]);
  /** @type {Map<string, CardEls>} */
  const cards = new Map();
  /** @type {Set<string>} */
  const visible = new Set();
  let showPreview = true;
  /** @type {string|null} */
  let focusId = null;
  /** @type {string[]} */
  let domOrder = [];

  const io =
    typeof IntersectionObserver === "function"
      ? new IntersectionObserver(
          (entries) => {
            for (const e of entries) {
              const id = /** @type {HTMLElement} */ (e.target).dataset.id ?? "";
              if (e.isIntersecting) {
                if (!visible.has(id)) {
                  visible.add(id);
                  store.mark({ card: id });
                }
              } else visible.delete(id);
            }
          },
          { rootMargin: "200px" },
        )
      : null;

  list.addEventListener("click", (ev) => {
    const btn = /** @type {HTMLElement} */ (ev.target).closest(".card-hit");
    if (btn instanceof HTMLElement && btn.dataset.id) {
      setFocus(btn.dataset.id, false);
      o.onOpen(btn.dataset.id, btn);
    }
  });
  list.addEventListener("keydown", (ev) => {
    const keys = ["ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp", "Home", "End"];
    if (!keys.includes(ev.key)) return;
    const shown = domOrder.filter((id) => !cards.get(id)?.root.hidden);
    if (shown.length === 0) return;
    const i = Math.max(0, shown.indexOf(focusId ?? ""));
    const cols = columns();
    let j = i;
    if (ev.key === "ArrowRight") j = i + 1;
    if (ev.key === "ArrowLeft") j = i - 1;
    if (ev.key === "ArrowDown") j = i + cols;
    if (ev.key === "ArrowUp") j = i - cols;
    if (ev.key === "Home") j = 0;
    if (ev.key === "End") j = shown.length - 1;
    j = Math.max(0, Math.min(shown.length - 1, j));
    ev.preventDefault();
    setFocus(/** @type {string} */ (shown[j]), true);
  });

  function columns() {
    const first = list.firstElementChild;
    if (!(first instanceof HTMLElement)) return 1;
    const top = first.offsetTop;
    let n = 0;
    for (const el of list.children) {
      if (!(el instanceof HTMLElement) || el.hidden) continue;
      if (el.offsetTop !== top) break;
      n += 1;
    }
    return Math.max(1, n);
  }

  /** @param {string} id @param {boolean} move */
  function setFocus(id, move) {
    for (const [cid, c] of cards) c.hit.tabIndex = cid === id ? 0 : -1;
    focusId = id;
    if (move) cards.get(id)?.hit.focus();
  }

  /** @param {string[]} order */
  function applyOrder(order) {
    if (order.length === domOrder.length && order.every((id, i) => id === domOrder[i])) return;
    const animate = !prefersReducedMotion() && domOrder.length > 0 && order.length <= 120;
    /** @type {Map<string, DOMRect>} */
    const before = new Map();
    if (animate) for (const id of order) {
      const c = cards.get(id);
      if (c && !c.root.hidden) before.set(id, c.root.getBoundingClientRect());
    }
    const frag = document.createDocumentFragment();
    for (const id of order) {
      const c = cards.get(id);
      if (c) frag.append(c.root);
    }
    list.append(frag);
    domOrder = [...order];
    if (!animate) return;
    for (const [id, r0] of before) {
      const c = cards.get(id);
      if (!c) continue;
      const r1 = c.root.getBoundingClientRect();
      const dx = r0.left - r1.left;
      const dy = r0.top - r1.top;
      if (Math.abs(dx) < 1 && Math.abs(dy) < 1) continue;
      c.root.style.transition = "none";
      c.root.style.transform = `translate(${dx}px, ${dy}px)`;
      requestAnimationFrame(() => {
        c.root.style.transition = "transform 320ms cubic-bezier(.2,.7,.2,1)";
        c.root.style.transform = "";
        c.root.addEventListener("transitionend", () => (c.root.style.transition = ""), { once: true });
      });
    }
  }

  function render() {
    const now = store.now();
    const ids = new Set(store.students.keys());
    for (const [id, c] of cards) {
      if (!ids.has(id)) {
        io?.unobserve(c.root);
        c.root.remove();
        cards.delete(id);
        visible.delete(id);
      }
    }
    for (const id of ids) {
      if (!cards.has(id)) {
        const c = createCard(id);
        cards.set(id, c);
        io?.observe(c.root);
        if (!io) visible.add(id);
      }
    }
    applyOrder(store.order.filter((id) => cards.has(id)));
    const all = store.pending.all;
    let shown = 0;
    for (const id of domOrder) {
      const c = /** @type {CardEls} */ (cards.get(id));
      const match = store.matchesFilter(id);
      if (c.root.hidden === match) c.root.hidden = !match;
      if (match) shown += 1;
      if (!match || !(all || store.pending.cards.has(id))) continue;
      const x = store.derived(id);
      if (!x) continue;
      updateCard(c, x, now, { showPreview, refreshPreview: visible.has(id) || !io, demo: o.demo });
    }
    if (focusId === null || !cards.has(focusId) || cards.get(focusId)?.root.hidden) {
      const firstShown = domOrder.find((id) => !cards.get(id)?.root.hidden);
      if (firstShown) setFocus(firstShown, false);
    }
    const noStudents = store.loaded && store.students.size === 0;
    empty.hidden = !(noStudents || (store.students.size > 0 && shown === 0));
    if (noStudents) setText(empty, "В классе пока нет студентов. Они появятся здесь, когда подключатся к серверу класса по коду сессии.");
    else if (shown === 0) setText(empty, "Нет студентов, подходящих под фильтр или поиск.");
    list.setAttribute("aria-busy", store.loaded ? "false" : "true");
    return shown;
  }

  return {
    root,
    render,
    /** @param {boolean} on */
    setShowPreview(on) {
      showPreview = on;
      list.classList.toggle("compact", !on);
      store.mark({ all: true });
    },
    /** @param {string} id */
    focusCard(id) {
      setFocus(id, true);
    },
    /** element of a card (for focus restore) @param {string} id */
    cardButton(id) {
      return cards.get(id)?.hit ?? null;
    },
    get count() {
      return cards.size;
    },
    destroy() {
      io?.disconnect();
    },
  };
}
