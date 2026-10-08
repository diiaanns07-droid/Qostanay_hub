// @ts-check
// Tiny DOM helpers (no framework). All text goes through text nodes / textContent; there is no innerHTML
// anywhere in this UI (tests/static.test.mjs enforces it). Icons are built with createElementNS from the
// constant shapes below — no markup strings are parsed.

/** @typedef {string|number|boolean|null|undefined|((e: any) => void)} AttrValue */

/**
 * @param {string} tag
 * @param {Record<string, AttrValue>} [attrs]
 * @param {Array<Node|string|null|undefined|false>} [children]
 * @returns {HTMLElement}
 */
export function h(tag, attrs = {}, children = []) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = String(v);
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, String(v));
  }
  append(el, children);
  return el;
}

/** @param {Element} el @param {Array<Node|string|null|undefined|false>} children */
export function append(el, children) {
  for (const c of children) {
    if (c === null || c === undefined || c === false) continue;
    el.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
}

/** @param {Element} el @param {Array<Node|string|null|undefined|false>} children */
export function replace(el, children) {
  while (el.firstChild) el.removeChild(el.firstChild);
  append(el, children);
}

/** Set text only when it changed. @param {Element} el @param {string} text */
export function setText(el, text) {
  if (el.textContent !== text) el.textContent = text;
}

/** @param {Element} el @param {string} name @param {string|null} value */
export function setAttr(el, name, value) {
  if (value === null) {
    if (el.hasAttribute(name)) el.removeAttribute(name);
  } else if (el.getAttribute(name) !== value) el.setAttribute(name, value);
}

/** @param {Element} el @param {boolean} on */
export function setDisabled(el, on) {
  setAttr(el, "disabled", on ? "" : null);
}

/** @param {Element} el @param {boolean} on */
export function setHidden(el, on) {
  setAttr(el, "hidden", on ? "" : null);
}

let uid = 0;
/** @param {string} prefix */
export const nextId = (prefix) => `${prefix}-${++uid}`;

const SVG_NS = "http://www.w3.org/2000/svg";
const STROKE = { fill: "none", stroke: "currentColor", "stroke-width": "1.6", "stroke-linecap": "round", "stroke-linejoin": "round" };

/** @type {Record<string, Array<[string, Record<string, string>]>>} */
const SHAPES = {
  clock: [["circle", { cx: "8", cy: "8", r: "6.3", ...STROKE }], ["path", { d: "M8 4.6V8l2.4 1.6", ...STROKE }]],
  run: [["path", { d: "M13.4 8.6A5.5 5.5 0 1 1 11.9 4", ...STROKE }], ["path", { d: "M12.3 1.6v2.8H9.5", ...STROKE }]],
  check: [["circle", { cx: "8", cy: "8", r: "6.6", fill: "currentColor" }], ["path", { d: "M5 8.2l2 2 4-4.4", fill: "none", stroke: "#fff", "stroke-width": "1.9", "stroke-linecap": "round", "stroke-linejoin": "round" }]],
  octagon: [["path", { d: "M5.2 1.5h5.6l3.7 3.7v5.6l-3.7 3.7H5.2l-3.7-3.7V5.2z", fill: "currentColor" }], ["path", { d: "M5.9 5.9l4.2 4.2M10.1 5.9l-4.2 4.2", fill: "none", stroke: "#fff", "stroke-width": "1.8", "stroke-linecap": "round" }]],
  dashed: [["circle", { cx: "8", cy: "8", r: "6.3", ...STROKE, "stroke-dasharray": "2.6 2.2" }], ["path", { d: "M3.5 12.5l9-9", ...STROKE }]],
  slash: [["circle", { cx: "8", cy: "8", r: "6.3", ...STROKE }], ["path", { d: "M4 12L12 4", ...STROKE }]],
  question: [["circle", { cx: "8", cy: "8", r: "6.3", ...STROKE, "stroke-dasharray": "2 2" }], ["path", { d: "M6.3 6.2a1.8 1.8 0 1 1 2.4 1.7c-.5.2-.7.5-.7 1v.4", ...STROKE }], ["circle", { cx: "8", cy: "11.6", r: ".9", fill: "currentColor" }]],
  lock: [["rect", { x: "3", y: "7", width: "10", height: "7.5", rx: "1.5", fill: "currentColor" }], ["path", { d: "M5.3 7V5a2.7 2.7 0 0 1 5.4 0v2", ...STROKE }]],
  unlock: [["rect", { x: "3", y: "7", width: "10", height: "7.5", rx: "1.5", ...STROKE }], ["path", { d: "M5.3 7V5a2.7 2.7 0 0 1 5.2-1", ...STROKE }]],
  hourglass: [["path", { d: "M4.5 1.8h7M4.5 14.2h7M5.2 1.8c0 3.4 5.6 3.2 5.6 6.2s-5.6 2.8-5.6 6.2M10.8 1.8c0 3.4-5.6 3.2-5.6 6.2s5.6 2.8 5.6 6.2", ...STROKE }]],
  online: [["circle", { cx: "8", cy: "8", r: "4.2", fill: "currentColor" }]],
  offline: [["circle", { cx: "8", cy: "8", r: "4.6", ...STROKE }], ["path", { d: "M3 13L13 3", ...STROKE }]],
  warn: [["path", { d: "M8 1.8L15 14H1z", ...STROKE }], ["path", { d: "M8 6v3.6", ...STROKE }], ["circle", { cx: "8", cy: "11.7", r: "1", fill: "currentColor" }]],
  info: [["circle", { cx: "8", cy: "8", r: "6.3", ...STROKE }], ["path", { d: "M8 7.2v4", ...STROKE }], ["circle", { cx: "8", cy: "4.9", r: "1", fill: "currentColor" }]],
  flask: [["path", { d: "M6 1.6h4M6.6 1.6v4.6L2.9 12.6a1.3 1.3 0 0 0 1.1 1.9h8a1.3 1.3 0 0 0 1.1-1.9L9.4 6.2V1.6", ...STROKE }], ["path", { d: "M4.6 10h6.8", ...STROKE }]],
  close: [["path", { d: "M4 4l8 8M12 4l-8 8", ...STROKE, "stroke-width": "1.8" }]],
  plus: [["path", { d: "M8 3v10M3 8h10", ...STROKE, "stroke-width": "1.8" }]],
};

/** Decorative icon (aria-hidden); the text next to it carries the meaning. @param {string} name */
export function icon(name, cls = "ico") {
  const svg = document.createElementNS(SVG_NS, "svg");
  for (const [k, v] of Object.entries({ viewBox: "0 0 16 16", width: "16", height: "16", "aria-hidden": "true", focusable: "false", class: cls })) svg.setAttribute(k, v);
  for (const [tag, attrs] of SHAPES[name] ?? SHAPES.question) {
    const el = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    svg.appendChild(el);
  }
  return svg;
}

/**
 * Status chip: icon (shape) + group text + optional detail. tone -> colour.
 * @param {{icon: string, tone: string, groupLabel?: string, label?: string, group?: string} | null} st
 */
export function statusChip(st, emptyText = "команд не было") {
  if (!st) return h("span", { class: "chip tone-muted", "data-group": "none" }, [emptyText]);
  return h("span", { class: `chip tone-${st.tone}`, "data-group": st.group ?? "" }, [
    icon(st.icon),
    h("span", { class: "chip-group" }, [st.groupLabel ?? ""]),
  ]);
}

/** Keyboard focus trap + Escape for a modal container. @param {HTMLElement} box @param {() => void} onEscape */
export function trapFocus(box, onEscape) {
  box.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      e.preventDefault();
      onEscape();
      return;
    }
    if (e.key !== "Tab") return;
    const f = /** @type {HTMLElement[]} */ ([
      ...box.querySelectorAll("button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])"),
    ]).filter((el) => !el.closest("[hidden]"));
    if (!f.length) return;
    const first = f[0];
    const last = f[f.length - 1];
    if (e.shiftKey && (document.activeElement === first || document.activeElement === box)) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  });
}
