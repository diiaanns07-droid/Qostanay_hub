// @ts-check
// Shared UI parts: modal shell, results box for POST answers, sending of one-shot (idempotent) requests.
import { h, icon, nextId, replace, setHidden, setText, trapFocus } from "../dom.js";
import { commandStatus, fmtTime, OneShotRequest, sentence } from "../model.js";

/**
 * Modal dialog shell (role=dialog, focus trap, Escape, focus returns to the opener).
 * @param {{title: string, testid?: string, wide?: boolean}} o
 */
export function createModal(o) {
  const titleId = nextId("dlg-title");
  const overlay = h("div", { class: "overlay", hidden: true });
  const titleEl = h("h2", { id: titleId }, [o.title]);
  const body = h("div", { class: "dlg-body" });
  const foot = h("div", { class: "dlg-foot" });
  const closeBtn = h("button", { type: "button", class: "icon-btn", "aria-label": "Закрыть" }, [icon("close")]);
  const box = h("div", { class: `dialog${o.wide ? " dialog-wide" : ""}`, role: "dialog", "aria-modal": "true", "aria-labelledby": titleId, tabindex: "-1", "data-testid": o.testid ?? null }, [
    h("header", { class: "dlg-head" }, [titleEl, closeBtn]),
    body,
    foot,
  ]);
  overlay.appendChild(box);
  /** @type {HTMLElement|null} */
  let returnTo = null;
  /** @type {(() => void)|null} */
  let onClose = null;
  const close = () => {
    if (overlay.hasAttribute("hidden")) return;
    setHidden(overlay, true);
    document.body.classList.remove("modal-open");
    const fn = onClose;
    onClose = null;
    if (fn) fn();
    if (returnTo && document.contains(returnTo)) returnTo.focus();
    returnTo = null;
  };
  closeBtn.addEventListener("click", close);
  // Escape also works when focus fell out of the dialog (e.g. the focused element was re-rendered away)
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || overlay.hasAttribute("hidden") || box.contains(/** @type {Node} */ (e.target))) return;
    e.preventDefault();
    close();
  });
  overlay.addEventListener("mousedown", (e) => {
    if (e.target === overlay) close();
  });
  trapFocus(box, close);
  document.body.appendChild(overlay);
  return {
    overlay,
    box,
    body,
    foot,
    /** @param {string} t */
    setTitle: (t) => setText(titleEl, t),
    /** @param {HTMLElement|null} focusEl @param {(() => void)|null} [closeCb] */
    open(focusEl, closeCb = null) {
      returnTo = /** @type {HTMLElement|null} */ (document.activeElement instanceof HTMLElement ? document.activeElement : null);
      onClose = closeCb;
      setHidden(overlay, false);
      document.body.classList.add("modal-open");
      (focusEl ?? box).focus();
    },
    close,
    get isOpen() {
      return !overlay.hasAttribute("hidden");
    },
  };
}

const OUTCOME_LOOK = {
  created: { icon: "clock", tone: "info", word: "принята" },
  assigned: { icon: "clock", tone: "info", word: "назначена" },
  repeat: { icon: "info", tone: "muted", word: "повтор" },
  unavailable: { icon: "slash", tone: "warn", word: "недоступно" },
};

/**
 * Box that shows the server's answer to the last POST of this tab, per student, and offers a retry
 * with the SAME idempotency key when no answer was received.
 * @param {{testid: string, announce: (t: string) => void}} o
 */
export function createResultsBox(o) {
  const root = h("section", { class: "results", "aria-live": "polite", "data-testid": o.testid, hidden: true });
  /** @type {(() => void)|null} */
  let retryFn = null;

  /** @param {string} title @param {string} [note] */
  function busy(title, note = "Отправляем запрос серверу…") {
    setHidden(root, false);
    root.className = "results";
    replace(root, [h("h3", {}, [title]), h("p", { class: "muted" }, [note])]);
  }

  /**
   * @param {string} title
   * @param {{label: string, outcome: string, text: string, command?: any}[]} lines
   * @param {string} note
   */
  function show(title, lines, note) {
    setHidden(root, false);
    root.className = "results";
    replace(root, [
      h("h3", {}, [title]),
      h("p", { class: "results-note" }, [icon("info"), note]),
      h(
        "ul",
        { class: "results-list" },
        lines.map((l) => {
          const look = OUTCOME_LOOK[/** @type {"created"} */ (l.outcome)] ?? OUTCOME_LOOK.repeat;
          return h("li", { class: `res res-${l.outcome}`, "data-outcome": l.outcome }, [
            h("span", { class: `chip tone-${look.tone}` }, [icon(look.icon), h("span", { class: "chip-group" }, [look.word])]),
            h("span", { class: "res-who" }, [l.label]),
            h("span", { class: "res-text" }, [l.text]),
          ]);
        }),
      ),
    ]);
  }

  /** @param {string} title @param {any} err @param {(() => void)|null} retry */
  function error(title, err, retry) {
    setHidden(root, false);
    root.className = "results results-error";
    retryFn = retry;
    const network = err && err.code === "network";
    const btn = retry ? h("button", { type: "button", class: "btn", "data-testid": "retry-same" }, ["Повторить тот же запрос"]) : null;
    if (btn) btn.addEventListener("click", () => retryFn && retryFn());
    replace(root, [
      h("h3", {}, [title]),
      h("p", { role: "alert" }, [
        icon("warn"),
        err ? sentence(err.message) : "Ошибка.",
        network ? " Запрос мог как дойти, так и не дойти до сервера. Повтор отправит тот же запрос с тем же ключом повтора — сервер не создаст вторую команду." : "",
      ]),
      btn,
    ]);
  }

  function hide() {
    setHidden(root, true);
  }

  return { root, busy, show, error, hide };
}

/**
 * Send a one-shot request: one click = one idempotency key; retry after a network error resends the same body.
 * @param {{
 *   title: string, results: ReturnType<typeof createResultsBox>, body: Record<string, unknown>,
 *   post: (body: any) => Promise<any>, onAnswer: (data: any) => void, lines: (data: any) => any[], note: string,
 *   announce: (t: string) => void
 * }} o
 */
export function sendOneShot(o) {
  const req = new OneShotRequest(o.body);
  const run = async () => {
    o.results.busy(o.title, req.tries ? `Повторяем запрос (тот же ключ повтора, попытка ${req.tries + 1})…` : undefined);
    const r = await req.send(o.post);
    if (r.ok) {
      o.onAnswer(r.data);
      o.results.show(`${o.title} · ответ сервера ${fmtTime(new Date().toISOString())}`, o.lines(r.data), o.note);
      o.announce(`${o.title}: сервер ответил`);
    } else {
      o.results.error(o.title, r.error, req.retryable ? run : null);
      o.announce(`${o.title}: ${r.error.message}`);
    }
    return r;
  };
  return { req, run };
}

/** Small "last command" block: chip (shape+colour+group) + server label + who/when. @param {any} cmd */
export function lastCommandBlock(cmd) {
  const st = commandStatus(cmd);
  if (!st) return [h("span", { class: "muted" }, ["команд не было"])];
  return [
    h("span", { class: `chip tone-${st.tone}`, "data-group": st.group, "data-testid": "cmd-chip" }, [icon(st.icon), h("span", { class: "chip-group" }, [st.groupLabel])]),
    h("span", { class: "cmd-label", "data-testid": "cmd-label" }, [st.label]),
    h("span", { class: "sub" }, [`${String(cmd.kind_ru ?? cmd.kind)} · ${fmtTime(cmd.issued_at)} · ${String(cmd.issued_by_name ?? "")}`]),
  ];
}

/**
 * Field error helpers for forms.
 * @param {HTMLElement} control @param {HTMLElement} errEl @param {string} msg
 */
export function setFieldError(control, errEl, msg) {
  setText(errEl, msg);
  setHidden(errEl, !msg);
  if (msg) control.setAttribute("aria-invalid", "true");
  else control.removeAttribute("aria-invalid");
}
