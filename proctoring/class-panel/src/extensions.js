// @ts-check
// Extension points of the student card (T02). Other roles (history, commands, audio) plug modules in here
// instead of editing the panel. No baseline extension API existed (T01 not published), so this is a LOCAL,
// minimal registry; see HANDOFF.md for the request to make it shared.
//
//   window.QorgauClassPanel.registerStudentModule({
//     id: "c1-commands", slot: "commands", title: "Команды",
//     mount(el, ctx) { ...; return () => {/* unmount */} },
//   });
//
// ctx = { studentId, mode: "demo"|"real", getStudent(): StudentView|null, subscribe(fn): unsubscribe,
//         getIncidents(): Promise<{ok:true, incidents: unknown[]} | {ok:false, error:string}> }
// A module only gets read access through ctx; transport for commands/audio is the module's own business
// and must use the qorgau.class.v1 teacher endpoints.

/** @typedef {"history"|"commands"|"audio"} Slot */
/** @typedef {import("./model.js").StudentView} StudentView */
/**
 * @typedef {object} ModuleContext
 * @property {string} studentId
 * @property {"demo"|"real"} mode
 * @property {() => StudentView|null} getStudent
 * @property {(fn: () => void) => () => void} subscribe
 * @property {() => Promise<{ok: true, incidents: unknown[]} | {ok: false, error: string}>} getIncidents
 */
/**
 * @typedef {object} StudentModule
 * @property {string} id
 * @property {Slot} slot
 * @property {string} title
 * @property {(el: HTMLElement, ctx: ModuleContext) => (void | (() => void))} mount
 * @property {boolean} [builtin]
 */

export const SLOTS = /** @type {const} */ (["history", "commands", "audio"]);
export const SLOT_TITLE = { history: "История", commands: "Команды", audio: "Аудиосвязь" };
const ID_RE = /^[a-z0-9][a-z0-9._-]{0,63}$/;

export class ModuleRegistry {
  constructor() {
    /** @type {StudentModule[]} */
    this.modules = [];
    /** @type {Set<() => void>} */
    this.listeners = new Set();
  }

  /** @param {StudentModule} m */
  register(m) {
    if (!m || typeof m !== "object") throw new TypeError("module must be an object");
    if (!ID_RE.test(String(m.id))) throw new TypeError("module.id must match ^[a-z0-9][a-z0-9._-]{0,63}$");
    if (!SLOTS.includes(/** @type {Slot} */ (m.slot))) throw new TypeError(`module.slot must be one of ${SLOTS.join(", ")}`);
    if (typeof m.mount !== "function") throw new TypeError("module.mount must be a function");
    const title = typeof m.title === "string" && m.title.trim() ? m.title.trim().slice(0, 60) : SLOT_TITLE[m.slot];
    this.modules = this.modules.filter((x) => x.id !== m.id);
    this.modules.push({ id: m.id, slot: m.slot, title, mount: m.mount, builtin: !!m.builtin });
    this.listeners.forEach((l) => l());
    return () => this.unregister(m.id);
  }

  /** @param {string} id */
  unregister(id) {
    this.modules = this.modules.filter((x) => x.id !== id);
    this.listeners.forEach((l) => l());
  }

  /** External modules replace the built-in one of the same slot. @param {Slot} slot */
  forSlot(slot) {
    const all = this.modules.filter((m) => m.slot === slot);
    const external = all.filter((m) => !m.builtin);
    return external.length ? external : all;
  }

  /** @param {() => void} fn */
  onChange(fn) {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }
}
