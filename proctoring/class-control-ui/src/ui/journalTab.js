// @ts-check
// Journal: who (teacher or client/system), to whom, when, what (action, command, reason) and the result.
// Append-only on the server; the panel only reads it (GET .../journal, newest first).
import { h, replace, setDisabled, setText } from "../dom.js";
import { journalRow, KIND_RU, str } from "../model.js";

/** @param {any} ctx */
export function createJournalTab(ctx) {
  const { api, store } = ctx;
  const filter = /** @type {HTMLSelectElement} */ (h("select", { id: "journal-student", "data-testid": "journal-filter" }));
  const auto = /** @type {HTMLInputElement} */ (h("input", { type: "checkbox", id: "journal-auto", checked: true }));
  const refreshBtn = h("button", { type: "button", class: "btn btn-small", "data-testid": "journal-refresh" }, ["Обновить"]);
  const status = h("p", { class: "muted", role: "status", "data-testid": "journal-status" });
  const tbody = h("tbody");
  const table = h("table", { class: "journal", "data-testid": "journal-table" }, [
    h("caption", { class: "sr-only" }, ["Журнал действий: когда, кто, кому, что, результат"]),
    h("thead", {}, [h("tr", {}, ["Когда", "Кто", "Кому", "Что", "Результат"].map((t) => h("th", { scope: "col" }, [t])))]),
    tbody,
  ]);
  const root = h("div", { class: "journal-tab" }, [
    h("h2", { class: "panel-title" }, ["Журнал"]),
    h("p", { class: "hint" }, ["Журнал ведёт сервер и не позволяет его менять. «клиент/система» — изменения, о которых сообщил клиент студента или сам сервер (доставка, подтверждение, истечение срока)."]),
    h("div", { class: "toolbar-row" }, [
      h("label", { for: "journal-student" }, ["Студент"]),
      filter,
      h("label", { class: "check", for: "journal-auto" }, [auto, " обновлять автоматически"]),
      refreshBtn,
    ]),
    status,
    h("div", { class: "table-wrap" }, [table]),
  ]);

  let loading = false;
  let last = 0;
  let filterSig = "";
  refreshBtn.addEventListener("click", () => load());
  filter.addEventListener("change", () => load());

  async function load() {
    const s = store.state;
    if (!s.examId || s.examError || loading) return;
    loading = true;
    last = Date.now();
    const sid = filter.value || null;
    const examId = s.examId;
    const r = await api.journal(examId, { student_id: sid, limit: 300 });
    loading = false;
    if (store.state.examId !== examId) return;
    if (!r.ok) {
      setText(status, r.error.message);
      return;
    }
    const entries = Array.isArray(r.data) ? r.data : [];
    const kinds = { ...KIND_RU, ...(store.state.meta?.kinds ?? {}) };
    setText(status, entries.length ? `Записей: ${entries.length} (новые сверху), обновлено ${new Date().toLocaleTimeString("ru-RU")}` : "Записей нет.");
    replace(
      tbody,
      entries.map((e) => {
        const row = journalRow(e, kinds);
        return h("tr", { "data-seq": str(row.seq), "data-journal-action": str(e.action) }, [
          h("td", { class: "nowrap" }, [h("time", { datetime: row.whenIso }, [row.when])]),
          h("td", { class: row.byTeacher ? "who-teacher" : "who-system" }, [row.who]),
          h("td", {}, [row.whom]),
          h("td", {}, [row.what]),
          h("td", {}, [row.result]),
        ]);
      }),
    );
  }

  /** @param {import("../app.js").AppState} s */
  function render(s) {
    const opts = [["", "Все студенты"], ...s.students.map((x) => [str(x.student_id), str(x.label) || str(x.student_id)])];
    const sig = JSON.stringify(opts);
    if (sig !== filterSig) {
      const keep = filter.value;
      filter.replaceChildren(...opts.map(([v, t]) => h("option", { value: v }, [t])));
      filter.value = opts.some(([v]) => v === keep) ? keep : "";
      filterSig = sig;
    }
    setDisabled(refreshBtn, !s.examId || !!s.examError);
    if (s.tab !== "journal") return;
    if (!s.examId || s.examError) {
      replace(tbody, []);
      setText(status, s.examError ? "Журнал недоступен: нет доступа к экзамену." : "Выберите экзамен.");
      return;
    }
    if (auto.checked && Date.now() - last > 2000) load();
    else if (!last) load();
  }

  return { root, render };
}
