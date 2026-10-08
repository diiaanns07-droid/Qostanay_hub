// Minimal DOM for unit tests (no dependencies). Supports what src/dom.js, t02-module.js need:
// elements, text nodes, attributes, classList, events with bubbling, simple selectors.
// textContent is the only way text gets in — there is deliberately NO innerHTML here: a module that tried
// to use it would fail the tests.

class FakeNode {
  constructor(doc) {
    this.ownerDocument = doc;
    this.parentNode = null;
    this.childNodes = [];
  }
  get firstChild() {
    return this.childNodes[0] ?? null;
  }
  appendChild(c) {
    if (c.parentNode) c.parentNode.removeChild(c);
    c.parentNode = this;
    this.childNodes.push(c);
    return c;
  }
  insertBefore(c, ref) {
    if (!ref) return this.appendChild(c);
    if (c.parentNode) c.parentNode.removeChild(c);
    const i = this.childNodes.indexOf(ref);
    c.parentNode = this;
    this.childNodes.splice(i < 0 ? this.childNodes.length : i, 0, c);
    return c;
  }
  removeChild(c) {
    const i = this.childNodes.indexOf(c);
    if (i >= 0) this.childNodes.splice(i, 1);
    c.parentNode = null;
    return c;
  }
  remove() {
    if (this.parentNode) this.parentNode.removeChild(this);
  }
  get textContent() {
    return this.childNodes.map((c) => c.textContent).join("");
  }
  set textContent(v) {
    for (const c of this.childNodes) c.parentNode = null;
    this.childNodes = [];
    const s = String(v ?? "");
    if (s) this.appendChild(new FakeText(this.ownerDocument, s));
  }
}

export class FakeText extends FakeNode {
  constructor(doc, data) {
    super(doc);
    this.nodeType = 3;
    this.data = String(data);
  }
  get textContent() {
    return this.data;
  }
  set textContent(v) {
    this.data = String(v);
  }
}

export class FakeElement extends FakeNode {
  constructor(doc, tag, ns = null) {
    super(doc);
    this.nodeType = 1;
    this.namespaceURI = ns;
    this.tagName = ns ? tag : tag.toUpperCase();
    this.localName = tag.toLowerCase();
    this.attributes = new Map();
    this.listeners = new Map();
    this.value = "";
    this.checked = false;
    this.indeterminate = false;
    const self = this;
    this.classList = {
      _list: () => (self.getAttribute("class") ?? "").split(/\s+/).filter(Boolean),
      contains: (c) => self.classList._list().includes(c),
      add: (c) => !self.classList.contains(c) && self.setAttribute("class", [...self.classList._list(), c].join(" ")),
      remove: (c) => self.setAttribute("class", self.classList._list().filter((x) => x !== c).join(" ")),
      toggle: (c, on) => {
        const want = on === undefined ? !self.classList.contains(c) : !!on;
        if (want) self.classList.add(c);
        else self.classList.remove(c);
        return want;
      },
    };
  }
  setAttribute(k, v) {
    this.attributes.set(k, String(v));
    if (k === "value") this.value = String(v);
    if (k === "checked") this.checked = true;
  }
  getAttribute(k) {
    return this.attributes.has(k) ? this.attributes.get(k) : null;
  }
  hasAttribute(k) {
    return this.attributes.has(k);
  }
  removeAttribute(k) {
    this.attributes.delete(k);
  }
  toggleAttribute(k, on) {
    const want = on === undefined ? !this.hasAttribute(k) : !!on;
    if (want) this.setAttribute(k, "");
    else this.removeAttribute(k);
    return want;
  }
  get className() {
    return this.getAttribute("class") ?? "";
  }
  set className(v) {
    this.setAttribute("class", v);
  }
  get id() {
    return this.getAttribute("id") ?? "";
  }
  get disabled() {
    return this.hasAttribute("disabled");
  }
  set disabled(v) {
    this.toggleAttribute("disabled", !!v);
  }
  get hidden() {
    return this.hasAttribute("hidden");
  }
  set hidden(v) {
    this.toggleAttribute("hidden", !!v);
  }
  get children() {
    return this.childNodes.filter((c) => c.nodeType === 1);
  }
  append(...nodes) {
    for (const n of nodes) this.appendChild(typeof n === "string" ? new FakeText(this.ownerDocument, n) : n);
  }
  replaceChildren(...nodes) {
    for (const c of this.childNodes) c.parentNode = null;
    this.childNodes = [];
    this.append(...nodes);
  }
  addEventListener(type, fn) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(fn);
  }
  removeEventListener(type, fn) {
    this.listeners.set(type, (this.listeners.get(type) ?? []).filter((f) => f !== fn));
  }
  dispatchEvent(ev) {
    ev.target ??= this;
    let el = this;
    while (el && !ev._stopped) {
      for (const fn of el.listeners?.get(ev.type) ?? []) fn(ev);
      el = el.parentNode;
    }
    return !ev.defaultPrevented;
  }
  click() {
    if (this.disabled) return;
    this.dispatchEvent(makeEvent("click"));
  }
  focus() {
    this.ownerDocument.activeElement = this;
  }
  closest(sel) {
    let el = this;
    while (el && el.nodeType === 1) {
      if (matches(el, sel)) return el;
      el = el.parentNode;
    }
    return null;
  }
  querySelectorAll(sel) {
    const out = [];
    const walk = (n) => {
      for (const c of n.childNodes) {
        if (c.nodeType === 1) {
          if (sel.split(",").some((s) => matches(c, s.trim()))) out.push(c);
          walk(c);
        }
      }
    };
    walk(this);
    return out;
  }
  querySelector(sel) {
    return this.querySelectorAll(sel)[0] ?? null;
  }
}

/** @param {string} type */
export function makeEvent(type, extra = {}) {
  return {
    type,
    defaultPrevented: false,
    _stopped: false,
    preventDefault() {
      this.defaultPrevented = true;
    },
    stopPropagation() {
      this._stopped = true;
    },
    ...extra,
  };
}

/** Compound selector: tag? (.class | [attr] | [attr="value"])* */
function matches(el, sel) {
  const m = /^([a-zA-Z0-9-]*)((?:\.[\w-]+|\[[^\]]+\])*)$/.exec(sel);
  if (!m) throw new Error(`fake-dom: unsupported selector ${sel}`);
  if (m[1] && el.localName !== m[1].toLowerCase()) return false;
  const parts = m[2].match(/\.[\w-]+|\[[^\]]+\]/g) ?? [];
  for (const p of parts) {
    if (p.startsWith(".")) {
      if (!el.classList.contains(p.slice(1))) return false;
    } else {
      const a = /^\[([\w-]+)(?:="([^"]*)")?\]$/.exec(p);
      if (!a) throw new Error(`fake-dom: unsupported attribute selector ${p}`);
      if (!el.hasAttribute(a[1])) return false;
      if (a[2] !== undefined && el.getAttribute(a[1]) !== a[2]) return false;
    }
  }
  return true;
}

export function createFakeDocument() {
  const doc = {
    activeElement: null,
    createElement: (tag) => new FakeElement(doc, tag),
    createElementNS: (ns, tag) => new FakeElement(doc, tag, ns),
    createTextNode: (t) => new FakeText(doc, t),
    contains: () => true,
  };
  doc.head = new FakeElement(doc, "head");
  doc.body = new FakeElement(doc, "body");
  doc.querySelector = (sel) => doc.head.querySelector(sel) ?? doc.body.querySelector(sel);
  return doc;
}

/** Install as globalThis.document (src/dom.js uses the global document). */
export function installFakeDocument() {
  const doc = createFakeDocument();
  globalThis.document = doc;
  return doc;
}
