// @ts-check
// Tiny DOM helpers (no framework). Text is always set through textContent — never innerHTML with data.

/**
 * @param {string} tag
 * @param {Record<string, string|number|boolean|null|undefined|((e: any) => void)>} [attrs]
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
  for (const c of children) {
    if (c === null || c === undefined || c === false) continue;
    el.append(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return el;
}

/** Set text only when it changed (avoids layout work on every tick). @param {Element} el @param {string} text */
export function setText(el, text) {
  if (el.textContent !== text) el.textContent = text;
}

/** @param {Element} el @param {string} name @param {string|null} value */
export function setAttr(el, name, value) {
  if (value === null) {
    if (el.hasAttribute(name)) el.removeAttribute(name);
  } else if (el.getAttribute(name) !== value) el.setAttribute(name, value);
}

/** @param {Element} el @param {string} cls @param {boolean} on */
export function toggleClass(el, cls, on) {
  if (el.classList.contains(cls) !== on) el.classList.toggle(cls, on);
}

/** Static SVG markup from this file only (no data interpolated). @param {string} markup */
export function svg(markup) {
  const t = document.createElement("template");
  t.innerHTML = markup.trim();
  return /** @type {Element} */ (t.content.firstElementChild);
}

export function prefersReducedMotion() {
  return typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
}
