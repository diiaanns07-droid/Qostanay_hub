// @ts-check
// DEV host page for the T03 module (not the T02 panel): PIN login, class session, student list,
// and the review module mounted with the same ctx shape the T02 registry passes.
import { createReviewModule } from "./review-module.js";

const app = /** @type {HTMLElement} */ (document.getElementById("dev-app"));
const mod = createReviewModule();
/** @type {(() => void) | null} */
let unmount = null;
/** @type {string|null} */
let current = null;

function el(tag, attrs = {}, children = []) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v);
  }
  for (const c of children) e.append(c);
  return e;
}

async function call(path, init) {
  const r = await fetch(path, { credentials: "same-origin", ...init });
  const body = await r.json().catch(() => null);
  return { ok: r.ok, status: r.status, body };
}

function loginView(message) {
  const pin = el("input", { type: "password", inputmode: "numeric", autocomplete: "off", "aria-label": "PIN преподавателя", maxlength: "12" });
  const msg = el("p", { role: "status" }, [message ?? ""]);
  const form = el("form", { class: "t3-login" }, [el("label", {}, ["PIN преподавателя (печатается в консоли сервера): ", pin]), el("button", { type: "submit" }, ["Войти"]), msg]);
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const r = await call("/api/teacher/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ pin: pin.value }) });
    if (r.ok) start();
    else msg.textContent = r.body?.error?.message_ru ?? `Ошибка ${r.status}`;
  });
  app.replaceChildren(form);
}

async function start() {
  const r = await call("/api/teacher/students");
  if (r.status === 401) return loginView();
  if (!r.ok) {
    app.replaceChildren(el("p", {}, [r.body?.error?.message_ru ?? `Ошибка ${r.status}`]));
    return;
  }
  const code = el("p", { class: "t3-code", role: "status" });
  const students = el("ul", { class: "t3-students", "aria-label": "Студенты" });
  const pane = el("div", { class: "t3-pane" }, [el("p", { class: "t3-muted" }, ["Выберите студента."])]);
  const newSession = el("button", { type: "button", onclick: async () => {
    const s = await call("/api/teacher/session", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: "DEV", mode: "app", allowed_urls: [], allowed_apps: [] }) });
    code.textContent = s.ok ? `Код подключения: ${s.body.join_code}` : `Ошибка: ${s.body?.error?.message_ru ?? s.status}`;
  } }, ["Начать сессию класса"]);
  app.replaceChildren(el("div", { class: "t3-dev-layout" }, [el("aside", {}, [newSession, code, el("h2", {}, ["Студенты"]), students]), pane]));

  async function refresh() {
    const res = await call("/api/teacher/students");
    if (res.ok && Array.isArray(res.body)) {
      students.replaceChildren(
        ...res.body.map((s) => {
          const label = `${s.student_label || s.computer_name || s.student_id} — эпизодов ${s.incidents_total}, не проверено ${s.incidents_unreviewed}${s.connected ? "" : " (нет связи)"}`;
          return el("li", {}, [el("button", { type: "button", "aria-pressed": s.student_id === current ? "true" : "false", "data-student": s.student_id, onclick: () => open(s.student_id) }, [label])]);
        }),
      );
    }
    setTimeout(refresh, 2000);
  }

  function open(id) {
    if (unmount) unmount();
    current = id;
    const body = el("div", { class: "slot-body", "data-module": mod.id });
    pane.replaceChildren(el("h2", {}, [mod.title]), body);
    unmount = mod.mount(body, { studentId: id, mode: "real", getStudent: () => null, subscribe: () => () => {}, getIncidents: async () => ({ ok: false, error: "n/a" }) }) ?? null;
  }

  refresh();
}

start();
