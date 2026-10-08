// @ts-check
// Small C1 session entry point: a teacher should not need curl to connect a class.
// No calls or fabricated join codes in DEMO; configuration is not proof of enforcement.
import { h, setText } from "./dom.js";

/** @param {"real"|"demo"} mode */
export function createSessionBar(mode) {
  const root = h("section", { class: "session-bar", "aria-label": "Подключение класса" });
  if (mode === "demo") {
    root.append(h("div", { class: "session-summary" }, [
      h("p", { class: "eyebrow" }, ["НАБЛЮДЕНИЕ"]),
      h("h1", {}, ["Учебная аудитория"]),
      h("span", { class: "session-note" }, ["Выберите студента, чтобы посмотреть события."]),
    ]));
    return { root, destroy() {} };
  }

  /** @type {{session_id:string,title:string,state:string,join_code?:string|null,students_total?:number}|null} */
  let session = null;
  let alive = true;
  let loading = false;
  let busy = false;
  const title = h("h1", {}, ["Подключение к классу…"]);
  const note = h("span", { class: "session-note" });
  const code = h("strong", { class: "join-code" });
  const copy = /** @type {HTMLButtonElement} */ (h("button", { class: "btn", type: "button" }, ["Копировать"]));
  const codeBox = h("div", { class: "join-box", hidden: true }, [h("span", {}, ["Код класса"]), code, copy]);
  const create = /** @type {HTMLButtonElement} */ (h("button", { class: "btn btn-primary", type: "button", disabled: true }, ["Создать класс"]));
  const retry = h("button", { class: "btn", type: "button", hidden: true }, ["Повторить"]);
  const feedback = h("span", { class: "session-feedback", role: "status" });
  root.append(h("div", { class: "session-summary" }, [h("p", { class: "eyebrow" }, ["НАБЛЮДЕНИЕ"]), title, note]), codeBox, create, retry, feedback);

  const dialog = /** @type {HTMLDialogElement} */ (h("dialog", { class: "session-dialog", "aria-labelledby": "session-form-title" }));
  const form = /** @type {HTMLFormElement} */ (h("form"));
  const name = /** @type {HTMLInputElement} */ (h("input", { id: "session-name", required: true, maxlength: "200", placeholder: "Например, информатика · группа 12" }));
  const site = /** @type {HTMLInputElement} */ (h("input", { id: "session-site", type: "url", placeholder: "https://…" }));
  const extra = /** @type {HTMLTextAreaElement} */ (h("textarea", { id: "session-extra", rows: "3", placeholder: "https://login.example.kz/*" }));
  const accept = /** @type {HTMLInputElement} */ (h("input", { type: "checkbox" }));
  const warning = h("label", { class: "session-replace", hidden: true }, [accept, h("span", {}, ["Завершить подключение к текущему классу. Студентам потребуется новый код. Сам экзамен на их компьютерах эта кнопка не завершает."])]);
  const error = h("p", { class: "session-error", role: "alert", hidden: true });
  const submit = /** @type {HTMLButtonElement} */ (h("button", { type: "submit", class: "btn btn-primary" }, ["Создать и получить код"]));
  const cancel = /** @type {HTMLButtonElement} */ (h("button", { type: "button", class: "btn" }, ["Отмена"]));
  form.append(
    h("p", { class: "eyebrow" }, ["ПОДГОТОВКА"]),
    h("h2", { id: "session-form-title" }, ["Новый класс"]),
    h("p", { class: "muted" }, ["Получите код и введите его в приложениях студентов вместе с адресом компьютера преподавателя."]),
    h("label", { class: "session-field", for: name.id }, ["Название", name]),
    h("label", { class: "session-field", for: site.id }, ["Сайт экзамена", h("span", { class: "muted" }, ["Можно указать позже" ]), site]),
    h("details", {}, [h("summary", {}, ["Дополнительные адреса входа и заданий"]), h("label", { class: "session-field", for: extra.id }, ["По одному адресу на строку", extra])]),
    h("p", { class: "session-policy-note" }, ["Адреса передаются клиенту как настройки. Их указание ещё не подтверждает, что переходы на другие сайты заблокированы."]),
    warning, error,
    h("div", { class: "session-actions" }, [cancel, submit]),
  );
  dialog.append(form);
  root.append(dialog);
  dialog.addEventListener("cancel", e => { if (busy) e.preventDefault(); });
  cancel.addEventListener("click", () => dialog.close());
  create.addEventListener("click", () => {
    warning.hidden = session?.state !== "open";
    accept.required = !warning.hidden;
    accept.checked = false;
    error.hidden = true;
    dialog.showModal();
    name.focus();
  });
  retry.addEventListener("click", () => void refresh());
  copy.addEventListener("click", async () => {
    if (!session?.join_code) return;
    try {
      await navigator.clipboard.writeText(session.join_code);
      setText(feedback, "Код скопирован");
    } catch { setText(feedback, "Выделите код и скопируйте его вручную."); }
  });

  /** @param {Response} r */
  async function response(r) {
    if (!r.ok) {
      if (r.status === 401) throw new Error("Войдите в панель заново, чтобы управлять классом.");
      let msg = "Сервер не выполнил запрос. Попробуйте ещё раз.";
      try { const body = await r.json(); msg = body.error?.message_ru || msg; } catch { /* HTTP error */ }
      throw new Error(msg);
    }
    return r.json();
  }

  /** Network/parser messages from the browser are not useful instructions for a teacher. @param {unknown} err */
  function failureText(err) {
    if (err instanceof DOMException && err.name === "TimeoutError") return "Сервер долго не отвечает. Проверьте связь и попробуйте ещё раз.";
    if (err instanceof TypeError) return "Не удалось связаться с сервером класса. Проверьте, что он запущен, и повторите попытку.";
    return err instanceof Error ? err.message : "Не удалось выполнить запрос. Попробуйте ещё раз.";
  }

  async function refresh() {
    if (loading || busy || !alive) return;
    loading = true;
    try {
      const data = await response(await fetch("/api/teacher/session", { cache: "no-store", signal: AbortSignal.timeout(8000) }));
      if (!alive) return;
      session = data;
      const active = session?.state === "open";
      setText(title, active && session ? session.title : "Подключите студентов");
      setText(note, active ? "Введите этот код в приложении студента." : "Создайте класс и получите код подключения.");
      codeBox.hidden = !active;
      setText(code, session?.join_code ?? "—");
      copy.disabled = !session?.join_code;
      setText(create, active ? "Новый класс" : "Создать класс");
      create.disabled = false;
      retry.hidden = true;
    } catch (err) {
      if (!alive) return;
      codeBox.hidden = true;
      create.disabled = true;
      setText(title, "Класс недоступен");
      setText(note, failureText(err));
      retry.hidden = false;
    } finally { loading = false; }
  }

  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (busy || !form.reportValidity()) return;
    error.hidden = true;
    const start = site.value.trim();
    const urls = extra.value.split(/\r?\n/).map(s => s.trim()).filter(Boolean);
    try {
      for (const url of [...urls, ...(start ? [start] : [])]) {
        let parsed;
        try { parsed = new URL(url); }
        catch { throw new Error("Проверьте адрес сайта. Например: https://exam.example.kz"); }
        if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password) throw new Error("Нужен адрес http:// или https:// без логина и пароля в ссылке.");
      }
      if (!name.value.trim()) throw new Error("Введите название класса.");
      if (urls.length > 99) throw new Error("Сократите список до 99 дополнительных адресов.");
      busy = true;
      submit.disabled = cancel.disabled = true;
      setText(submit, "Создаём…");
      const result = await response(await fetch("/api/teacher/session", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: name.value.trim(), mode: "url", allowed_urls: [...new Set([...(start ? [start] : []), ...urls])], ...(start ? { start_url: start } : {}) }),
        signal: AbortSignal.timeout(10000),
      }));
      session = result.session;
      dialog.close();
      setText(feedback, "Класс создан. Можно подключать студентов.");
    } catch (err) {
      setText(error, failureText(err));
      error.hidden = false;
    } finally {
      busy = false;
      submit.disabled = cancel.disabled = false;
      setText(submit, "Создать и получить код");
      void refresh();
    }
  });
  void refresh();
  const timer = setInterval(() => { if (!document.hidden) void refresh(); }, 10000);
  return { root, destroy() { alive = false; clearInterval(timer); if (dialog.open) dialog.close(); } };
}
