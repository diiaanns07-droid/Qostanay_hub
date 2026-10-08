// @ts-check
// Students and commands: table (polled), bulk commands for the selected students, policy assignment,
// confirmation dialog (who will / will not receive the command and why), per-student details with the
// command history and cancellation.
import { h, icon, nextId, replace, setAttr, setDisabled, setHidden, setText } from "../dom.js";
import {
  assignmentResultLines,
  commandResultLines,
  commandStatus,
  connectionText,
  EXAM_STATE_RU,
  expiryText,
  fmtTime,
  KIND_RU,
  latestCommand,
  lockStatus,
  reasonCheck,
  roleDenial,
  splitByAvailability,
  STATE_RU,
  str,
  TEACHER_KINDS,
} from "../model.js";
import { createModal, createResultsBox, lastCommandBlock, sendOneShot } from "./common.js";

const SHORT = { start_exam: "Начать", lock: "Заблокировать…", unlock: "Разблокировать", finish_exam: "Завершить" };
const NET_DOWN = "Нет связи с сервером класса — команды не отправляются, данные в строке могут быть устаревшими";
const SERVER_NOTE =
  "Сервер принял запрос — это ещё не выполнение. Итог по каждому студенту — в колонке «Последняя команда»: «выполнено» появляется только после подтверждения клиента студента.";

/** @param {any} ctx */
export function createStudentsTab(ctx) {
  const { api, store, announce } = ctx;
  /** @param {string} kind */
  const kindRu = (kind) => str(store.state.meta?.kinds?.[kind]) || KIND_RU[/** @type {"lock"} */ (kind)] || kind;
  /** @param {string} id */
  const labelOf = (id) => str(store.state.students.find((/** @type {any} */ s) => s.student_id === id)?.label) || id;

  const results = createResultsBox({ testid: "cmd-results", announce });

  // ------------------------------------------------------------ toolbar
  const selCount = h("p", { class: "sel-count", "data-testid": "sel-count" });
  /** @type {Record<string, HTMLElement>} */
  const bulk = {};
  for (const kind of TEACHER_KINDS) {
    const b = h("button", { type: "button", class: `btn${kind === "lock" ? " btn-danger" : ""}`, "data-bulk": kind }, [
      kind === "lock" ? `${KIND_RU.lock}…` : KIND_RU[kind],
    ]);
    b.addEventListener("click", () => dialog.open(kind, [...store.state.selected], b));
    bulk[kind] = b;
  }
  const bulkWhy = h("p", { class: "why", id: nextId("bulk-why"), "data-testid": "bulk-why" });
  for (const b of Object.values(bulk)) b.setAttribute("aria-describedby", bulkWhy.id);
  const bulkSummary = h("ul", { class: "why-list", "data-testid": "bulk-summary" });

  const policySelect = h("select", { id: "assign-policy" });
  const deliverNow = h("input", { type: "checkbox", id: "assign-now", checked: true });
  const assignBtn = h("button", { type: "button", class: "btn", "data-testid": "assign-btn" }, ["Назначить политику выбранным"]);
  const assignWhy = h("p", { class: "why", id: nextId("assign-why") });
  assignBtn.setAttribute("aria-describedby", assignWhy.id);
  assignBtn.addEventListener("click", () => assignPolicy());

  // Pausing the external site's timer is a different thing from our lock and is NOT available:
  // there is no integration with the exam site. Shown disabled on purpose, so nobody expects it.
  const timerWhy = h("p", { class: "why", id: nextId("timer-why"), "data-testid": "site-timer-why" }, [
    "Пауза таймера внешнего сайта недоступна: интеграции с сайтом нет. «Заблокировать» закрывает только экран студента нашим клиентом — время на сайте продолжает идти.",
  ]);
  const timerBtn = h("button", { type: "button", class: "btn", disabled: true, "aria-describedby": timerWhy.id, "data-testid": "site-timer-btn" }, [icon("clock"), "Пауза таймера сайта"]);
  const toolbar = h("section", { class: "toolbar", "aria-label": "Действия с выбранными студентами" }, [
    h("div", { class: "toolbar-row" }, [selCount, h("div", { class: "btn-row" }, Object.values(bulk))]),
    bulkWhy,
    bulkSummary,
    h("div", { class: "toolbar-row timer-row" }, [timerBtn, timerWhy]),
    h("div", { class: "toolbar-row assign" }, [
      h("label", { for: "assign-policy" }, ["Политика"]),
      policySelect,
      h("label", { class: "check" }, [deliverNow, " сразу отправить клиентам на связи"]),
      assignBtn,
    ]),
    assignWhy,
  ]);

  // ------------------------------------------------------------ table
  const allBox = h("input", { type: "checkbox", "aria-label": "Выбрать всех студентов", "data-testid": "select-all" });
  allBox.addEventListener("change", () => {
    const on = /** @type {HTMLInputElement} */ (allBox).checked;
    store.set({ selected: on ? new Set(store.state.students.map((/** @type {any} */ s) => s.student_id)) : new Set() });
  });
  const tbody = h("tbody");
  const table = h("table", { class: "students", "data-testid": "students-table" }, [
    h("caption", { class: "sr-only" }, ["Студенты экзамена: связь, экран, политика, последняя команда и действия"]),
    h("thead", {}, [
      h("tr", {}, [
        h("th", { scope: "col", class: "col-check" }, [allBox]),
        h("th", { scope: "col" }, ["Студент"]),
        h("th", { scope: "col" }, ["Связь"]),
        h("th", { scope: "col" }, ["Экран студента"]),
        h("th", { scope: "col" }, ["Политика"]),
        h("th", { scope: "col" }, ["Последняя команда"]),
        h("th", { scope: "col" }, ["Действия"]),
      ]),
    ]),
    tbody,
  ]);
  const tableWrap = h("div", { class: "table-wrap" }, [table]);
  const empty = h("p", { class: "empty", "data-testid": "students-empty", hidden: true });
  const freshness = h("p", { class: "freshness", "data-testid": "freshness" });

  /** @type {Map<string, ReturnType<typeof createRow>>} */
  const rows = new Map();

  const dialog = createActionDialog(ctx, kindRu, sendCommand);
  const drawer = createDrawer(ctx, kindRu);

  const root = h("div", { class: "students-tab" }, [
    h("h2", { class: "panel-title" }, ["Студенты и команды"]),
    toolbar,
    results.root,
    freshness,
    empty,
    tableWrap,
  ]);

  // ------------------------------------------------------------ rows
  /** @param {string} sid */
  function createRow(sid) {
    const cb = h("input", { type: "checkbox" });
    cb.addEventListener("change", () => {
      const sel = new Set(store.state.selected);
      if (/** @type {HTMLInputElement} */ (cb).checked) sel.add(sid);
      else sel.delete(sid);
      store.set({ selected: sel });
    });
    const name = h("span", { class: "st-name", "data-testid": "st-name" });
    const simTag = h("span", { class: "tag tag-sim", hidden: true }, [icon("flask", "ico ico-s"), "симулятор"]);
    const sub = h("span", { class: "sub" });
    const conn = h("td", { class: "conn", "data-testid": "conn" });
    const lock = h("td", { class: "lock", "data-testid": "lock" });
    const policy = h("td", { class: "policy", "data-testid": "policy" });
    const last = h("td", { class: "last", "data-testid": "last" });
    /** @type {Record<string, HTMLElement>} */
    const btns = {};
    const why = h("ul", { class: "why-list", id: nextId("row-why"), "data-testid": "row-why" });
    for (const kind of TEACHER_KINDS) {
      const b = h("button", { type: "button", class: `btn btn-small${kind === "lock" ? " btn-danger" : ""}`, "data-action": kind, "aria-describedby": why.id }, [SHORT[kind]]);
      b.addEventListener("click", () => dialog.open(kind, [sid], b));
      btns[kind] = b;
    }
    const more = h("button", { type: "button", class: "btn btn-small btn-ghost", "data-testid": "details" }, ["Подробнее"]);
    more.addEventListener("click", () => drawer.open(sid, more));
    const tr = h("tr", { "data-student": sid }, [
      h("td", { class: "col-check" }, [cb]),
      h("th", { scope: "row" }, [name, simTag, sub]),
      conn,
      lock,
      policy,
      last,
      h("td", { class: "actions" }, [h("div", { class: "btn-row" }, [...Object.values(btns), more]), why]),
    ]);
    let sig = "";
    return {
      tr,
      /** @param {any} v @param {import("../app.js").AppState} s */
      update(v, s) {
        const label = str(v.label) || sid;
        /** @type {HTMLInputElement} */ (cb).checked = s.selected.has(sid);
        cb.setAttribute("aria-label", `Выбрать: ${label}`);
        const cmd = latestCommand(v.commands, s.posted.get(sid));
        const denial = roleDenial(s.role, "command") ?? (s.net.down ? NET_DOWN : null);
        const next = JSON.stringify([v, cmd, denial]);
        if (next === sig) return;
        sig = next;
        setText(name, label);
        setHidden(simTag, !v.simulated);
        setText(sub, [v.computer_name ? `компьютер ${str(v.computer_name)}` : "", v.capabilities?.app_version ? `клиент ${str(v.capabilities.app_version)}` : ""].filter(Boolean).join(" · "));
        tr.classList.toggle("is-offline", !v.connected);
        replace(conn, [icon(v.connected ? "online" : "offline", "ico ico-s"), h("span", {}, [connectionText(v)])]);
        conn.className = `conn ${v.connected ? "tone-ok" : "tone-warn"}`;
        const ls = lockStatus(v.lock);
        replace(lock, [h("span", { class: `lockstate tone-${ls.tone}`, "data-lock-state": ls.state }, [icon(ls.icon), h("span", { "data-testid": "lock-label" }, [ls.label])])]);
        const pa = v.policy?.assigned;
        replace(policy, [
          h("span", {}, [pa ? `«${str(pa.name)}» v${str(pa.version)} · ${pa.mode === "app" ? "программа" : "сайт"}` : "нет данных"]),
          h("span", { class: "sub" }, [str(v.policy?.label_ru)]),
        ]);
        replace(last, lastCommandBlock(cmd));
        /** @type {HTMLElement[]} */
        const whyItems = [];
        if (denial) whyItems.push(h("li", {}, [denial]));
        for (const kind of TEACHER_KINDS) {
          const a = v.actions?.[kind];
          const available = !!a && a.available === true;
          setDisabled(btns[kind], !available || !!denial);
          setAttr(btns[kind], "aria-label", `${kindRu(kind)}: ${label}${available ? "" : " — недоступно"}`);
          if (!available && !denial) whyItems.push(h("li", { "data-why": kind }, [`${kindRu(kind)}: недоступно — ${a ? str(a.reason_ru) || "без объяснения" : "сервер не сообщил"}`]));
        }
        replace(why, whyItems);
      },
    };
  }

  // ------------------------------------------------------------ render
  let ctxKey = "";
  /** @param {import("../app.js").AppState} s */
  function render(s) {
    // answers to POSTs belong to the teacher and exam they were sent for
    const key = `${s.teacher}|${s.examId}`;
    if (key !== ctxKey) {
      if (ctxKey) results.hide();
      ctxKey = key;
    }
    const ids = s.students.map((x) => x.student_id);
    for (const [id, r] of rows) if (!ids.includes(id)) {
      r.tr.remove();
      rows.delete(id);
    }
    ids.forEach((id, i) => {
      let r = rows.get(id);
      if (!r) {
        r = createRow(id);
        rows.set(id, r);
      }
      if (tbody.children[i] !== r.tr) tbody.insertBefore(r.tr, tbody.children[i] ?? null);
      r.update(s.students[i], s);
    });
    const n = s.selected.size;
    const total = s.students.length;
    setText(selCount, `Выбрано: ${n} из ${total}`);
    /** @type {HTMLInputElement} */ (allBox).checked = total > 0 && n === total;
    /** @type {HTMLInputElement} */ (allBox).indeterminate = n > 0 && n < total;
    const denial = roleDenial(s.role, "command");
    for (const b of Object.values(bulk)) setDisabled(b, n === 0 || !!denial || !!s.examError || s.net.down);
    setText(bulkWhy, denial ? denial : s.net.down ? "Нет связи с сервером — команды не отправляются" : n === 0 ? "Отметьте студентов в таблице, чтобы отправить им команду." : "");
    /** @type {HTMLElement[]} */
    const summary = [];
    if (n > 0 && !denial) {
      for (const kind of TEACHER_KINDS) {
        const sp = splitByAvailability(s.students, s.selected, kind);
        if (sp.unavailable.length) summary.push(h("li", {}, [`«${kindRu(kind)}» недоступно для ${sp.unavailable.length} из ${n} выбранных (подробности — перед отправкой)`]));
      }
    }
    replace(bulkSummary, summary);
    // policy assignment
    const pols = s.exam?.policies ?? [];
    const sig = JSON.stringify(pols.map((/** @type {any} */ p) => [p.policy_id, p.name, p.version, p.mode_ru]));
    const sel = /** @type {HTMLSelectElement} */ (policySelect);
    if (sel.getAttribute("data-sig") !== sig) {
      const keep = sel.value;
      sel.replaceChildren(...pols.map((/** @type {any} */ p) => h("option", { value: str(p.policy_id) }, [`«${str(p.name)}» v${str(p.version)} — ${str(p.mode_ru)}`])));
      sel.setAttribute("data-sig", sig);
      if (keep && pols.some((/** @type {any} */ p) => p.policy_id === keep)) sel.value = keep;
    }
    const editDenial = roleDenial(s.role, "edit");
    setDisabled(assignBtn, n === 0 || !!editDenial || pols.length === 0 || s.net.down);
    setDisabled(sel, !!editDenial);
    setText(assignWhy, editDenial ? `Назначение политики: ${editDenial}` : n === 0 ? "" : `Политика будет назначена ${n} выбранным студентам; новая версия уходит клиенту командой «Применить политику» (если клиент её поддерживает).`);
    // empty / freshness
    const noExam = !s.examId;
    setHidden(tableWrap, noExam || !!s.examError || total === 0);
    tableWrap.classList.toggle("is-stale", s.net.down);
    setHidden(toolbar, noExam || !!s.examError);
    setHidden(empty, !(noExam || (!s.examError && total === 0 && s.studentsAt)));
    setText(empty, noExam ? "Выберите или создайте экзамен." : "К этому экзамену ещё не подключился ни один студент. Студент появится здесь, когда его клиент подключится к серверу класса с кодом экзамена.");
    setText(
      freshness,
      s.examError || noExam
        ? ""
        : s.net.down
          ? `Данные устарели: нет связи с сервером. Последнее обновление — ${s.studentsAt ? s.studentsAt.toLocaleTimeString("ru-RU") : "не было"}.`
          : s.studentsAt
            ? `Обновлено в ${s.studentsAt.toLocaleTimeString("ru-RU")} (опрос сервера каждые ${s.pollMs / 1000} с).`
            : "Загружаем студентов…",
    );
    freshness.classList.toggle("tone-warn", s.net.down);
    drawer.refresh(s);
  }

  // ------------------------------------------------------------ sending
  /** @param {string} kind @param {string[]} ids @param {Record<string, unknown>} payload */
  function sendCommand(kind, ids, payload) {
    const examId = store.state.examId;
    if (!examId) return;
    const title = `«${kindRu(kind)}» → ${ids.length === 1 ? labelOf(ids[0]) : `${ids.length} студентам`}`;
    const { run } = sendOneShot({
      title,
      results,
      body: { kind, student_ids: ids, payload },
      post: (b) => api.sendCommand(examId, b),
      onAnswer: (data) => {
        const posted = new Map(store.state.posted);
        for (const r of data?.results ?? []) if (r.command) posted.set(r.student_id, r.command);
        store.set({ posted });
        ctx.pollNow();
      },
      lines: (d) => commandResultLines(d, labelOf),
      note: SERVER_NOTE,
      announce,
    });
    run();
  }

  function assignPolicy() {
    const examId = store.state.examId;
    const ids = [...store.state.selected];
    const policyId = /** @type {HTMLSelectElement} */ (policySelect).value;
    if (!examId || !ids.length || !policyId) return;
    const p = (store.state.exam?.policies ?? []).find((/** @type {any} */ x) => x.policy_id === policyId);
    const { run } = sendOneShot({
      title: `Назначение политики «${str(p?.name ?? policyId)}» v${str(p?.version ?? "")} → ${ids.length} студ.`,
      results,
      body: { policy_id: policyId, student_ids: ids, deliver_now: /** @type {HTMLInputElement} */ (deliverNow).checked },
      post: (b) => api.assign(examId, b),
      onAnswer: () => {
        ctx.reloadExam();
        ctx.pollNow();
      },
      lines: (d) => assignmentResultLines(d, labelOf),
      note: "Назначение сохранено сервером. Применение у клиента подтверждается отдельно — смотрите колонку «Политика».",
      announce,
    });
    run();
  }

  return { root, render };
}

// ================================================================== confirmation dialog

/**
 * @param {any} ctx @param {(k: string) => string} kindRu
 * @param {(kind: string, ids: string[], payload: Record<string, unknown>) => void} onSend
 */
function createActionDialog(ctx, kindRu, onSend) {
  const { store } = ctx;
  const modal = createModal({ title: "", testid: "action-dialog" });
  const reasonId = nextId("reason");
  const counterId = nextId("reason-count");
  const errId = nextId("reason-err");
  const reason = /** @type {HTMLTextAreaElement} */ (h("textarea", { id: reasonId, rows: "3", "aria-describedby": `${counterId} ${errId}`, "data-testid": "lock-reason" }));
  const counter = h("p", { id: counterId, class: "counter" });
  const reasonErr = h("p", { id: errId, class: "field-error", hidden: true });
  const sendBtn = h("button", { type: "button", class: "btn btn-primary", "data-testid": "dialog-send" });
  const cancelBtn = h("button", { type: "button", class: "btn" }, ["Отмена"]);
  cancelBtn.addEventListener("click", () => modal.close());
  replace(modal.foot, [cancelBtn, sendBtn]);
  /** @type {{kind: string, ids: string[]}} */
  let cur = { kind: "", ids: [] };
  const max = () => Number(store.state.meta?.reason_max) || 200;
  const updateCounter = () => {
    const c = reasonCheck(reason.value, max());
    setText(counter, `${c.length} / ${max()} символов`);
    counter.classList.toggle("tone-bad", c.length > max());
  };
  reason.addEventListener("input", () => {
    updateCounter();
    if (!reasonErr.hasAttribute("hidden")) {
      const c = reasonCheck(reason.value, max());
      setText(reasonErr, c.ok ? "" : c.message);
      setHidden(reasonErr, c.ok);
    }
  });
  sendBtn.addEventListener("click", () => {
    const s = store.state;
    const sp = splitByAvailability(s.students, cur.ids, cur.kind);
    if (!sp.available.length) return;
    /** @type {Record<string, unknown>} */
    let payload = {};
    if (cur.kind === "lock") {
      const c = reasonCheck(reason.value, max());
      if (!c.ok) {
        setText(reasonErr, c.message);
        setHidden(reasonErr, false);
        reason.setAttribute("aria-invalid", "true");
        reason.focus();
        return;
      }
      payload = { reason_ru: c.value };
    }
    modal.close();
    // all selected students go to the server: it re-checks availability (the table may be a second old),
    // answers unavailable_ru for the others and journals the attempt
    onSend(cur.kind, [...cur.ids], payload);
  });

  /** @param {string} kind @param {string[]} ids @param {HTMLElement} _opener */
  function open(kind, ids, _opener) {
    const s = store.state;
    cur = { kind, ids };
    const sp = splitByAvailability(s.students, ids, kind);
    modal.setTitle(`${kindRu(kind)}: подтверждение`);
    /** @type {Array<HTMLElement|null>} */
    const parts = [];
    parts.push(
      sp.available.length
        ? h("section", { class: "dlg-list", "data-testid": "will-send" }, [
            h("h3", {}, [`Будет отправлено (${sp.available.length})`]),
            h("ul", {}, sp.available.map((x) => h("li", {}, [x.label]))),
          ])
        : h("p", { class: "warn-box", role: "alert" }, [icon("warn"), "Ни одному выбранному студенту это действие сейчас недоступно — отправлять нечего."]),
    );
    if (sp.unavailable.length) {
      parts.push(
        h("section", { class: "dlg-list warn-box", "data-testid": "will-skip" }, [
          h("h3", {}, [icon("slash"), `Недоступно — студенту команда не будет отправлена (${sp.unavailable.length})`]),
          h("ul", {}, sp.unavailable.map((x) => h("li", {}, [h("strong", {}, [x.label]), ` — ${x.reason}`]))),
        ]),
      );
    }
    if (kind === "lock") {
      reason.value = "";
      reason.removeAttribute("aria-invalid");
      setHidden(reasonErr, true);
      updateCounter();
      parts.push(
        h("div", { class: "field" }, [h("label", { for: reasonId }, ["Причина блокировки (студент увидит её на экране блокировки)"]), reason, counter, reasonErr]),
        h("div", { class: "timer-note", role: "note", "data-testid": "timer-note" }, [
          icon("warn", "ico ico-l"),
          h("div", {}, [
            h("strong", {}, ["Таймер внешнего сайта не останавливается."]),
            " ",
            str(s.meta?.site_timer_note_ru) || "Блокировка нашим клиентом не ставит на паузу таймер внешнего сайта: интеграции с сайтом нет.",
          ]),
        ]),
      );
    }
    if (kind === "start_exam") {
      parts.push(h("p", { class: "muted" }, ["Клиент студента откроет экзамен по назначенной ему политике (режим, разрешённые адреса или программы) и включит наблюдение."]));
    }
    if (kind === "finish_exam") {
      parts.push(h("p", { class: "muted" }, ["Клиент завершит сессию экзамена у студента и снимет ограничения."]));
    }
    parts.push(h("p", { class: "expiry", "data-testid": "expiry" }, [icon("clock"), expiryText(kind, s.meta)]));
    replace(modal.body, parts);
    setText(sendBtn, sp.available.length ? `Отправить (${sp.available.length})` : "Отправить");
    setDisabled(sendBtn, sp.available.length === 0);
    sendBtn.className = `btn ${kind === "lock" ? "btn-danger" : "btn-primary"}`;
    modal.open(kind === "lock" && sp.available.length ? reason : sp.available.length ? sendBtn : cancelBtn);
  }

  return { open };
}

// ================================================================== details drawer

/** @param {any} ctx @param {(k: string) => string} kindRu */
function createDrawer(ctx, kindRu) {
  const { api, store, announce } = ctx;
  const modal = createModal({ title: "Студент", testid: "student-drawer", wide: true });
  const info = h("dl", { class: "kv" });
  const timer = h("p", { class: "timer-note small", role: "note" });
  const cmdStatus = h("p", { class: "muted", role: "status" });
  const cmdList = h("ol", { class: "cmd-list", "data-testid": "cmd-history" });
  replace(modal.body, [info, timer, h("h3", {}, ["Команды этому студенту"]), cmdStatus, cmdList]);
  /** @type {string|null} */
  let sid = null;
  let lastFetch = 0;
  let fetching = false;
  /** @type {any[]} */
  let cmds = [];

  /** @param {string} id @param {HTMLElement} opener */
  function open(id, opener) {
    sid = id;
    cmds = [];
    setText(cmdStatus, "Загружаем историю команд…");
    replace(cmdList, []);
    refresh(store.state, true);
    modal.open(null, () => {
      sid = null;
    });
    void opener;
  }

  /** @param {string} label @param {string} value */
  const row = (label, value) => [h("dt", {}, [label]), h("dd", {}, [value])];

  /** @param {import("../app.js").AppState} s @param {boolean} [force] */
  function refresh(s, force = false) {
    if (!sid || (!force && !modal.isOpen)) return;
    const v = s.students.find((x) => x.student_id === sid);
    if (!v) {
      replace(info, [h("dd", {}, ["Студента больше нет в списке экзамена."])]);
      return;
    }
    modal.setTitle(`${str(v.label)}${v.simulated ? " (симулятор)" : ""}`);
    const caps = v.capabilities || {};
    replace(info, [
      ...row("Связь", (v.connected ? "на связи" : "нет связи") + ` (${connectionText(v)})`),
      ...row("Экран студента", lockStatus(v.lock).label),
      ...row("Этап экзамена у клиента", v.status ? EXAM_STATE_RU[/** @type {"idle"} */ (v.status.exam_state)] ?? str(v.status.exam_state) : "статус не получен"),
      ...row("Политика", `${v.policy?.assigned ? `«${str(v.policy.assigned.name)}» v${str(v.policy.assigned.version)}` : "—"} — ${str(v.policy?.label_ru)}`),
      ...row(
        "Клиент умеет",
        caps.declared
          ? (caps.commands || []).map((/** @type {string} */ k) => kindRu(k)).join(", ") || "ничего из команд T04"
          : "клиент v1 не сообщил список возможностей: доступны только команды протокола v1",
      ),
      ...row("Режимы клиента", Array.isArray(caps.modes) ? caps.modes.map((/** @type {string} */ m) => (m === "url" ? "внешний сайт" : m === "app" ? "отдельная программа" : m)).join(", ") : "не сообщены"),
      ...row("Проверка срока команд на клиенте", caps.command_expiry ? "да" : "клиент не сообщил (сервер всё равно не отправит просроченную команду)"),
      ...row("id", `${str(v.student_id)}${v.computer_name ? ` · ${str(v.computer_name)}` : ""}`),
    ]);
    setText(timer, str(v.site_timer_ru));
    const now = Date.now();
    if (!fetching && (force || now - lastFetch > 900)) loadCommands(s);
  }

  /** @param {import("../app.js").AppState} s */
  async function loadCommands(s) {
    const id = sid;
    const examId = s.examId;
    if (!id || !examId) return;
    fetching = true;
    lastFetch = Date.now();
    const r = await api.commands(examId, id);
    fetching = false;
    if (sid !== id) return;
    if (!r.ok) {
      setText(cmdStatus, r.error.message);
      return;
    }
    cmds = Array.isArray(r.data) ? r.data : [];
    setText(cmdStatus, cmds.length ? `Команд: ${cmds.length} (новые сверху)` : "Команд этому студенту ещё не было.");
    drawCommands();
  }

  function drawCommands() {
    const denial = roleDenial(store.state.role, "command");
    const focusedId = document.activeElement?.getAttribute("data-cancel");
    const hadFocus = cmdList.contains(document.activeElement);
    replace(
      cmdList,
      cmds.map((c) => {
        const st = commandStatus(c);
        const cancel = c.cancellable
          ? h("button", { type: "button", class: "btn btn-small", "data-cancel": str(c.command_id), disabled: !!denial, title: denial ?? null }, ["Отменить (ещё не доставлена)"])
          : null;
        if (cancel) cancel.addEventListener("click", () => cancelCommand(c, cancel));
        const ack = c.ack
          ? `Ответ клиента: ${c.ack.ok ? "выполнено" : "не выполнено"}${c.ack.code ? ` (код ${str(c.ack.code)})` : ""}${c.ack.error_ru ? ` — ${str(c.ack.error_ru)}` : ""}${c.ack.late ? " · пришёл после восстановления связи" : ""} · получен ${fmtTime(c.ack.received_at)}`
          : "Ответа клиента нет";
        return h("li", { class: "cmd", "data-command": str(c.command_id), "data-group": st ? st.group : "" }, [
          h("div", { class: "cmd-head" }, [
            st ? h("span", { class: `chip tone-${st.tone}` }, [icon(st.icon), h("span", { class: "chip-group" }, [st.groupLabel])]) : null,
            h("strong", {}, [str(c.kind_ru ?? c.kind)]),
            c.payload?.reason_ru ? h("span", {}, [` — причина: «${str(c.payload.reason_ru)}»`]) : null,
          ]),
          h("p", { class: "cmd-label" }, [str(c.label_ru)]),
          h("p", { class: "sub" }, [
            `Отправил: ${str(c.issued_by_name)} · создана ${fmtTime(c.issued_at)} · действует до ${fmtTime(c.expires_at)} · попыток доставки: ${str(c.attempts)}`,
          ]),
          h("p", { class: "sub" }, [ack]),
          c.note_ru ? h("p", { class: "sub" }, [str(c.note_ru)]) : null,
          h(
            "ol",
            { class: "history" },
            (c.history || []).map((/** @type {any} */ x) => {
                const label = STATE_RU[/** @type {"sent"} */ (x.state)] ?? str(x.state);
                const detail = str(x.detail_ru);
                return h("li", { "data-state": str(x.state) }, [h("time", {}, [fmtTime(x.at)]), " ", h("span", {}, [label]), detail && !label.startsWith(detail) ? ` — ${detail}` : ""]);
              },
            ),
          ),
          cancel,
        ]);
      }),
    );
    const again = focusedId ? /** @type {HTMLElement|null} */ (cmdList.querySelector(`[data-cancel="${CSS.escape(focusedId)}"]`)) : null;
    if (again && !again.hasAttribute("disabled")) again.focus();
    else if (hadFocus) modal.box.focus(); // keep keyboard focus inside the dialog
  }

  /** @param {any} c @param {HTMLElement} btn */
  async function cancelCommand(c, btn) {
    const examId = store.state.examId;
    if (!examId) return;
    setDisabled(btn, true);
    const r = await api.cancel(examId, c.command_id);
    if (r.ok) {
      announce(`Команда «${str(c.kind_ru)}» отменена`);
      setText(cmdStatus, "Команда отменена до доставки.");
    } else {
      setText(cmdStatus, `Не удалось отменить: ${r.error.message}`);
      setDisabled(btn, false);
    }
    ctx.pollNow();
    loadCommands(store.state);
  }

  return { open, refresh };
}
