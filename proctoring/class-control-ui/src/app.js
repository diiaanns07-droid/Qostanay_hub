// @ts-check
// Qorgau Class — exam conditions and teacher commands (T04). Entry point.
//
// Data flow: GET /meta once; GET /exams + GET /exams/{id} on load / teacher switch / every 5 s;
// GET /exams/{id}/students every poll (1 s by default; ?poll_ms= for tests). Nothing is inferred locally:
// command badges and the lock column show exactly what the server reports from the client's answers.
import { createApi } from "./api.js";
import { h, icon, replace, setHidden, setText } from "./dom.js";
import { effectiveRole, ROLE_RU, str } from "./model.js";
import { createExamTab } from "./ui/examTab.js";
import { createJournalTab } from "./ui/journalTab.js";
import { createPoliciesTab } from "./ui/policiesTab.js";
import { createSimulatorTab } from "./ui/simulatorTab.js";
import { createStudentsTab } from "./ui/studentsTab.js";

const LS_TEACHER = "qorgau.t04.devTeacher";
const LS_EXAM = "qorgau.t04.examId";

/** @param {string} key */
function lsGet(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}
/** @param {string} key @param {string|null} value */
function lsSet(key, value) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    /* storage unavailable: only a convenience */
  }
}

/**
 * @typedef {object} AppState
 * @property {any} meta
 * @property {any} sim             /sim/info (DEV simulator) or null
 * @property {Record<string, {name: string, role: string}>} teachers
 * @property {string|null} teacher DEV teacher id (X-Qorgau-Dev-Teacher) or null (real server: PIN cookie)
 * @property {any[]} exams
 * @property {any} examsError
 * @property {string|null} examId
 * @property {any} exam
 * @property {any} examError
 * @property {string} role
 * @property {any[]} students
 * @property {Date|null} studentsAt
 * @property {any} studentsError
 * @property {{down: boolean, since: Date|null}} net
 * @property {Set<string>} selected
 * @property {Map<string, any>} posted
 * @property {string} tab
 * @property {number} pollMs
 */

export function createStore() {
  /** @type {AppState} */
  const state = {
    meta: null,
    sim: null,
    teachers: {},
    teacher: null,
    exams: [],
    examsError: null,
    examId: null,
    exam: null,
    examError: null,
    role: "unknown",
    students: [],
    studentsAt: null,
    studentsError: null,
    net: { down: false, since: null },
    selected: new Set(),
    posted: new Map(),
    tab: "students",
    pollMs: 1000,
  };
  /** @type {Set<(s: AppState) => void>} */
  const listeners = new Set();
  return {
    state,
    /** @param {Partial<AppState>} patch */
    set(patch) {
      Object.assign(state, patch);
      for (const fn of listeners) fn(state);
    },
    /** @param {(s: AppState) => void} fn */
    subscribe(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
  };
}
/** @typedef {ReturnType<typeof createStore>} Store */

async function main() {
  const root = /** @type {HTMLElement} */ (document.getElementById("app"));
  const q = new URLSearchParams(location.search);
  const store = createStore();
  const pollQ = Number(q.get("poll_ms"));
  store.state.pollMs = Number.isFinite(pollQ) && pollQ >= 200 && pollQ <= 10000 ? pollQ : 1000;

  const api = createApi({ getTeacher: () => store.state.teacher });

  // ---------------------------------------------------------------- simulator + meta
  const simRes = await api.simInfo();
  const sim = simRes.ok && simRes.data && simRes.data.simulator === true ? simRes.data : null;
  if (sim) {
    const teachers = sim.teachers && typeof sim.teachers === "object" ? sim.teachers : {};
    const wanted = q.get("teacher") ?? lsGet(LS_TEACHER);
    const teacher = wanted && teachers[wanted] ? wanted : teachers["t-aigerim"] ? "t-aigerim" : Object.keys(teachers)[0] ?? null;
    store.set({ sim, teachers, teacher });
  }
  const metaRes = await api.meta();
  store.set({ meta: metaRes.ok ? metaRes.data : null });

  // ---------------------------------------------------------------- live region
  const live = h("div", { class: "sr-only", role: "status", "aria-live": "polite" });
  /** @param {string} text */
  const announce = (text) => {
    setText(live, "");
    setTimeout(() => setText(live, text), 30);
  };

  // ---------------------------------------------------------------- header
  const simBanner = sim
    ? h("div", { class: "sim-banner", role: "note", "data-testid": "sim-banner" }, [
        icon("flask", "ico ico-l"),
        h("div", {}, [
          h("strong", {}, ["СИМУЛЯТОР"]),
          " — студенты имитированы, это не реальные компьютеры. Команды получает только симулятор; на экранах студентов ничего не происходит.",
        ]),
      ])
    : null;

  const teacherSelect = h("select", { id: "dev-teacher", "aria-describedby": "dev-teacher-note" });
  for (const [id, t] of Object.entries(store.state.teachers)) {
    const opt = h("option", { value: id }, [`${str(t.name)} — ${ROLE_RU[/** @type {"teacher"} */ (t.role)] ?? str(t.role)}`]);
    teacherSelect.appendChild(opt);
  }
  if (store.state.teacher) /** @type {HTMLSelectElement} */ (teacherSelect).value = store.state.teacher;
  teacherSelect.addEventListener("change", () => {
    const id = /** @type {HTMLSelectElement} */ (teacherSelect).value;
    lsSet(LS_TEACHER, id);
    store.set({ teacher: id, selected: new Set(), posted: new Map(), students: [], studentsAt: null, exam: null, examError: null });
    announce(`Преподаватель (DEV): ${store.state.teachers[id]?.name ?? id}`);
    reloadAll();
  });
  const teacherBox = sim
    ? h("div", { class: "dev-teacher" }, [
        h("label", { for: "dev-teacher" }, ["Преподаватель (DEV)"]),
        teacherSelect,
        h("span", { id: "dev-teacher-note", class: "sub" }, ["только в DEV-сервере; на сервере класса вход по PIN"]),
      ])
    : null;
  const roleText = h("span", { class: "role", "data-testid": "role" });
  const serverState = h("span", { class: "server-state", "data-testid": "server-state" });

  const header = h("header", { class: "top" }, [
    h("div", { class: "brand" }, [h("span", { class: "brand-mark" }, ["Qorgau Class"]), h("h1", {}, ["Экзамен и команды преподавателя"])]),
    h("div", { class: "top-right" }, [teacherBox, roleText, serverState]),
  ]);

  // ---------------------------------------------------------------- network banner
  const netText = h("span");
  const netRetry = h("button", { type: "button", class: "btn btn-small" }, ["Повторить сейчас"]);
  netRetry.addEventListener("click", () => {
    announce("Повторяем запрос к серверу");
    reloadAll();
  });
  const netBanner = h("div", { class: "net-banner", role: "alert", hidden: true, "data-testid": "net-banner" }, [icon("warn"), netText, netRetry]);

  // ---------------------------------------------------------------- exam bar
  const examSelect = h("select", { id: "exam-select" });
  examSelect.addEventListener("change", () => selectExam(/** @type {HTMLSelectElement} */ (examSelect).value || null));
  const newExamBtn = h("button", { type: "button", class: "btn", "data-testid": "new-exam" }, [icon("plus"), "Создать экзамен"]);
  const newExamWhy = h("span", { class: "why", hidden: true });
  const examMeta = h("p", { class: "exam-meta", "data-testid": "exam-meta" });
  const examBar = h("section", { class: "exam-bar", "aria-label": "Выбор экзамена" }, [
    h("div", { class: "exam-pick" }, [h("label", { for: "exam-select" }, ["Экзамен"]), examSelect, newExamBtn, newExamWhy]),
    examMeta,
  ]);
  const accessPanel = h("div", { class: "access-panel", role: "alert", hidden: true, "data-testid": "access-panel" });

  // ---------------------------------------------------------------- tabs
  const ctx = { api, store, announce, pollNow: () => pollStudents(true), reloadExam: () => loadExam(), reloadExams: () => loadExams(), selectExam, showTab };
  const tabs = [
    { id: "students", title: "Студенты и команды", view: createStudentsTab(ctx) },
    { id: "exam", title: "Условия экзамена", view: createExamTab(ctx) },
    { id: "policies", title: "Политики", view: createPoliciesTab(ctx) },
    { id: "journal", title: "Журнал", view: createJournalTab(ctx) },
    ...(sim ? [{ id: "sim", title: "Симулятор", view: createSimulatorTab(ctx) }] : []),
  ];
  const tablist = h("div", { class: "tabs", role: "tablist", "aria-label": "Разделы панели" });
  /** @type {Record<string, {tab: HTMLElement, panel: HTMLElement}>} */
  const tabEls = {};
  for (const t of tabs) {
    const tab = h("button", { type: "button", role: "tab", id: `tab-${t.id}`, "aria-controls": `panel-${t.id}`, "data-tab": t.id }, [t.title]);
    tab.addEventListener("click", () => showTab(t.id));
    tab.addEventListener("keydown", (e) => {
      const i = tabs.findIndex((x) => x.id === t.id);
      let j = -1;
      if (e.key === "ArrowRight") j = (i + 1) % tabs.length;
      else if (e.key === "ArrowLeft") j = (i - 1 + tabs.length) % tabs.length;
      else if (e.key === "Home") j = 0;
      else if (e.key === "End") j = tabs.length - 1;
      if (j >= 0) {
        e.preventDefault();
        showTab(tabs[j].id);
        tabEls[tabs[j].id].tab.focus();
      }
    });
    const panel = h("section", { role: "tabpanel", id: `panel-${t.id}`, "aria-labelledby": `tab-${t.id}`, tabindex: "-1", class: "panel" }, [t.view.root]);
    tablist.appendChild(tab);
    tabEls[t.id] = { tab, panel };
  }

  /** @param {string} id */
  function showTab(id) {
    if (!tabEls[id]) return;
    store.set({ tab: id });
  }

  newExamBtn.addEventListener("click", () => {
    showTab("exam");
    tabs.find((t) => t.id === "exam")?.view.startCreate?.();
  });

  replace(root, [
    h("a", { class: "skip", href: "#main" }, ["К содержимому"]),
    simBanner,
    header,
    netBanner,
    h("main", { id: "main", tabindex: "-1" }, [examBar, accessPanel, tablist, ...Object.values(tabEls).map((x) => x.panel)]),
    live,
  ]);

  // ---------------------------------------------------------------- rendering of the frame
  store.subscribe(renderFrame);
  for (const t of tabs) store.subscribe((s) => t.view.render(s));

  /** @param {AppState} s */
  function renderFrame(s) {
    for (const t of tabs) {
      const on = s.tab === t.id;
      tabEls[t.id].tab.setAttribute("aria-selected", on ? "true" : "false");
      tabEls[t.id].tab.setAttribute("tabindex", on ? "0" : "-1");
      setHidden(tabEls[t.id].panel, !on);
    }
    const tName = s.teacher ? s.teachers[s.teacher]?.name ?? s.teacher : null;
    setText(roleText, s.exam ? `Ваша роль в экзамене: ${ROLE_RU[/** @type {"teacher"} */ (s.role)] ?? s.role}` : tName ? "" : "Вход преподавателя: PIN сервера класса");
    serverState.replaceChildren(icon(s.net.down ? "offline" : "online", "ico ico-s"), document.createTextNode(s.net.down ? "Сервер: нет связи" : "Сервер: на связи"));
    serverState.className = `server-state ${s.net.down ? "tone-warn" : "tone-ok"}`;
    setHidden(netBanner, !s.net.down);
    setText(
      netText,
      s.net.down
        ? `Нет связи с сервером класса${s.net.since ? ` с ${s.net.since.toLocaleTimeString("ru-RU")}` : ""}. Данные на экране могут быть устаревшими; команды не отправляются. Панель повторяет попытки сама.`
        : "",
    );
    // exam select
    const opts = s.exams.map((e) => ({ id: str(e.exam_id), text: `${str(e.title)} — ${str(e.owner_name)}` }));
    if (s.examId && !opts.some((o) => o.id === s.examId)) opts.unshift({ id: s.examId, text: s.examError ? "Выбранный экзамен (нет доступа)" : "Выбранный экзамен" });
    const sel = /** @type {HTMLSelectElement} */ (examSelect);
    const sig = JSON.stringify(opts);
    if (sel.getAttribute("data-sig") !== sig) {
      sel.replaceChildren(...(opts.length ? opts.map((o) => h("option", { value: o.id }, [o.text])) : [h("option", { value: "" }, ["Нет доступных экзаменов"])]));
      sel.setAttribute("data-sig", sig);
    }
    if (s.examId) sel.value = s.examId;
    const canCreate = !s.teacher || s.teachers[s.teacher]?.role === "teacher";
    newExamBtn.toggleAttribute("disabled", !canCreate);
    setHidden(newExamWhy, canCreate);
    setText(newExamWhy, canCreate ? "" : "Создавать экзамены может только преподаватель");
    if (s.exam) {
      const staff = Object.entries(s.exam.staff || {}).map(([id, r]) => `${id} (${ROLE_RU[/** @type {"teacher"} */ (r)] ?? r})`);
      setText(examMeta, `Владелец: ${str(s.exam.owner_name)} · ревизия ${str(s.exam.revision)} · студентов: ${(s.exam.students || []).length}${staff.length ? ` · сотрудники: ${staff.join(", ")}` : ""}`);
    } else setText(examMeta, s.examsError ? s.examsError.message : s.exams.length === 0 && !s.examId ? "Экзаменов пока нет. Создайте экзамен." : "");
    // access errors
    const err = s.examError;
    setHidden(accessPanel, !err);
    if (err) {
      const who = tName ? ` (${tName})` : "";
      replace(accessPanel, [
        icon(err.status === 403 || err.status === 401 ? "lock" : "warn"),
        h("div", {}, [
          h("strong", {}, [err.status === 403 ? `Нет доступа к этому экзамену${who}` : err.status === 401 ? "Нужен вход преподавателя" : "Экзамен не загружен"]),
          h("p", {}, [`Ответ сервера: «${err.message}». `, err.status === 403 ? "Экзамен ведёт другой преподаватель; попросите владельца добавить вас в состав сотрудников." : err.status === 401 ? "Войдите в панель сервера класса по PIN и обновите страницу." : ""]),
        ]),
      ]);
    }
  }

  // ---------------------------------------------------------------- loading
  async function loadExams() {
    const r = await api.exams();
    if (r.ok && Array.isArray(r.data)) {
      store.set({ exams: r.data, examsError: null });
      markNet(true);
    } else {
      if (r.network) markNet(false);
      store.set({ exams: [], examsError: r.network ? null : r.error });
    }
    return r;
  }

  async function loadExam() {
    const id = store.state.examId;
    if (!id) {
      store.set({ exam: null, examError: null, role: "unknown" });
      return;
    }
    const r = await api.exam(id);
    if (store.state.examId !== id) return;
    if (r.ok) {
      markNet(true);
      const s = store.state;
      store.set({ exam: r.data, examError: null, role: effectiveRole(r.data, s.teacher, s.teacher ? s.teachers[s.teacher]?.role : null) });
    } else if (r.network) markNet(false);
    else store.set({ exam: null, examError: r.error, students: [], role: r.error.status === 403 ? "none" : "unknown" });
  }

  /** @param {string|null} id */
  function selectExam(id) {
    lsSet(LS_EXAM, id);
    store.set({ examId: id, exam: null, examError: null, students: [], studentsAt: null, selected: new Set(), posted: new Map() });
    loadExam().then(() => pollStudents(true));
  }

  async function reloadAll() {
    await loadExams();
    const s = store.state;
    let id = s.examId;
    if (!id) {
      const remembered = q.get("exam") ?? lsGet(LS_EXAM) ?? (sim ? str(sim.exam_id) : null);
      id = remembered || (s.exams[0] ? str(s.exams[0].exam_id) : null);
    }
    store.set({ examId: id });
    await loadExam();
    await pollStudents(true);
  }

  /** @param {boolean} ok */
  function markNet(ok) {
    const n = store.state.net;
    if (ok && n.down) store.set({ net: { down: false, since: null } });
    else if (!ok && !n.down) store.set({ net: { down: true, since: new Date() } });
  }

  // ---------------------------------------------------------------- polling
  /** @type {ReturnType<typeof setTimeout>|null} */
  let timer = null;
  let tickNo = 0;
  let pollSeq = 0; // a newer poll (e.g. right after a command) wins over an older answer
  let appliedSeq = 0;

  /** @param {boolean} [_now] */
  async function pollStudents(_now = false) {
    if (timer) clearTimeout(timer);
    timer = null;
    const seq = ++pollSeq;
    try {
      const s = store.state;
      tickNo += 1;
      if (s.examId && !s.examError) {
        if (tickNo % 5 === 0 || !s.exam) await loadExam();
        const id = s.examId;
        const r = await api.students(id);
        if (store.state.examId === id && seq > appliedSeq) {
          appliedSeq = seq;
          if (r.ok && Array.isArray(r.data)) {
            markNet(true);
            const posted = new Map(store.state.posted);
            for (const st of r.data) {
              const p = posted.get(st.student_id);
              if (p && Array.isArray(st.commands) && st.commands.some((/** @type {any} */ c) => c.command_id === p.command_id)) posted.delete(st.student_id);
            }
            const ids = new Set(r.data.map((/** @type {any} */ x) => x.student_id));
            const selected = new Set([...store.state.selected].filter((x) => ids.has(x)));
            store.set({ students: r.data, studentsAt: new Date(), studentsError: null, posted, selected });
          } else if (r.network) markNet(false);
          else if (r.error.status === 401 || r.error.status === 403) store.set({ examError: r.error, students: [] });
          else store.set({ studentsError: r.error });
        }
      } else if (s.net.down) {
        await loadExams();
        if (!store.state.net.down) await loadExam();
      }
      if (sim && (tickNo % 2 === 0 || store.state.tab === "sim")) {
        const si = await api.simInfo();
        if (si.ok && si.data) store.set({ sim: si.data });
      }
    } finally {
      if (seq === pollSeq) schedule();
    }
  }

  function schedule() {
    if (timer) clearTimeout(timer);
    const s = store.state;
    timer = setTimeout(() => pollStudents(), s.net.down ? Math.max(2000, s.pollMs) : s.pollMs);
  }

  renderFrame(store.state);
  await reloadAll();
}

main().catch((err) => {
  const root = document.getElementById("app");
  if (root) replace(root, [h("p", { class: "fatal", role: "alert" }, ["Панель не запустилась. Обновите страницу; если ошибка повторится, сообщите администратору."])]);
  console.error(err);
});
