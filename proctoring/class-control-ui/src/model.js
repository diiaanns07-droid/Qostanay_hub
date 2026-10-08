// @ts-check
// Pure logic of the T04 teacher interface (no DOM, no network): status mapping, form -> API payloads,
// permissions, error normalization, idempotency keys, journal rows. Unit-tested with node --test.
//
// Rule of this file: anything that says "выполнено" / "заблокирован" comes from the server
// (command.group / command.label_ru / lock.label_ru). Nothing here derives a result from an HTTP status.

export const API_BASE = "/api/teacher/control";
export const TEACHER_KINDS = /** @type {const} */ (["start_exam", "lock", "unlock", "finish_exam"]);

/** Fallback labels (the server sends its own in GET /meta `kinds`). */
export const KIND_RU = {
  start_exam: "Начать экзамен и наблюдение",
  finish_exam: "Завершить экзамен",
  lock: "Заблокировать",
  unlock: "Разблокировать",
  apply_policy: "Применить политику",
};

/** The five teacher-facing groups of a command (+ cancelled). Shape of the icon differs per group. */
export const STATUS_GROUPS = {
  pending: { label: "ожидает доставки", icon: "clock", tone: "info" },
  executing: { label: "выполняется", icon: "run", tone: "info" },
  done: { label: "выполнено", icon: "check", tone: "ok" },
  error: { label: "ошибка", icon: "octagon", tone: "bad" },
  no_connection: { label: "нет связи", icon: "dashed", tone: "warn" },
  cancelled: { label: "отменена", icon: "slash", tone: "muted" },
};

/** Server command states (history[].state, journal details.state) in Russian. */
export const STATE_RU = {
  queued: "принята сервером",
  sent: "отправлена клиенту",
  executing: "клиент выполняет",
  succeeded: "выполнено (подтверждено клиентом)",
  failed: "ошибка на клиенте",
  rejected: "отклонена клиентом",
  expired: "срок действия истёк",
  unconfirmed: "нет ответа клиента",
  lost: "связь потеряна после отправки",
  superseded: "заменена более новой командой",
  cancelled: "отменена",
};

export const ROLE_RU = {
  teacher: "преподаватель",
  assistant: "ассистент",
  observer: "наблюдатель (только просмотр)",
  none: "нет доступа",
  unknown: "проверяет сервер",
};

export const SIM_BEHAVIOUR_RU = {
  success: "успех (подтверждает через 0,6 с)",
  delay: "задержка ответа (12 с)",
  error: "ошибка выполнения",
  unsupported: "не поддерживает блокировку",
  disconnect_before_exec: "обрыв связи до выполнения",
  disconnect_after_exec: "обрыв связи после выполнения",
  no_ack: "не отвечает (нет подтверждения)",
  v1: "клиент v1 без списка возможностей",
  offline: "нет связи",
};

export const EXAM_STATE_RU = {
  idle: "не начат",
  preflight: "проверка перед экзаменом",
  calibrating: "калибровка",
  running: "идёт",
  paused: "пауза",
  finished: "завершён",
};

/** @param {unknown} v */
export const str = (v) => (v === null || v === undefined ? "" : String(v));

/** Server messages are often without a final period; make them a sentence before adding text. @param {unknown} t */
export function sentence(t) {
  const s = str(t).trim();
  return !s || /[.!?…»)]$/.test(s) ? s : `${s}.`;
}

// ------------------------------------------------------------------ command / lock status

/**
 * Badge for a command view from the server. Unknown groups are shown as unknown, never as done.
 * @param {any} cmd
 */
export function commandStatus(cmd) {
  if (!cmd || typeof cmd !== "object") return null;
  const g = STATUS_GROUPS[/** @type {keyof typeof STATUS_GROUPS} */ (cmd.group)];
  if (!g) return { group: "unknown", groupLabel: "состояние неизвестно", label: str(cmd.label_ru), icon: "question", tone: "muted" };
  return { group: String(cmd.group), groupLabel: g.label, label: str(cmd.label_ru), icon: g.icon, tone: g.tone };
}

/**
 * Lock column: the text is EXACTLY lock.label_ru; only the icon/tone follow lock.state.
 * @param {any} lock
 */
export function lockStatus(lock) {
  if (!lock || typeof lock !== "object" || typeof lock.label_ru !== "string") {
    return { state: "unknown", label: "Нет данных о блокировке", icon: "question", tone: "muted" };
  }
  const look = {
    locked: { icon: "lock", tone: "bad" },
    unlocked: { icon: "unlock", tone: "ok" },
    lock_pending: { icon: "hourglass", tone: "info" },
    unlock_pending: { icon: "hourglass", tone: "info" },
  }[/** @type {string} */ (lock.state)] ?? { icon: "question", tone: "muted" };
  return { state: str(lock.state) || "unknown", label: lock.label_ru, ...look };
}

/**
 * Latest command of a student: the polled list (newest first) merged with commands returned by our own
 * POST (server-reported state at creation; replaced as soon as the poll knows the command).
 * @param {any[]} polled @param {any|undefined} posted
 */
export function latestCommand(polled, posted) {
  const top = Array.isArray(polled) && polled.length ? polled[0] : null;
  if (!posted) return top;
  if (Array.isArray(polled) && polled.some((c) => c && c.command_id === posted.command_id)) return top;
  if (!top) return posted;
  return Number(posted.seq) > Number(top.seq) ? posted : top;
}

/** @param {any} s student view */
export function connectionText(s, fmt = fmtTime) {
  if (!s) return "нет данных";
  if (s.connected) return s.connected_at ? `на связи с ${fmt(s.connected_at)}` : "на связи";
  return s.disconnected_at ? `нет связи с ${fmt(s.disconnected_at)}` : "нет связи";
}

// ------------------------------------------------------------------ availability / permissions

const RANK = { observer: 0, assistant: 1, teacher: 2 };

/**
 * Effective role of the current teacher on an exam, mirroring backend access.check():
 * the lower of the exam role (owner = teacher, staff map) and the account role.
 * Returns "unknown" when the panel does not know who is logged in (real server, PIN cookie).
 * @param {any} exam @param {string|null} teacherId @param {string|null|undefined} accountRole
 */
export function effectiveRole(exam, teacherId, accountRole) {
  if (!exam || !teacherId) return "unknown";
  const examRole = exam.owner_id === teacherId ? "teacher" : exam.staff && typeof exam.staff === "object" ? exam.staff[teacherId] : undefined;
  if (!examRole || !(examRole in RANK)) return "none";
  if (!accountRole || !(accountRole in RANK)) return examRole;
  return RANK[/** @type {keyof typeof RANK} */ (accountRole)] < RANK[/** @type {keyof typeof RANK} */ (examRole)] ? accountRole : examRole;
}

/**
 * null = allowed (or unknown: the server decides); otherwise the Russian reason shown next to the control.
 * @param {string} role @param {"view"|"command"|"edit"} action
 */
export function roleDenial(role, action) {
  if (role === "unknown") return null;
  if (role === "none") return "Нет доступа к этому экзамену";
  if (action === "view") return null;
  if (role === "teacher") return null;
  if (role === "assistant") return action === "command" ? null : "Ассистент не меняет условия экзамена и политики";
  return "Только просмотр: ваша роль в этом экзамене — наблюдатель";
}

/**
 * Split selected students into "will be sent" and "unavailable (why)" BEFORE sending a bulk command.
 * @param {any[]} students @param {Iterable<string>} ids @param {string} kind
 */
export function splitByAvailability(students, ids, kind) {
  /** @type {{student_id: string, label: string}[]} */
  const available = [];
  /** @type {{student_id: string, label: string, reason: string}[]} */
  const unavailable = [];
  const byId = new Map((students || []).map((s) => [s.student_id, s]));
  for (const id of ids) {
    const s = byId.get(id);
    if (!s) {
      unavailable.push({ student_id: id, label: id, reason: "Студента больше нет в списке экзамена" });
      continue;
    }
    const label = str(s.label) || id;
    const a = s.actions && s.actions[kind];
    if (!a || typeof a.available !== "boolean") unavailable.push({ student_id: id, label, reason: "Сервер не сообщил, доступно ли действие" });
    else if (!a.available) unavailable.push({ student_id: id, label, reason: str(a.reason_ru) || "Действие недоступно" });
    else available.push({ student_id: id, label });
  }
  return { available, unavailable };
}

// ------------------------------------------------------------------ lock reason

const CONTROL_CHARS = /[\x00-\x08\x0b-\x1f\x7f]/g;

/** Same cleaning as the server (control characters removed, trimmed). @param {unknown} text */
export function cleanReason(text) {
  return str(text).replace(CONTROL_CHARS, "").trim();
}

/** @param {unknown} text @param {number} [max] */
export function reasonCheck(text, max = 200) {
  const value = cleanReason(text);
  const length = [...value].length;
  if (length === 0) return { ok: false, value, length, message: "Укажите причину блокировки — её увидит студент" };
  if (length > max) return { ok: false, value, length, message: `Причина длиннее ${max} символов` };
  return { ok: true, value, length, message: "" };
}

// ------------------------------------------------------------------ forms -> payloads

/** Trim, drop empties, de-duplicate (order kept). @param {unknown[]} items */
export function cleanList(items) {
  const out = [];
  const seen = new Set();
  for (const raw of items || []) {
    const v = str(raw).trim();
    if (!v || seen.has(v)) continue;
    seen.add(v);
    out.push(v);
  }
  return out;
}

/**
 * Text typed or pasted into a list editor -> items. URLs never contain spaces, program names may.
 * @param {string} text @param {{allowSpaces?: boolean}} [o]
 */
export function splitInput(text, o = {}) {
  const sep = o.allowSpaces ? /[\n\r,;]+/ : /[\s,;]+/;
  return cleanList(str(text).split(sep));
}

/**
 * Policy form -> PolicyIn. Fields of the other mode are not sent (the server rejects mixed policies).
 * @param {{mode: string, start_url?: string, allowed_urls?: string[], auth_domains?: string[], allowed_apps?: string[], instructions_ru?: string}} f
 */
export function policyPayload(f) {
  const instructions_ru = str(f.instructions_ru).trim();
  if (f.mode === "app") {
    return { mode: "app", start_url: null, allowed_urls: [], auth_domains: [], allowed_apps: cleanList(f.allowed_apps || []), instructions_ru };
  }
  const start = str(f.start_url).trim();
  return {
    mode: f.mode === "url" ? "url" : str(f.mode),
    start_url: start || null,
    allowed_urls: cleanList(f.allowed_urls || []),
    auth_domains: cleanList(f.auth_domains || []),
    allowed_apps: [],
    instructions_ru,
  };
}

/** Staff editor rows -> {teacher_id: role}. @param {{id: string, role: string}[]} rows */
export function staffMap(rows) {
  /** @type {Record<string, string>} */
  const out = {};
  for (const r of rows || []) {
    const id = str(r.id).trim();
    if (id) out[id] = str(r.role) || "observer";
  }
  return out;
}

/**
 * Client-side checks before sending (the server stays authoritative and its errors are shown too).
 * @param {{title?: string, mode: string, start_url?: string, allowed_apps?: string[]}} f
 * @param {{withTitle?: boolean, withName?: boolean, name?: string}} [o]
 * @returns {Record<string, string>} field -> message
 */
export function validateForm(f, o = {}) {
  /** @type {Record<string, string>} */
  const errors = {};
  if (o.withTitle && !str(f.title).trim()) errors.title = "Укажите название экзамена";
  if (o.withName && !str(o.name).trim()) errors.name = "Укажите название политики";
  if (f.mode !== "url" && f.mode !== "app") errors.mode = "Выберите «Внешний сайт» или «Отдельная программа»";
  if (f.mode === "url" && !str(f.start_url).trim()) errors.start_url = "Укажите адрес экзамена — он откроется у студента первым";
  if (f.mode === "app" && cleanList(f.allowed_apps || []).length === 0) errors.allowed_apps = "Укажите хотя бы одну программу (name.exe)";
  return errors;
}

/**
 * @param {{title: string, staff?: {id: string, role: string}[]} & Parameters<typeof policyPayload>[0]} f
 */
export function examCreatePayload(f) {
  return { title: str(f.title).trim(), policy: policyPayload(f), staff: staffMap(f.staff || []) };
}

/** Policy view from the server -> comparable PolicyIn. @param {any} p */
export function policyFromView(p) {
  return policyPayload({
    mode: p.mode,
    start_url: p.start_url ?? "",
    allowed_urls: p.allowed_urls || [],
    auth_domains: p.auth_domains || [],
    allowed_apps: p.allowed_apps || [],
    instructions_ru: p.instructions_ru || "",
  });
}

/**
 * Edit of an existing exam: PATCH exam (title/staff, optimistic `revision`) and/or PATCH of the default
 * policy (optimistic `version`). Unchanged parts are not sent. Exam first: a policy edit bumps the revision.
 * @param {any} f form values @param {any} exam exam view the form was filled from
 * @param {{canEditStaff?: boolean}} [o]
 */
export function examEditPayloads(f, exam, o = {}) {
  const policy = (exam.policies || []).find((p) => p.policy_id === exam.default_policy_id);
  /** @type {{revision: number, title?: string, staff?: Record<string, string>} | null} */
  let examPatch = null;
  const title = str(f.title).trim();
  if (title !== exam.title) examPatch = { revision: exam.revision, title };
  if (o.canEditStaff && f.staff) {
    const staff = staffMap(f.staff);
    if (JSON.stringify(sortKeys(staff)) !== JSON.stringify(sortKeys(exam.staff || {}))) examPatch = { ...(examPatch ?? { revision: exam.revision }), staff };
  }
  let policyPatch = null;
  if (policy) {
    const next = policyPayload(f);
    if (JSON.stringify(next) !== JSON.stringify(policyFromView(policy))) policyPatch = { version: policy.version, policy: next };
  }
  return { examPatch, policyPatch, policyId: policy ? policy.policy_id : null };
}

/** @param {Record<string, unknown>} o */
function sortKeys(o) {
  return Object.fromEntries(Object.entries(o).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)));
}

// ------------------------------------------------------------------ errors

const HTTP_FALLBACK_RU = {
  400: "Неверный запрос",
  401: "Нужен вход преподавателя",
  403: "Нет доступа",
  404: "Не найдено",
  409: "Конфликт: данные уже изменены",
  422: "Неверные данные",
};

const PYDANTIC_RU = {
  string_too_long: "слишком длинное значение",
  too_long: "слишком много элементов",
  too_short: "список пуст",
  missing: "обязательное поле",
  extra_forbidden: "лишнее поле",
  string_pattern_mismatch: "недопустимые символы",
  string_type: "ожидается текст",
  list_type: "ожидается список",
  greater_than_equal: "слишком маленькое значение",
  less_than_equal: "слишком большое значение",
};

/**
 * API error -> {status, code, message, field, details}. Handles the T04 format
 * {"error":{code,message_ru,details:{field}}} and FastAPI validation errors {"detail":[{loc,type,...}]}.
 * @param {number} status @param {any} body
 */
export function normalizeError(status, body) {
  const fallback = HTTP_FALLBACK_RU[/** @type {keyof typeof HTTP_FALLBACK_RU} */ (status)] ?? (status >= 500 ? `Ошибка сервера (HTTP ${status})` : `Ошибка (HTTP ${status})`);
  if (body && typeof body === "object" && body.error && typeof body.error === "object") {
    const e = body.error;
    const details = e.details && typeof e.details === "object" ? e.details : {};
    return { status, code: str(e.code) || `http_${status}`, message: str(e.message_ru) || fallback, field: typeof details.field === "string" ? details.field : null, details };
  }
  if (body && typeof body === "object" && Array.isArray(body.detail) && body.detail.length) {
    const d = body.detail[0] || {};
    const loc = Array.isArray(d.loc) ? d.loc.filter((x) => typeof x === "string" && x !== "body" && x !== "policy" && x !== "query" && x !== "path") : [];
    const field = loc.length ? loc[loc.length - 1] : null;
    const what = PYDANTIC_RU[/** @type {keyof typeof PYDANTIC_RU} */ (d.type)] ?? "неверный формат";
    const limit = d.ctx && (d.ctx.max_length ?? d.ctx.min_length);
    return {
      status,
      code: "validation",
      message: `Поле ${field ? `«${field}»` : "запроса"}: ${what}${limit !== undefined ? ` (предел ${limit})` : ""}`,
      field,
      details: {},
    };
  }
  return { status, code: `http_${status}`, message: fallback, field: null, details: {} };
}

/** @param {any} err */
export const isStaleEdit = (err) => !!err && err.status === 409 && err.code === "stale_edit";

// ------------------------------------------------------------------ idempotency

/** @param {{randomUUID?: () => string, getRandomValues?: (a: Uint8Array) => Uint8Array} | undefined} [c] */
export function newIdempotencyKey(c = globalThis.crypto) {
  if (c && typeof c.randomUUID === "function") return c.randomUUID();
  if (!c || typeof c.getRandomValues !== "function") throw new Error("no secure random source");
  const b = c.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const hex = [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

/**
 * One teacher click = one request body with ONE idempotency key. send() may be called again after a
 * network failure (the request may or may not have reached the server): the SAME body/key is resent and
 * the server answers "repeat" instead of creating a second command.
 */
export class OneShotRequest {
  /** @param {Record<string, unknown>} body @param {() => string} [makeKey] */
  constructor(body, makeKey = () => newIdempotencyKey()) {
    this.key = makeKey();
    this.body = Object.freeze({ ...body, idempotency_key: this.key });
    this.tries = 0;
    /** @type {any} */
    this.last = null;
    this.inFlight = false;
  }

  /** @param {(body: Readonly<Record<string, unknown>>) => Promise<any>} post */
  async send(post) {
    if (this.inFlight) throw new Error("request already in flight");
    this.inFlight = true;
    this.tries += 1;
    try {
      this.last = await post(this.body);
    } finally {
      this.inFlight = false;
    }
    return this.last;
  }

  /** A retry makes sense only when the outcome is unknown (network error), never after a server answer. */
  get retryable() {
    return !!this.last && this.last.ok === false && this.last.network === true;
  }
}

// ------------------------------------------------------------------ results of POSTs

/**
 * POST /commands response -> per-student lines. Wording never claims an effect: "created" only means
 * the server accepted the request.
 * @param {any} resp @param {(id: string) => string} labelOf @param {{where?: string}} [o] where the real outcome is shown
 */
export function commandResultLines(resp, labelOf, o = {}) {
  const where = o.where ?? "в колонке «Последняя команда»";
  const results = resp && Array.isArray(resp.results) ? resp.results : [];
  return results.map((r) => {
    const label = labelOf(r.student_id) || str(r.student_id);
    if (r.unavailable_ru) return { student_id: r.student_id, label, outcome: "unavailable", text: `недоступно — ${r.unavailable_ru}`, command: null };
    const st = commandStatus(r.command);
    const now = st ? ` Сейчас: ${st.groupLabel}${st.label ? ` (${st.label})` : ""}.` : "";
    if (r.created === false) {
      return { student_id: r.student_id, label, outcome: "repeat", text: `повтор того же запроса — новая команда не создана.${now}`, command: r.command };
    }
    return { student_id: r.student_id, label, outcome: "created", text: `команда принята сервером.${now} Итог — ${where}.`, command: r.command };
  });
}

/** POST /assignments response -> per-student lines. @param {any} resp @param {(id: string) => string} labelOf */
export function assignmentResultLines(resp, labelOf) {
  const results = resp && Array.isArray(resp.results) ? resp.results : [];
  return results.map((r) => {
    const label = labelOf(r.student_id) || str(r.student_id);
    if (!r.assigned) return { student_id: r.student_id, label, outcome: "unavailable", text: `не назначена — ${str(r.unavailable_ru) || "недоступно"}`, command: null };
    const st = commandStatus(r.command);
    const delivery = str(r.delivery_ru);
    return {
      student_id: r.student_id,
      label,
      outcome: "assigned",
      text: `назначена.${delivery ? ` ${delivery}.` : ""}${st ? ` Доставка: ${st.groupLabel}.` : ""}`,
      command: r.command ?? null,
    };
  });
}

// ------------------------------------------------------------------ time

/** @param {unknown} iso */
function toDate(iso) {
  if (typeof iso !== "string" || !iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** Local HH:MM:SS. @param {unknown} iso */
export function fmtTime(iso) {
  const d = toDate(iso);
  if (!d) return "—";
  return d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

/** Local dd.mm HH:MM:SS. @param {unknown} iso */
export function fmtDateTime(iso) {
  const d = toDate(iso);
  if (!d) return "—";
  const date = d.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" });
  return `${date} ${fmtTime(iso)}`;
}

/** @param {number} seconds */
export function fmtDuration(seconds) {
  const s = Math.round(Number(seconds) || 0);
  if (s < 60) return `${s} с`;
  if (s < 3600) {
    const m = Math.floor(s / 60);
    const r = s % 60;
    return r ? `${m} мин ${r} с` : `${m} мин`;
  }
  const hh = Math.floor(s / 3600);
  const mm = Math.round((s % 3600) / 60);
  return mm ? `${hh} ч ${mm} мин` : `${hh} ч`;
}

/** Expiry explanation shown before sending a command. @param {string} kind @param {any} meta */
export function expiryText(kind, meta) {
  const ttl = meta && meta.ttl_default_s ? Number(meta.ttl_default_s[kind]) : NaN;
  if (!Number.isFinite(ttl) || ttl <= 0) return "Срок действия команды задаёт сервер.";
  return (
    `Срок действия команды — ${fmtDuration(ttl)}. Если компьютер студента не получит её за это время ` +
    "(например, нет связи), команда истечёт и НЕ выполнится позже, после переподключения."
  );
}

// ------------------------------------------------------------------ journal

/**
 * Journal entry -> table row: когда / кто / кому / что / результат.
 * @param {any} e @param {Record<string, string>} [kindsRu]
 */
export function journalRow(e, kindsRu = KIND_RU) {
  const d = e && e.details && typeof e.details === "object" ? e.details : {};
  const kind = e.kind ? kindsRu[e.kind] ?? d.kind_ru ?? e.kind : null;
  const what = [str(e.action_ru) || str(e.action)];
  if (kind) what.push(`«${kind}»`);
  if (d.reason_ru && e.action !== "command_unavailable") what.push(`причина: «${str(d.reason_ru)}»`);
  if (d.title) what.push(`«${str(d.title)}»`);
  if (d.name) what.push(`политика «${str(d.name)}»`);
  /** @type {string[]} */
  const result = [];
  const stateRu = d.state ? STATE_RU[/** @type {keyof typeof STATE_RU} */ (d.state)] ?? str(d.state) : "";
  if (stateRu) result.push(stateRu);
  if (d.detail_ru && !stateRu.startsWith(str(d.detail_ru))) result.push(str(d.detail_ru));
  if (d.code) result.push(`код клиента: ${str(d.code)}`);
  if (e.action === "command_issued" && d.expires_at) result.push(`срок действия до ${fmtTime(d.expires_at)}`);
  if (e.action === "command_unavailable" && d.reason_ru) result.push(str(d.reason_ru));
  if (e.action === "command_duplicate_request") result.push("новая команда не создана");
  if (e.action === "access_denied") result.push(`отказано (${{ view: "просмотр", edit: "изменение", command: "команда" }[/** @type {"view"} */ (d.attempted)] ?? str(d.attempted)})`);
  if (d.version !== undefined && (e.action === "policy_assigned" || e.action === "policy_updated")) result.push(`версия ${str(d.version)}`);
  if (e.action === "ack_duplicate") result.push("повторное подтверждение проигнорировано");
  if (typeof d.late_ack_ok === "boolean") result.push(d.late_ack_ok ? "клиент позже сообщил: выполнено" : "клиент позже сообщил: не выполнено");
  return {
    seq: e.seq,
    when: fmtDateTime(e.at),
    whenIso: str(e.at),
    who: str(e.actor_name) || (e.actor_id ? str(e.actor_id) : "клиент/система"),
    byTeacher: !!e.actor_id,
    whom: Array.isArray(e.student_labels) && e.student_labels.length ? e.student_labels.map(str).join(", ") : "—",
    what: what.join(" · "),
    result: result.length ? result.join(" · ") : "—",
    commandId: e.command_id ? str(e.command_id) : "",
  };
}
