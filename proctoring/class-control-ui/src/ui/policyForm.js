// @ts-check
// Policy fields (mode, start URL, allowed addresses, sign-in domains with explanation and suggestions,
// allowed programs, instructions) and a reusable list editor. Server validation errors are shown next to
// the field named in error.details.field.
import { h, icon, nextId, replace, setDisabled, setHidden, setText } from "../dom.js";
import { cleanList, splitInput, str } from "../model.js";
import { setFieldError } from "./common.js";

/**
 * @param {{label: string, hint?: string, placeholder?: string, allowSpaces?: boolean, testid: string, addLabel?: string}} o
 */
export function createListEditor(o) {
  const inputId = nextId("list-in");
  const hintId = nextId("list-hint");
  const errId = nextId("list-err");
  /** @type {string[]} */
  let items = [];
  const list = h("ul", { class: "list-items", "aria-label": o.label, "data-testid": `${o.testid}-items` });
  const input = /** @type {HTMLInputElement} */ (h("input", { id: inputId, type: "text", placeholder: o.placeholder ?? "", "aria-describedby": `${hintId} ${errId}`, "data-testid": `${o.testid}-input`, autocomplete: "off", spellcheck: "false" }));
  const addBtn = h("button", { type: "button", class: "btn btn-small", "data-testid": `${o.testid}-add` }, [icon("plus"), o.addLabel ?? "Добавить"]);
  const err = h("p", { class: "field-error", id: errId, hidden: true, "data-testid": `${o.testid}-error` });
  let disabled = false;
  /** @type {(() => void)|null} */
  let onChange = null;

  const draw = () => {
    replace(
      list,
      items.length
        ? items.map((it, i) => {
            const rm = h("button", { type: "button", class: "icon-btn", "aria-label": `Убрать ${it}`, disabled: disabled }, [icon("close")]);
            rm.addEventListener("click", () => {
              items = items.filter((_, j) => j !== i);
              draw();
              onChange?.();
              input.focus();
            });
            return h("li", { class: "list-item" }, [h("code", {}, [it]), rm]);
          })
        : [h("li", { class: "list-empty" }, ["Список пуст"])],
    );
  };
  const addFromInput = () => {
    const add = splitInput(input.value, { allowSpaces: o.allowSpaces });
    if (!add.length) return;
    items = cleanList([...items, ...add]);
    input.value = "";
    draw();
    onChange?.();
  };
  addBtn.addEventListener("click", addFromInput);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      addFromInput();
    }
  });
  draw();
  const root = h("div", { class: "field list-editor", "data-testid": o.testid }, [
    h("label", { for: inputId }, [o.label]),
    o.hint ? h("p", { class: "hint", id: hintId }, [o.hint]) : h("span", { id: hintId, hidden: true }),
    list,
    h("div", { class: "list-add" }, [input, addBtn]),
    err,
  ]);
  return {
    root,
    input,
    /** Items including text still typed in the input (so "forgot to press Добавить" is not lost). */
    get: () => cleanList([...items, ...splitInput(input.value, { allowSpaces: o.allowSpaces })]),
    /** @param {string[]} next */
    set(next) {
      items = cleanList(next || []);
      input.value = "";
      draw();
    },
    /** @param {string[]} more @returns {string[]} actually added */
    add(more) {
      const before = new Set(items);
      items = cleanList([...items, ...more]);
      draw();
      onChange?.();
      return items.filter((x) => !before.has(x));
    },
    has: (/** @type {string} */ v) => items.includes(v),
    /** @param {string} msg */
    setError: (msg) => setFieldError(input, err, msg),
    /** @param {boolean} on */
    setDisabled(on) {
      if (disabled === on) return;
      disabled = on;
      setDisabled(input, on);
      setDisabled(addBtn, on);
      draw();
    },
    /** @param {() => void} fn */
    onChange(fn) {
      onChange = fn;
    },
  };
}

/**
 * @param {{meta: any, testid: string}} o
 */
export function createPolicyFields(o) {
  const meta = o.meta || {};
  const name = nextId("mode");
  const modeErr = h("p", { class: "field-error", hidden: true });
  const urlRadio = /** @type {HTMLInputElement} */ (h("input", { type: "radio", name, value: "url", id: `${name}-url`, checked: true, "data-testid": "mode-url" }));
  const appRadio = /** @type {HTMLInputElement} */ (h("input", { type: "radio", name, value: "app", id: `${name}-app`, "data-testid": "mode-app" }));
  const modeSet = h("fieldset", { class: "field mode" }, [
    h("legend", {}, ["Как студент проходит экзамен"]),
    h("div", { class: "radio-row" }, [
      h("label", { for: `${name}-url`, class: "radio-card" }, [urlRadio, h("span", {}, [h("strong", {}, [str(meta.modes?.url) || "Внешний сайт"]), h("span", { class: "sub" }, ["тест на сайте; браузер ограничен списком адресов"])])]),
      h("label", { for: `${name}-app`, class: "radio-card" }, [appRadio, h("span", {}, [h("strong", {}, [str(meta.modes?.app) || "Отдельная программа"]), h("span", { class: "sub" }, ["экзамен в программе на компьютере; разрешены только перечисленные программы"])])]),
    ]),
    modeErr,
  ]);

  // ---- url mode
  const startId = nextId("start-url");
  const startErr = h("p", { class: "field-error", hidden: true, "data-testid": "start_url-error" });
  const startUrl = /** @type {HTMLInputElement} */ (h("input", { id: startId, type: "url", placeholder: "https://exam.university.kz/test/42", "aria-describedby": `${startId}-hint`, "data-testid": "start-url", autocomplete: "off", spellcheck: "false" }));
  const allowed = createListEditor({
    label: "Разрешённые адреса",
    hint: "Каждый адрес — сайт и начало пути: https://exam.kz/* (весь сайт), https://exam.kz/test/* (раздел), *.exam.kz (сайт и поддомены). Сайт экзамена сервер добавит сам.",
    placeholder: "https://exam.university.kz/*",
    testid: "allowed-urls",
  });
  const auth = createListEditor({
    label: "Домены входа (авторизация на сайте)",
    hint: "Адреса, через которые проходит вход на сайт экзамена. Добавьте только те, что реально нужны вашему сайту.",
    placeholder: "https://login.university.kz/*",
    testid: "auth-domains",
  });
  const chipsBox = h("div", { class: "chips", "data-testid": "sso-chips" });
  const chipsNote = h("p", { class: "sub", "aria-live": "polite" });
  const suggestions = meta.sso_suggestions && typeof meta.sso_suggestions === "object" ? meta.sso_suggestions : {};
  /** @type {HTMLElement[]} */
  const chipButtons = [];
  for (const [provider, list] of Object.entries(suggestions)) {
    const group = h("div", { class: "chip-group-row" }, [h("span", { class: "chip-provider" }, [`${provider}:`])]);
    for (const url of Array.isArray(list) ? list : []) {
      const b = h("button", { type: "button", class: "chip-btn", "data-suggest": str(url), "aria-label": `Добавить ${str(url)} в домены входа (${provider})` }, [icon("plus", "ico ico-s"), str(url)]);
      b.addEventListener("click", () => {
        const added = auth.add([str(url)]);
        setText(chipsNote, added.length ? `Добавлено: ${str(url)}. Проверьте вход на вашем сайте.` : `${str(url)} уже в списке.`);
        drawChips();
      });
      chipButtons.push(b);
      group.appendChild(b);
    }
    chipsBox.appendChild(group);
  }
  const drawChips = () => {
    for (const b of chipButtons) {
      const v = b.getAttribute("data-suggest") ?? "";
      b.classList.toggle("is-added", auth.has(v));
      b.setAttribute("aria-pressed", auth.has(v) ? "true" : "false");
    }
  };
  auth.onChange(drawChips);
  const authNote = h("div", { class: "auth-note", role: "note", "data-testid": "auth-note" }, [
    icon("info", "ico ico-l"),
    h("div", {}, [
      h("strong", {}, ["Вход на сайт может требовать дополнительных доменов."]),
      " ",
      str(meta.auth_domains_note_ru) || "Вход через внешние сервисы перенаправляет на другие домены; добавьте их в список, иначе студент не сможет войти.",
    ]),
  ]);
  const urlGroup = h("div", { class: "mode-group", "data-testid": "url-group" }, [
    h("div", { class: "field" }, [
      h("label", { for: startId }, ["Адрес экзамена (откроется у студента первым)"]),
      h("p", { class: "hint", id: `${startId}-hint` }, ["Полный адрес страницы теста, https://…"]),
      startUrl,
      startErr,
    ]),
    allowed.root,
    h("section", { class: "auth-block", "aria-label": "Домены входа" }, [
      authNote,
      auth.root,
      Object.keys(suggestions).length
        ? h("div", { class: "field" }, [
            h("p", { class: "hint" }, ["Подсказки (часто встречаются у Google и Microsoft). Сами не добавляются — нажмите, чтобы добавить, и проверьте вход на своём сайте:"]),
            chipsBox,
            chipsNote,
          ])
        : null,
    ]),
  ]);

  // ---- app mode
  const apps = createListEditor({
    label: "Разрешённые программы",
    hint: "Только имя файла программы, без пути: name.exe. Браузеры и системные программы (cmd, PowerShell, Проводник) разрешать нельзя или не стоит — сервер предупредит.",
    placeholder: "testclient.exe",
    allowSpaces: true,
    testid: "allowed-apps",
  });
  const appGroup = h("div", { class: "mode-group", "data-testid": "app-group", hidden: true }, [apps.root]);

  // ---- instructions
  const instrId = nextId("instr");
  const instrErr = h("p", { class: "field-error", hidden: true });
  const instr = /** @type {HTMLTextAreaElement} */ (h("textarea", { id: instrId, rows: "3", maxlength: "2000", "data-testid": "instructions" }));
  const instrField = h("div", { class: "field" }, [h("label", { for: instrId }, ["Инструкция для студента (необязательно)"]), instr, instrErr]);

  const mode = () => (appRadio.checked ? "app" : "url");
  const syncMode = () => {
    setHidden(urlGroup, mode() !== "url");
    setHidden(appGroup, mode() !== "app");
  };
  urlRadio.addEventListener("change", syncMode);
  appRadio.addEventListener("change", syncMode);

  const root = h("div", { class: "policy-fields", "data-testid": o.testid }, [modeSet, urlGroup, appGroup, instrField]);

  /** @type {Record<string, (msg: string) => void>} */
  const errorSetters = {
    mode: (m) => setFieldError(urlRadio, modeErr, m),
    start_url: (m) => setFieldError(startUrl, startErr, m),
    allowed_urls: (m) => allowed.setError(m),
    auth_domains: (m) => auth.setError(m),
    allowed_apps: (m) => apps.setError(m),
    instructions_ru: (m) => setFieldError(instr, instrErr, m),
  };
  /** @type {Record<string, () => HTMLElement>} */
  const focusers = {
    mode: () => (mode() === "app" ? appRadio : urlRadio),
    start_url: () => startUrl,
    allowed_urls: () => allowed.input,
    auth_domains: () => auth.input,
    allowed_apps: () => apps.input,
    instructions_ru: () => instr,
  };

  return {
    root,
    get: () => ({
      mode: mode(),
      start_url: startUrl.value,
      allowed_urls: allowed.get(),
      auth_domains: auth.get(),
      allowed_apps: apps.get(),
      instructions_ru: instr.value,
    }),
    /** @param {any} p policy view or null for a new one */
    set(p) {
      const m = p && p.mode === "app" ? "app" : "url";
      urlRadio.checked = m === "url";
      appRadio.checked = m === "app";
      startUrl.value = p ? str(p.start_url) : "";
      allowed.set(p ? p.allowed_urls || [] : []);
      auth.set(p ? p.auth_domains || [] : []);
      apps.set(p ? p.allowed_apps || [] : []);
      instr.value = p ? str(p.instructions_ru) : "";
      setText(chipsNote, "");
      syncMode();
      drawChips();
    },
    fields: Object.keys(errorSetters),
    /** @param {string} field @param {string} msg */
    setError(field, msg) {
      errorSetters[field]?.(msg);
    },
    clearErrors() {
      for (const fn of Object.values(errorSetters)) fn("");
    },
    /** @param {string} field */
    focus(field) {
      focusers[field]?.().focus();
    },
    /** @param {boolean} on */
    setDisabled(on) {
      for (const el of [urlRadio, appRadio, startUrl, instr]) setDisabled(el, on);
      for (const b of chipButtons) setDisabled(b, on);
      allowed.setDisabled(on);
      auth.setDisabled(on);
      apps.setDisabled(on);
    },
  };
}
