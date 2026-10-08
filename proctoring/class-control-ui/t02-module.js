// @ts-check
// T04 module for the T02 class panel (extension point window.QorgauClassPanel.registerStudentModule,
// slot "commands"). Shows, for ctx.studentId, the teacher commands with their real delivery status and the
// lock state exactly as the server reports it, using the same T04 API (/api/teacher/control/*).
//
// Usage in the T02 panel page (real mode, same origin as the class server):
//   <script type="module" src="/ui/t02-module.js"></script>
// It registers itself when window.QorgauClassPanel exists, otherwise queues itself in
// window.QorgauClassPanelModules (read by the panel at start-up). In DEMO mode it renders
// "Недоступно в DEMO-режиме" and makes no requests.
import { createApi } from "./src/api.js";
import { h, icon, replace, setDisabled, setHidden, setText } from "./src/dom.js";
import {
  commandResultLines,
  commandStatus,
  expiryText,
  fmtTime,
  KIND_RU,
  latestCommand,
  lockStatus,
  OneShotRequest,
  reasonCheck,
  str,
  TEACHER_KINDS,
} from "./src/model.js";

export const MODULE_ID = "t04-commands";

/**
 * @param {{ api?: ReturnType<typeof createApi>, fetchImpl?: typeof fetch, getTeacher?: () => string|null,
 *           pollMs?: number, timers?: {setTimeout: typeof setTimeout, clearTimeout: typeof clearTimeout},
 *           onUpdate?: (stage: string) => void, stylesheet?: string|null }} [opts]
 */
export function createCommandsModule(opts = {}) {
  const pollMs = opts.pollMs ?? 1000;
  const timers = opts.timers ?? { setTimeout: globalThis.setTimeout.bind(globalThis), clearTimeout: globalThis.clearTimeout.bind(globalThis) };
  const notify = opts.onUpdate ?? (() => {});
  return {
    id: MODULE_ID,
    slot: /** @type {const} */ ("commands"),
    title: "Команды преподавателя (T04)",
    /**
     * @param {HTMLElement} el
     * @param {{studentId: string, mode: "demo"|"real"|string, getStudent?: () => any, subscribe?: (fn: () => void) => () => void}} ctx
     */
    mount(el, ctx) {
      ensureStylesheet(opts.stylesheet);
      const box = h("div", { class: "t04-mod", "data-testid": "t04-module" });
      replace(el, [box]);
      if (ctx.mode !== "real") {
        replace(box, [h("p", { class: "t04-note", "data-testid": "t04-demo" }, ["Недоступно в DEMO-режиме: команды отправляются только реальным клиентам через сервер класса."])]);
        notify("demo");
        return () => {};
      }
      const api = opts.api ?? createApi({ fetchImpl: opts.fetchImpl, getTeacher: opts.getTeacher });
      let alive = true;
      /** @type {any} */
      let timer = null;
      /** @type {string|null} */
      let examId = null;
      /** @type {any} */
      let view = null;
      /** @type {any} */
      let meta = null;
      /** @type {any} */
      let posted = null;

      const status = h("p", { class: "t04-status", role: "status", "data-testid": "t04-status" }, ["Загружаем…"]);
      const lockLine = h("p", { class: "t04-lock", "data-testid": "t04-lock" });
      const lastLine = h("div", { class: "t04-last", "data-testid": "t04-last" });
      /** @type {Record<string, HTMLElement>} */
      const btns = {};
      const why = h("ul", { class: "t04-why", "data-testid": "t04-why" });
      const result = h("div", { class: "t04-result", "aria-live": "polite", "data-testid": "t04-result" });
      const reasonIn = /** @type {HTMLTextAreaElement} */ (h("textarea", { rows: "2", "aria-label": "Причина блокировки (увидит студент)", "data-testid": "t04-reason" }));
      const reasonCount = h("span", { class: "t04-sub" });
      const reasonErr = h("p", { class: "t04-err", hidden: true });
      const lockSend = h("button", { type: "button", class: "t04-btn t04-danger", "data-testid": "t04-lock-send" }, ["Отправить блокировку"]);
      const timerNote = h("p", { class: "t04-timer", role: "note", "data-testid": "t04-timer" });
      const expiry = h("p", { class: "t04-sub", "data-testid": "t04-expiry" });
      const lockForm = h("div", { class: "t04-lockform", hidden: true, "data-testid": "t04-lockform" }, [reasonIn, reasonCount, reasonErr, timerNote, expiry, lockSend]);
      for (const kind of TEACHER_KINDS) {
        const b = h("button", { type: "button", class: `t04-btn${kind === "lock" ? " t04-danger" : ""}`, "data-t04-action": kind }, [kind === "lock" ? `${KIND_RU.lock}…` : KIND_RU[kind]]);
        b.addEventListener("click", () => {
          if (kind === "lock") {
            setHidden(lockForm, !lockForm.hasAttribute("hidden"));
            if (!lockForm.hasAttribute("hidden")) reasonIn.focus();
          } else send(kind, {});
        });
        btns[kind] = b;
      }
      reasonIn.addEventListener("input", () => setText(reasonCount, `${reasonCheck(reasonIn.value, Number(meta?.reason_max) || 200).length} / ${Number(meta?.reason_max) || 200}`));
      lockSend.addEventListener("click", () => {
        const c = reasonCheck(reasonIn.value, Number(meta?.reason_max) || 200);
        setText(reasonErr, c.message);
        setHidden(reasonErr, c.ok);
        if (!c.ok) return;
        setHidden(lockForm, true);
        reasonIn.value = "";
        send("lock", { reason_ru: c.value });
      });
      replace(box, [status, lockLine, lastLine, h("div", { class: "t04-btns" }, Object.values(btns)), why, lockForm, result]);

      /** @param {string} kind @param {Record<string, unknown>} payload */
      async function send(kind, payload) {
        if (!examId) return;
        const req = new OneShotRequest({ kind, student_ids: [ctx.studentId], payload });
        const run = async () => {
          replace(result, [h("p", { class: "t04-sub" }, [req.tries ? "Повторяем тот же запрос…" : "Отправляем…"])]);
          const r = await req.send((b) => api.sendCommand(/** @type {string} */ (examId), b));
          if (!alive) return;
          if (r.ok) {
            const line = commandResultLines(r.data, () => str(view?.label) || ctx.studentId, { where: "в строке «последняя команда» выше" })[0];
            posted = line?.command ?? posted;
            replace(result, [h("p", { "data-outcome": line?.outcome ?? "" }, [`Ответ сервера ${fmtTime(new Date().toISOString())}: `, line ? line.text : "без результата"])]);
            notify("sent");
            poll();
          } else {
            const retry = req.retryable ? h("button", { type: "button", class: "t04-btn", "data-testid": "t04-retry" }, ["Повторить тот же запрос"]) : null;
            if (retry) retry.addEventListener("click", run);
            replace(result, [h("p", { class: "t04-err", role: "alert" }, [r.error.message]), retry]);
            notify("send-error");
          }
        };
        await run();
      }

      function draw() {
        if (!view) return;
        const ls = lockStatus(view.lock);
        replace(lockLine, [icon(ls.icon), h("span", {}, [`Экран: ${ls.label}`])]);
        lockLine.className = `t04-lock tone-${ls.tone}`;
        const cmd = latestCommand(view.commands, posted);
        if (posted && Array.isArray(view.commands) && view.commands.some((/** @type {any} */ c) => c.command_id === posted.command_id)) posted = null;
        const st = commandStatus(cmd);
        replace(
          lastLine,
          st
            ? [
                h("span", { class: `t04-chip tone-${st.tone}`, "data-group": st.group }, [icon(st.icon), st.groupLabel]),
                h("span", {}, [` ${str(cmd.kind_ru ?? cmd.kind)} · ${fmtTime(cmd.issued_at)} — ${st.label}`]),
              ]
            : [h("span", { class: "t04-sub" }, ["Команд этому студенту ещё не было."])],
        );
        /** @type {HTMLElement[]} */
        const reasons = [];
        for (const kind of TEACHER_KINDS) {
          const a = view.actions?.[kind];
          const ok = !!a && a.available === true;
          setDisabled(btns[kind], !ok);
          if (!ok) reasons.push(h("li", { "data-why": kind }, [`${str(meta?.kinds?.[kind]) || KIND_RU[kind]}: недоступно — ${a ? str(a.reason_ru) : "сервер не сообщил"}`]));
        }
        replace(why, reasons);
        setText(timerNote, str(view.site_timer_ru) || str(meta?.site_timer_note_ru));
        setText(expiry, expiryText("lock", meta));
        setText(status, view.connected ? "Клиент на связи." : "Клиента нет на связи — команды дождутся подключения в пределах срока действия.");
      }

      async function poll() {
        if (timer) timers.clearTimeout(timer);
        timer = null;
        if (!alive || !examId) return;
        const r = await api.students(examId);
        if (!alive) return;
        if (r.ok && Array.isArray(r.data)) {
          view = r.data.find((/** @type {any} */ s) => s.student_id === ctx.studentId) ?? null;
          if (!view) setText(status, "Студента больше нет в этом экзамене.");
          else draw();
          notify("ready");
        } else setText(status, r.error.message);
        timer = timers.setTimeout(poll, pollMs);
      }

      (async () => {
        const [m, ex] = await Promise.all([api.meta(), api.exams()]);
        if (!alive) return;
        meta = m.ok ? m.data : null;
        if (!ex.ok) {
          setText(status, ex.error.message);
          notify("error");
          return;
        }
        const exam = (Array.isArray(ex.data) ? ex.data : []).find((/** @type {any} */ e) => Array.isArray(e.students) && e.students.includes(ctx.studentId));
        if (!exam) {
          setText(status, "Студент не подключён ни к одному экзамену, доступному вам, — команды недоступны.");
          for (const b of Object.values(btns)) setDisabled(b, true);
          notify("no-exam");
          return;
        }
        examId = String(exam.exam_id);
        await poll();
      })();

      return () => {
        alive = false;
        if (timer) timers.clearTimeout(timer);
      };
    },
  };
}

/** @param {string|null|undefined} href */
function ensureStylesheet(href) {
  if (href === null || typeof document === "undefined" || !document.head) return;
  const url = href ?? new URL("./t02-module.css", import.meta.url).href;
  if (document.querySelector?.(`link[data-t04-module]`)) return;
  document.head.appendChild(h("link", { rel: "stylesheet", href: url, "data-t04-module": "1" }));
}

/** Register in the T02 panel (or queue for it). @param {any} win @param {Parameters<typeof createCommandsModule>[0]} [opts] */
export function register(win, opts) {
  if (!win) return false;
  const mod = createCommandsModule(opts);
  if (win.QorgauClassPanel && typeof win.QorgauClassPanel.registerStudentModule === "function") {
    win.QorgauClassPanel.registerStudentModule(mod);
    return true;
  }
  win.QorgauClassPanelModules = Array.isArray(win.QorgauClassPanelModules) ? win.QorgauClassPanelModules : [];
  win.QorgauClassPanelModules.push(mod);
  return true;
}

if (typeof window !== "undefined" && typeof document !== "undefined" && !(/** @type {any} */ (window).__QORGAU_T04_NO_AUTOREGISTER)) {
  register(window);
}
