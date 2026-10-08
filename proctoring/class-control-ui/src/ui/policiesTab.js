// @ts-check
// Policies of the selected exam: list with versions, create an extra policy, edit (optimistic `version`),
// assign a policy to the students selected on the "Студенты и команды" tab.
import { h, icon, nextId, replace, setDisabled, setHidden, setText } from "../dom.js";
import { assignmentResultLines, fmtDateTime, isStaleEdit, policyPayload, roleDenial, sentence, str, validateForm } from "../model.js";
import { createResultsBox, sendOneShot, setFieldError } from "./common.js";
import { createPolicyFields } from "./policyForm.js";

/** @param {any} ctx */
export function createPoliciesTab(ctx) {
  const { api, store, announce } = ctx;
  const results = createResultsBox({ testid: "assign-results", announce });
  const list = h("div", { class: "policy-list", "data-testid": "policy-list" });
  const createBtn = h("button", { type: "button", class: "btn", "data-testid": "policy-new" }, [icon("plus"), "Создать дополнительную политику"]);
  const intro = h("p", { class: "hint" }, [
    "Политика — режим экзамена и списки разрешённого. У экзамена есть основная политика; дополнительная нужна, если части студентов разрешено другое. Каждое изменение создаёт новую версию; у студента остаётся применённая версия, пока вы не отправите новую.",
  ]);
  const selNote = h("p", { class: "sel-note", "data-testid": "policy-sel-note" });

  // editor
  /** @type {{policy: any|null}} */
  let editing = { policy: null };
  let editorOpen = false;
  const edHeading = h("h3", {});
  const nameId = nextId("pol-name");
  const nameErr = h("p", { class: "field-error", hidden: true });
  const name = /** @type {HTMLInputElement} */ (h("input", { id: nameId, type: "text", maxlength: "80", "data-testid": "policy-name", autocomplete: "off" }));
  const fields = createPolicyFields({ meta: store.state.meta, testid: "policy-fields" });
  const saveBtn = h("button", { type: "submit", class: "btn btn-primary", "data-testid": "policy-save" }, ["Сохранить политику"]);
  const cancelBtn = h("button", { type: "button", class: "btn" }, ["Закрыть"]);
  const edStatus = h("div", { class: "form-status", "aria-live": "polite", "data-testid": "policy-status" });
  const editor = h("form", { class: "policy-editor", novalidate: true, hidden: true, "data-testid": "policy-editor" }, [
    edHeading,
    h("div", { class: "field" }, [h("label", { for: nameId }, ["Название политики"]), name, nameErr]),
    fields.root,
    h("div", { class: "btn-row form-actions" }, [saveBtn, cancelBtn]),
    edStatus,
  ]);
  editor.addEventListener("submit", (e) => {
    e.preventDefault();
    savePolicy();
  });
  cancelBtn.addEventListener("click", () => closeEditor());
  createBtn.addEventListener("click", () => openEditor(null));

  const root = h("div", { class: "policies-tab" }, [
    h("h2", { class: "panel-title" }, ["Политики экзамена"]),
    intro,
    h("div", { class: "btn-row" }, [createBtn]),
    selNote,
    results.root,
    editor,
    list,
  ]);

  /** @param {any|null} p */
  function openEditor(p) {
    editing = { policy: p };
    editorOpen = true;
    setText(edHeading, p ? `Изменение политики «${str(p.name)}» (сейчас v${str(p.version)})` : "Новая политика");
    name.value = p ? str(p.name) : "";
    fields.set(p);
    fields.clearErrors();
    setFieldError(name, nameErr, "");
    replace(edStatus, []);
    setHidden(editor, false);
    name.focus();
  }

  function closeEditor() {
    editorOpen = false;
    setHidden(editor, true);
  }

  /** @param {any} err */
  function showError(err) {
    if (err.field === "name") setFieldError(name, nameErr, err.message);
    else if (err.field && fields.fields.includes(err.field)) {
      fields.setError(err.field, err.message);
      fields.focus(err.field);
    }
    const stale = isStaleEdit(err);
    /** @type {HTMLElement|null} */
    let reload = null;
    if (stale && editing.policy) {
      const pid = editing.policy.policy_id;
      reload = h("button", { type: "button", class: "btn", "data-testid": "stale-reload" }, ["Загрузить актуальную версию (ваши правки будут сброшены)"]);
      reload.addEventListener("click", async () => {
        await ctx.reloadExam();
        const fresh = (store.state.exam?.policies ?? []).find((/** @type {any} */ x) => x.policy_id === pid);
        if (fresh) openEditor(fresh);
      });
    }
    replace(edStatus, [
      h("div", { class: `status-box ${stale ? "tone-warn" : "tone-bad"}`, role: "alert", "data-testid": stale ? "stale-box" : "error-box" }, [
        icon("warn"),
        h("div", {}, [
          h("strong", {}, [stale ? "Изменено другим действием — обновите." : "Не сохранено."]),
          h("p", {}, [sentence(err.message), stale ? " Ваши правки не сохранены, чужие изменения не перезаписаны." : ""]),
          reload,
        ]),
      ]),
    ]);
  }

  async function savePolicy() {
    const exam = store.state.exam;
    if (!exam) return;
    fields.clearErrors();
    setFieldError(name, nameErr, "");
    const f = fields.get();
    const local = validateForm(f, { withName: true, name: name.value });
    if (Object.keys(local).length) {
      for (const [k, m] of Object.entries(local)) {
        if (k === "name") setFieldError(name, nameErr, m);
        else fields.setError(k, m);
      }
      replace(edStatus, [h("p", { class: "field-error", role: "alert" }, ["Заполните обязательные поля."])]);
      return;
    }
    setDisabled(saveBtn, true);
    const p = editing.policy;
    const r = p
      ? await api.updatePolicy(exam.exam_id, p.policy_id, { version: p.version, name: name.value.trim(), policy: policyPayload(f) })
      : await api.createPolicy(exam.exam_id, { name: name.value.trim(), policy: policyPayload(f) });
    setDisabled(saveBtn, false);
    if (!r.ok) return showError(r.error);
    await ctx.reloadExam();
    editing = { policy: r.data };
    setText(edHeading, `Изменение политики «${str(r.data.name)}» (сейчас v${str(r.data.version)})`);
    const warnings = r.data.warnings_ru || [];
    replace(edStatus, [
      h("div", { class: "status-box tone-ok", role: "status", "data-testid": "saved-box" }, [
        icon("check"),
        h("div", {}, [
          h("strong", {}, [p ? `Сохранено: версия ${str(r.data.version)}.` : `Политика «${str(r.data.name)}» создана (v${str(r.data.version)}).`]),
          h("p", {}, ["Чтобы студенты получили эту версию, выберите их на вкладке «Студенты и команды» и назначьте политику."]),
          warnings.length ? h("div", { class: "warnings", "data-testid": "warnings" }, [h("p", {}, [icon("warn"), "Предупреждения сервера:"]), h("ul", {}, warnings.map((/** @type {string} */ w) => h("li", {}, [w])))]) : null,
        ]),
      ]),
    ]);
    announce("Политика сохранена");
  }

  /** @param {any} p */
  function assign(p) {
    const s = store.state;
    const examId = s.examId;
    const ids = [...s.selected];
    if (!examId || !ids.length) return;
    /** @param {string} id */
    const labelOf = (id) => str(s.students.find((x) => x.student_id === id)?.label) || id;
    const { run } = sendOneShot({
      title: `Назначение политики «${str(p.name)}» v${str(p.version)} → ${ids.length} студ.`,
      results,
      body: { policy_id: p.policy_id, student_ids: ids, deliver_now: true },
      post: (b) => api.assign(examId, b),
      onAnswer: () => {
        ctx.reloadExam();
        ctx.pollNow();
      },
      lines: (d) => assignmentResultLines(d, labelOf),
      note: "Назначение сохранено сервером. Применение у клиента подтверждается отдельно — смотрите колонку «Политика» на вкладке студентов.",
      announce,
    });
    run();
  }

  /** @param {string} label @param {string[]} items */
  const listRow = (label, items) =>
    h("div", { class: "kv-row" }, [h("span", { class: "kv-k" }, [label]), items.length ? h("ul", { class: "inline-list" }, items.map((x) => h("li", {}, [h("code", {}, [x])]))) : h("span", { class: "muted" }, ["—"])]);

  let sig = "";
  let ctxKey = "";
  /** @param {import("../app.js").AppState} s */
  function render(s) {
    const key = `${s.teacher}|${s.examId}`;
    if (key !== ctxKey) {
      if (ctxKey) {
        results.hide();
        closeEditor();
      }
      ctxKey = key;
    }
    const exam = s.exam;
    const denial = roleDenial(s.role, "edit");
    setDisabled(createBtn, !exam || !!denial);
    fields.setDisabled(!!denial);
    setDisabled(name, !!denial);
    setDisabled(saveBtn, !!denial);
    const n = s.selected.size;
    setText(selNote, !exam ? "" : denial ? `${denial}.` : n ? `Выбрано студентов на вкладке «Студенты и команды»: ${n}. Кнопка «Назначить выбранным» отправит им политику.` : "Чтобы назначить политику, отметьте студентов на вкладке «Студенты и команды».");
    if (!exam) {
      if (editorOpen) closeEditor();
      replace(list, [h("p", { class: "empty" }, [s.examError ? "Экзамен недоступен." : "Выберите экзамен."])]);
      sig = "";
      return;
    }
    const counts = new Map();
    for (const sid of exam.students || []) {
      const pid = (exam.assignments || {})[sid] ?? exam.default_policy_id;
      counts.set(pid, (counts.get(pid) ?? 0) + 1);
    }
    const next = JSON.stringify([exam.policies, exam.assignments, exam.students, denial, n]);
    if (next === sig) return;
    sig = next;
    replace(
      list,
      (exam.policies || []).map((/** @type {any} */ p) => {
        const isDefault = p.policy_id === exam.default_policy_id;
        const edit = h("button", { type: "button", class: "btn btn-small", disabled: !!denial, "data-edit-policy": str(p.policy_id) }, ["Изменить"]);
        edit.addEventListener("click", () => openEditor(p));
        const assignBtn = h("button", { type: "button", class: "btn btn-small", disabled: !!denial || n === 0, "data-assign-policy": str(p.policy_id) }, [`Назначить выбранным (${n})`]);
        assignBtn.addEventListener("click", () => assign(p));
        return h("article", { class: "policy-card", "data-policy": str(p.policy_id) }, [
          h("header", { class: "policy-head" }, [
            h("h3", {}, [str(p.name)]),
            h("span", { class: "tag" }, [`версия ${str(p.version)}`]),
            isDefault ? h("span", { class: "tag tag-accent" }, ["основная"]) : null,
            h("span", { class: "tag" }, [str(p.mode_ru)]),
          ]),
          p.mode === "url"
            ? h("div", {}, [
                h("div", { class: "kv-row" }, [h("span", { class: "kv-k" }, ["Адрес экзамена"]), h("code", {}, [str(p.start_url)])]),
                listRow("Разрешённые адреса", p.allowed_urls || []),
                listRow("Домены входа", p.auth_domains || []),
                h("div", { class: "kv-row" }, [h("span", { class: "kv-k" }, ["Таймер сайта"]), h("span", {}, ["не управляется: интеграции с сайтом нет, блокировка его не останавливает"])]),
              ])
            : listRow("Разрешённые программы", p.allowed_apps || []),
          p.instructions_ru ? h("div", { class: "kv-row" }, [h("span", { class: "kv-k" }, ["Инструкция"]), h("span", {}, [str(p.instructions_ru)])]) : null,
          (p.warnings_ru || []).length ? h("ul", { class: "warn-list" }, p.warnings_ru.map((/** @type {string} */ w) => h("li", {}, [icon("warn"), w]))) : null,
          h("p", { class: "sub" }, [`Студентов с этой политикой: ${counts.get(p.policy_id) ?? 0} · изменена ${fmtDateTime(p.updated_at)} · ${str(p.updated_by)}`]),
          h("div", { class: "btn-row" }, [edit, assignBtn]),
        ]);
      }),
    );
  }

  return { root, render };
}
