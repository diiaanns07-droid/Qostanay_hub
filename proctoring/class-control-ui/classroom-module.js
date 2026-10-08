// @ts-check
// Live module uses only C1's current classroom and persisted command bus. No second exam selection.
import { createApi } from "./src/api.js";
import { h, replace, setDisabled, setHidden, setText } from "./src/dom.js";
import { reasonCheck } from "./src/model.js";
import { ACTIONS, REASONS, currentLock, commandState, unavailable } from "./classroom-model.js";

/** @param {{api?: ReturnType<typeof createApi>, pollMs?: number, stylesheet?: string|null}} [opts] */
export function createClassroomCommands(opts = {}) {
  return {
    id: "adal-classroom-commands", slot: /** @type {const} */ ("commands"), title: "Управление студентом",
    /** @param {HTMLElement} el @param {{studentId:string, mode:string}} ctx */
    mount(el, ctx) {
      if (opts.stylesheet !== null && !document.querySelector("link[data-adal-controls]")) document.head.appendChild(h("link", {
        rel: "stylesheet", href: opts.stylesheet ?? new URL("./t02-module.css", import.meta.url).href, "data-adal-controls": "1",
      }));
      const box = h("div", { class: "t04-mod", "data-testid": "adal-controls" });
      replace(el, [box]);
      if (ctx.mode !== "real") {
        replace(box, [h("p", { class: "t04-note" }, ["Команды доступны при подключении к настоящему серверу класса."])]);
        return () => {};
      }
      const api = opts.api ?? createApi();
      const path = `/api/teacher/students/${encodeURIComponent(ctx.studentId)}`;
      let alive = true, busy = false, loading = false, online = false;
      /** @type {ReturnType<typeof setTimeout>|null} */ let timer = null;
      /** @type {any} */ let session = null;
      /** @type {any} */ let card = null;
      /** @type {any[]} */ let commands = [];
      /** @type {any} */ let posted = null;
      const heading = h("p", { class: "t04-sub", "data-testid": "adal-class" }, ["Загружаем текущий класс…"]);
      const lock = h("p", { class: "t04-lock", "data-testid": "adal-lock", role: "status" });
      const last = h("p", { class: "t04-last", "data-testid": "adal-command", role: "status" });
      const notice = h("p", { class: "t04-sub", role: "status", "data-testid": "adal-result" });
      const reason = /** @type {HTMLTextAreaElement} */ (h("textarea", { rows: "2", maxlength: "200", "aria-label": "Причина закрытия экрана — увидит студент", "data-testid": "adal-reason" }));
      const reasonError = h("p", { class: "t04-err", role: "alert" });
      const presets = REASONS.map(text => h("button", { type: "button", class: "t04-btn t04-preset", onclick: () => { reason.value = text; reason.focus(); } }, [text]));
      const lockSend = h("button", { type: "button", class: "t04-btn t04-danger", "data-testid": "adal-lock-send", onclick: () => {
        const checked = reasonCheck(reason.value, 200);
        setText(reasonError, checked.message);
        if (!checked.ok) return;
        void send("lock", { reason_ru: checked.value });
      } }, ["Закрыть экран Adal"]);
      const lockForm = h("div", { class: "t04-lockform", hidden: true }, [h("span", { class: "t04-sub" }, ["Причина для студента"]), h("div", { class: "t04-presets" }, presets), reason, reasonError,
        h("p", { class: "t04-sub" }, ["Экран закрывается внутри Adal. Это не блокировка Windows. Таймер внешнего сайта может продолжать идти."]), lockSend]);
      const finishSend = h("button", { type: "button", class: "t04-btn t04-danger", "data-testid": "adal-finish-send", onclick: () => void send("finish_exam", {}) }, ["Подтвердить завершение"]);
      const finishForm = h("div", { class: "t04-lockform", hidden: true }, [h("p", {}, ["Завершить локальный экзамен и наблюдение этого студента? Это не отправляет ответы на внешнем сайте."]), finishSend]);
      /** @type {Record<string,HTMLElement>} */ const buttons = {};
      for (const [kind, label] of Object.entries(ACTIONS)) {
        buttons[kind] = h("button", { type: "button", class: "t04-btn", disabled: true, "data-action": kind, onclick: () => {
          if (kind === "lock") { setHidden(lockForm, !lockForm.hidden); setHidden(finishForm, true); if (!lockForm.hidden) reason.focus(); }
          else if (kind === "finish_exam") { setHidden(finishForm, !finishForm.hidden); setHidden(lockForm, true); }
          else void send(kind, {});
        } }, [label]);
      }
      replace(box, [heading, lock, last, h("div", { class: "t04-btns" }, Object.values(buttons)), lockForm, finishForm, notice]);

      function draw() {
        const current = online && session?.state === "open" && session?.session_id === card?.session_id;
        setText(heading, current ? `Класс: ${session.title}` : "Студент не относится к текущему доступному классу.");
        const screen = currentLock(current ? card : null);
        lock.className = `t04-lock tone-${screen.tone}`; lock.dataset.state = screen.state; setText(lock, screen.text);
        const latest = posted && !commands.some(c => c.command_id === posted.command_id) ? posted : commands[0];
        const state = commandState(latest, card);
        last.className = `t04-last tone-${state.tone}`; last.dataset.state = state.state;
        const label = latest && ACTIONS[/** @type {keyof typeof ACTIONS} */ (latest.kind)];
        setText(last, `${label ? `${label.replace(/…$/, "")} — ` : ""}${state.text}`);
        for (const [kind, button] of Object.entries(buttons)) {
          const why = unavailable(current ? session : null, card, kind);
          setDisabled(button, busy || !!why);
          button.title = why ?? "";
        }
        setDisabled(lockSend, busy || !!unavailable(current ? session : null, card, "lock"));
        setDisabled(finishSend, busy || !!unavailable(current ? session : null, card, "finish_exam"));
      }

      /** @param {string} kind @param {Record<string,unknown>} payload */
      async function send(kind, payload) {
        if (busy || !online || unavailable(session, card, kind)) return;
        busy = true; draw(); setText(notice, "Отправляем команду…");
        // No blind retry after an uncertain POST: a fresh poll shows the persisted C1 command first.
        const response = await api.raw("POST", `${path}/commands`, { kind, payload });
        if (!alive) return;
        busy = false;
        if (response.ok) {
          posted = response.data;
          setHidden(lockForm, true); setHidden(finishForm, true);
          setText(notice, "Команда принята сервером. Это ещё не подтверждение выполнения.");
        } else setText(notice, response.network ? "Ответ на команду не получен. Проверьте последнюю команду перед повторной отправкой." : response.error?.message ?? "Ответ сервера не получен.");
        draw(); await poll();
      }

      async function poll() {
        if (!alive || loading) return;
        loading = true; if (timer) clearTimeout(timer); timer = null;
        const results = await Promise.all([api.raw("GET", "/api/teacher/session"), api.raw("GET", path), api.raw("GET", `${path}/commands`)]);
        loading = false; if (!alive) return;
        const failed = results.find(r => !r.ok);
        online = !failed;
        if (failed) setText(notice, failed.error?.message ?? "Ответ сервера не получен.");
        else {
          [session, card, commands] = results.map(r => r.data);
          commands = Array.isArray(commands) ? commands.sort((a, b) => Date.parse(b.issued_at) - Date.parse(a.issued_at)) : [];
        }
        draw(); timer = setTimeout(poll, opts.pollMs ?? 1000);
      }
      draw(); void poll();
      return () => { alive = false; if (timer) clearTimeout(timer); };
    },
  };
}

/** @param {any} win */
export function register(win) {
  const mod = createClassroomCommands();
  if (typeof win.QorgauClassPanel?.registerStudentModule === "function") win.QorgauClassPanel.registerStudentModule(mod);
  else (win.QorgauClassPanelModules ??= []).push(mod);
}
if (typeof window !== "undefined" && typeof document !== "undefined") register(window);
