// @ts-check
// Standalone teacher page for the audio module (DEV server). In the product the class panel (T02) hosts the
// same panel in its "Аудиосвязь" slot (see class-panel-module.js).
import { TeacherAudio } from "./teacher-audio.js";
import { TeacherSignaling } from "./teacher-signaling.js";
import { mountAudioPanel } from "./audio-panel.js";

const main = /** @type {HTMLElement} */ (document.getElementById("main"));
const conn = /** @type {HTMLElement} */ (document.getElementById("conn"));
const logoutBtn = /** @type {HTMLButtonElement} */ (document.getElementById("logout"));
const audioEl = /** @type {HTMLAudioElement} */ (document.getElementById("student-audio"));

/** @type {Map<string, {student_id: string, student_label: string, computer_name: string, connected: boolean, mic_active: boolean}>} */
const students = new Map();
let selected = /** @type {string|null} */ (null);
let unmountPanel = /** @type {null | (() => void)} */ (null);

const labelOf = (/** @type {string} */ id) => {
  const s = students.get(id);
  return s ? `${s.student_label || "без имени"} · ${s.computer_name || id}` : id;
};

async function boot() {
  const r = await fetch("/api/teacher/session", { credentials: "same-origin" });
  if (r.status === 401) return showLogin();
  if (!r.ok) {
    main.replaceChildren(text("p", `Сервер ответил ${r.status}. Пульт открывается только на компьютере преподавателя (127.0.0.1).`));
    return;
  }
  const { join_code } = await r.json();
  start(join_code);
}

/** @param {string} tag @param {string} t */
function text(tag, t) {
  const el = document.createElement(tag);
  el.textContent = t;
  return el;
}

function showLogin(/** @type {string} */ msg = "") {
  const form = document.createElement("form");
  form.className = "qa-login";
  const label = document.createElement("label");
  label.textContent = "PIN преподавателя (напечатан в консоли сервера)";
  const input = document.createElement("input");
  input.type = "password";
  input.autocomplete = "off";
  input.inputMode = "numeric";
  input.maxLength = 12;
  input.required = true;
  input.id = "pin";
  label.htmlFor = "pin";
  const btn = document.createElement("button");
  btn.type = "submit";
  btn.className = "qa-btn";
  btn.textContent = "Войти";
  const err = text("p", msg);
  err.className = "qa-problem";
  err.setAttribute("role", "alert");
  err.hidden = !msg;
  form.append(label, input, btn, err);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const r = await fetch("/api/teacher/login", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ pin: input.value }) });
    if (r.ok) return boot();
    const body = await r.json().catch(() => ({}));
    showLogin(body.message_ru ?? `Ошибка ${r.status}`);
  });
  main.replaceChildren(text("h1", "Вход преподавателя"), form);
  input.focus();
}

/** @param {string} joinCode */
function start(joinCode) {
  logoutBtn.hidden = false;
  const sig = new TeacherSignaling();
  const controller = new TeacherAudio({ signaling: sig, audioElement: audioEl });
  if (new URLSearchParams(location.search).get("debug") === "1") Object.assign(window, { __qa: { controller, sig } });

  const list = document.createElement("div");
  list.className = "qa-students";
  list.setAttribute("role", "radiogroup");
  list.setAttribute("aria-label", "Студенты");
  const panelHost = document.createElement("div");
  panelHost.className = "qa-panel-host";
  const info = text("p", `Код подключения для студентов: ${joinCode}`);
  info.className = "qa-join";
  main.replaceChildren(info, text("h2", "Студенты на связи"), list, panelHost);

  const renderList = () => {
    const items = [...students.values()];
    if (!items.length) {
      list.replaceChildren(text("p", "Пока никто не подключился."));
      return;
    }
    list.replaceChildren(
      ...items.map((s) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = `qa-student ${s.student_id === selected ? "qa-sel" : ""}`;
        b.setAttribute("role", "radio");
        b.setAttribute("aria-checked", String(s.student_id === selected));
        b.dataset.studentId = s.student_id;
        b.textContent = `${labelOf(s.student_id)} — ${s.connected ? "на связи" : "нет связи"}${s.mic_active ? " · микрофон включён" : ""}`;
        b.addEventListener("click", () => select(s.student_id));
        return b;
      }),
    );
  };

  const select = (/** @type {string} */ id) => {
    selected = id;
    unmountPanel?.();
    unmountPanel = mountAudioPanel(panelHost, { controller, studentId: id, labelOf, isOnline: () => !!students.get(id)?.connected });
    renderList();
  };

  sig.subscribe((m) => {
    if (m.type === "teacher_hello") {
      students.clear();
      for (const s of m.students ?? []) students.set(s.student_id, s);
      renderList();
    } else if (m.type === "student_update" && m.student) {
      students.set(m.student.student_id, m.student);
      renderList();
      if (selected === m.student.student_id) controller.nudge(); // re-render the panel (online state)
    }
  });
  sig.onStatus((up, code) => {
    conn.textContent = up ? "сервер: на связи" : code === 4401 ? "сервер: вход завершён" : "сервер: нет связи";
    conn.className = `qa-conn ${up ? "qa-up" : "qa-down"}`;
    if (!up && code === 4401) showLogin("Вход завершён. Аудиосвязь закрыта.");
  });
  logoutBtn.onclick = async () => {
    await fetch("/api/teacher/logout", { method: "POST", credentials: "same-origin" });
    controller.dispose();
    sig.close();
    logoutBtn.hidden = true;
    showLogin("Вы вышли. Аудиосвязь закрыта.");
  };
  sig.connect();
}

void boot();
