// @ts-check
// Exam conditions: create a new exam or edit the selected one (title, staff, default policy).
// Edits are optimistic: PATCH exam with `revision`, PATCH default policy with `version`; a 409 stale_edit
// is shown as "изменено другим действием — обновите" and nothing is overwritten.
import { h, icon, nextId, replace, setDisabled, setHidden, setText } from "../dom.js";
import { examCreatePayload, examEditPayloads, isStaleEdit, roleDenial, ROLE_RU, sentence, str, validateForm } from "../model.js";
import { setFieldError } from "./common.js";
import { createPolicyFields } from "./policyForm.js";

/** @param {any} ctx */
export function createExamTab(ctx) {
  const { api, store, announce } = ctx;
  /** @type {"create"|"edit"} */
  let mode = "edit";
  /** exam view the edit form was filled from (its revision/version are sent back) @type {any} */
  let base = null;
  let dirty = false;
  let busy = false;

  const heading = h("h2", { class: "panel-title" });
  const roleNote = h("p", { class: "role-note", role: "note", hidden: true, "data-testid": "exam-role-note" });

  const titleId = nextId("title");
  const titleErr = h("p", { class: "field-error", hidden: true, "data-testid": "title-error" });
  const title = /** @type {HTMLInputElement} */ (h("input", { id: titleId, type: "text", maxlength: "200", required: true, "data-testid": "exam-title", autocomplete: "off" }));
  const policy = createPolicyFields({ meta: store.state.meta, testid: "exam-policy" });

  // staff editor
  const staffRows = h("ul", { class: "staff-rows", "data-testid": "staff-rows" });
  const staffAdd = h("button", { type: "button", class: "btn btn-small" }, [icon("plus"), "Добавить сотрудника"]);
  const staffErr = h("p", { class: "field-error", hidden: true });
  const staffNote = h("p", { class: "hint" });
  /** @type {{id: HTMLInputElement, role: HTMLSelectElement, li: HTMLElement}[]} */
  let staff = [];
  const addStaffRow = (/** @type {string} */ id = "", /** @type {string} */ role = "observer") => {
    const idIn = /** @type {HTMLInputElement} */ (h("input", { type: "text", value: id, "aria-label": "Идентификатор сотрудника", placeholder: "t-ivanov", autocomplete: "off" }));
    const roleSel = /** @type {HTMLSelectElement} */ (h("select", { "aria-label": "Роль сотрудника" }, ["teacher", "assistant", "observer"].map((r) => h("option", { value: r }, [ROLE_RU[/** @type {"teacher"} */ (r)]]))));
    roleSel.value = role;
    const rm = h("button", { type: "button", class: "icon-btn", "aria-label": "Убрать сотрудника" }, [icon("close")]);
    const li = h("li", { class: "staff-row" }, [idIn, roleSel, rm]);
    const entry = { id: idIn, role: roleSel, li };
    rm.addEventListener("click", () => {
      staff = staff.filter((x) => x !== entry);
      li.remove();
      dirty = true;
    });
    for (const el of [idIn, roleSel]) el.addEventListener("input", () => (dirty = true));
    staff.push(entry);
    staffRows.appendChild(li);
  };
  staffAdd.addEventListener("click", () => {
    addStaffRow();
    staff[staff.length - 1].id.focus();
  });
  const staffSet = h("fieldset", { class: "field staff" }, [
    h("legend", {}, ["Сотрудники экзамена"]),
    h("p", { class: "hint" }, ["Преподаватель — меняет условия и отправляет команды; ассистент — только команды; наблюдатель — только просмотр."]),
    staffRows,
    staffAdd,
    staffNote,
    staffErr,
  ]);

  const saveBtn = h("button", { type: "submit", class: "btn btn-primary", "data-testid": "exam-save", "data-nodirty": true });
  const resetBtn = h("button", { type: "button", class: "btn", "data-nodirty": true }, ["Отменить правки"]);
  const formStatus = h("div", { class: "form-status", "aria-live": "polite", "data-testid": "exam-status" });
  const form = h("form", { class: "exam-form", novalidate: true, "data-testid": "exam-form" }, [
    h("div", { class: "field" }, [h("label", { for: titleId }, ["Название экзамена"]), title, titleErr]),
    policy.root,
    staffSet,
    h("div", { class: "btn-row form-actions" }, [saveBtn, resetBtn]),
    formStatus,
  ]);
  form.addEventListener("input", () => (dirty = true));
  form.addEventListener("click", (e) => {
    const t = /** @type {HTMLElement} */ (e.target);
    if (t && t.closest && t.closest("button") && !t.closest("[data-nodirty], .form-status")) dirty = true;
  });
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    save();
  });
  resetBtn.addEventListener("click", () => {
    if (mode === "create") {
      fillCreate();
      if (store.state.exam) startEdit();
    } else fillFrom(store.state.exam);
    setText(formStatus, "");
  });

  const empty = h("p", { class: "empty", hidden: true });
  const root = h("div", { class: "exam-tab" }, [heading, roleNote, empty, form]);

  // ------------------------------------------------------------ fill
  function fillCreate() {
    base = null;
    title.value = "";
    policy.set(null);
    replace(staffRows, []);
    staff = [];
    clearErrors();
    dirty = false;
  }

  /** @param {any} exam */
  function fillFrom(exam) {
    base = exam;
    clearErrors();
    if (!exam) return;
    title.value = str(exam.title);
    const p = (exam.policies || []).find((/** @type {any} */ x) => x.policy_id === exam.default_policy_id);
    policy.set(p ?? null);
    replace(staffRows, []);
    staff = [];
    for (const [id, role] of Object.entries(exam.staff || {})) addStaffRow(id, str(role));
    dirty = false;
  }

  function clearErrors() {
    policy.clearErrors();
    setFieldError(title, titleErr, "");
    setText(staffErr, "");
    setHidden(staffErr, true);
  }

  function startCreate() {
    mode = "create";
    fillCreate();
    setText(formStatus, "");
    render(store.state);
    title.focus();
  }

  function startEdit() {
    mode = "edit";
    fillFrom(store.state.exam);
    render(store.state);
  }

  // ------------------------------------------------------------ errors
  /** @param {any} err */
  function showError(err) {
    const field = err.field;
    if (field === "title") setFieldError(title, titleErr, err.message);
    else if (field === "staff") {
      setText(staffErr, err.message);
      setHidden(staffErr, false);
    } else if (field && policy.fields.includes(field)) {
      policy.setError(field, err.message);
      policy.focus(field);
    }
    if (field === "title") title.focus();
    const stale = isStaleEdit(err);
    /** @type {HTMLElement|null} */
    let reload = null;
    if (stale) {
      reload = h("button", { type: "button", class: "btn", "data-testid": "stale-reload" }, ["Загрузить актуальную версию (ваши правки будут сброшены)"]);
      reload.addEventListener("click", async () => {
        await ctx.reloadExam();
        await ctx.reloadExams();
        startEdit();
        setText(formStatus, "");
        announce("Загружена актуальная версия экзамена");
      });
    }
    replace(formStatus, [
      h("div", { class: `status-box ${stale ? "tone-warn" : "tone-bad"}`, role: "alert", "data-testid": stale ? "stale-box" : "error-box" }, [
        icon("warn"),
        h("div", {}, [
          h("strong", {}, [stale ? "Изменено другим действием — обновите." : field ? "Исправьте поле, отмеченное ниже." : "Не сохранено."]),
          h("p", {}, [sentence(err.message), stale ? " Ваши правки не сохранены, чужие изменения не перезаписаны." : ""]),
          reload,
        ]),
      ]),
    ]);
  }

  /** @param {string} headline @param {string[]} warnings @param {string[]} notes */
  function showSaved(headline, warnings, notes) {
    replace(formStatus, [
      h("div", { class: "status-box tone-ok", role: "status", "data-testid": "saved-box" }, [
        icon("check"),
        h("div", {}, [
          h("strong", {}, [headline]),
          ...notes.map((n) => h("p", {}, [n])),
          warnings.length
            ? h("div", { class: "warnings", "data-testid": "warnings" }, [h("p", {}, [icon("warn"), "Предупреждения сервера:"]), h("ul", {}, warnings.map((w) => h("li", {}, [w])))])
            : null,
        ]),
      ]),
    ]);
  }

  // ------------------------------------------------------------ save
  async function save() {
    if (busy) return;
    clearErrors();
    const f = { title: title.value, staff: staff.map((x) => ({ id: x.id.value, role: x.role.value })), ...policy.get() };
    const local = validateForm(f, { withTitle: true });
    if (Object.keys(local).length) {
      const [field] = Object.keys(local);
      for (const [k, m] of Object.entries(local)) {
        if (k === "title") setFieldError(title, titleErr, m);
        else policy.setError(k, m);
      }
      if (field === "title") title.focus();
      else policy.focus(field);
      replace(formStatus, [h("p", { class: "field-error", role: "alert" }, ["Заполните обязательные поля."])]);
      return;
    }
    busy = true;
    setDisabled(saveBtn, true);
    try {
      if (mode === "create") {
        const r = await api.createExam(examCreatePayload(f));
        if (!r.ok) return showError(r.error);
        const exam = r.data;
        const p = (exam.policies || [])[0];
        announce(`Экзамен «${str(exam.title)}» создан`);
        await ctx.reloadExams();
        ctx.selectExam(str(exam.exam_id));
        mode = "edit";
        fillFrom(exam);
        showSaved(`Экзамен создан: «${str(exam.title)}».`, p ? p.warnings_ru || [] : [], [
          `Политика «${str(p?.name)}» v${str(p?.version)} (${str(p?.mode_ru)}). Студенты получат её при подключении к экзамену.`,
        ]);
        return;
      }
      const exam = base;
      if (!exam) return;
      const isOwner = !store.state.teacher || exam.owner_id === store.state.teacher;
      const { examPatch, policyPatch, policyId } = examEditPayloads(f, exam, { canEditStaff: isOwner });
      if (!examPatch && !policyPatch) {
        replace(formStatus, [h("p", { class: "muted" }, ["Изменений нет — нечего сохранять."])]);
        return;
      }
      /** @type {string[]} */
      const notes = [];
      /** @type {string[]} */
      let warnings = [];
      if (examPatch) {
        const r = await api.updateExam(exam.exam_id, examPatch);
        if (!r.ok) return showError(r.error);
        notes.push(`Экзамен сохранён, ревизия ${str(r.data.revision)}.`);
      }
      if (policyPatch && policyId) {
        const r = await api.updatePolicy(exam.exam_id, policyId, policyPatch);
        if (!r.ok) {
          await ctx.reloadExam();
          base = store.state.exam ?? base;
          return showError(r.error);
        }
        warnings = r.data.warnings_ru || [];
        notes.push(
          `Политика «${str(r.data.name)}» сохранена как версия ${str(r.data.version)}. Студенты, уже получившие прежнюю версию, получат новую после назначения (вкладка «Студенты и команды» → «Назначить политику») или при следующем подключении.`,
        );
      }
      await ctx.reloadExam();
      await ctx.reloadExams();
      fillFrom(store.state.exam);
      showSaved("Сохранено.", warnings, notes);
      announce("Условия экзамена сохранены");
    } finally {
      busy = false;
      render(store.state);
    }
  }

  // ------------------------------------------------------------ render
  /** @param {import("../app.js").AppState} s */
  function render(s) {
    if (mode === "edit" && s.exam && (!base || (base.exam_id !== s.exam.exam_id) || (!dirty && base.revision !== s.exam.revision))) fillFrom(s.exam);
    if (mode === "edit" && !s.exam) base = null;
    const creating = mode === "create";
    setText(heading, creating ? "Новый экзамен" : s.exam ? `Условия экзамена «${str(s.exam.title)}»` : "Условия экзамена");
    const noExam = !creating && !s.exam;
    setHidden(form, noExam);
    setHidden(empty, !noExam);
    setText(empty, s.examError ? "Экзамен недоступен." : "Выберите экзамен или нажмите «Создать экзамен».");
    const canCreate = !s.teacher || s.teachers[s.teacher]?.role === "teacher";
    const denial = creating ? (canCreate ? null : "Создавать экзамены может только преподаватель") : roleDenial(s.role, "edit");
    setHidden(roleNote, !denial);
    setText(roleNote, denial ? `${denial}. Поля доступны только для просмотра; сервер отклонит изменения.` : "");
    const isOwner = creating || !s.teacher || (s.exam && s.exam.owner_id === s.teacher);
    for (const el of [title, saveBtn, resetBtn]) setDisabled(el, !!denial || busy);
    policy.setDisabled(!!denial);
    for (const x of staff) {
      setDisabled(x.id, !!denial || !isOwner);
      setDisabled(x.role, !!denial || !isOwner);
    }
    setDisabled(staffAdd, !!denial || !isOwner);
    setText(staffNote, isOwner ? "" : "Состав сотрудников меняет только владелец экзамена.");
    setText(saveBtn, creating ? "Создать экзамен" : "Сохранить изменения");
    setText(resetBtn, creating ? "Отмена" : "Отменить правки");
  }

  return { root, render, startCreate };
}
