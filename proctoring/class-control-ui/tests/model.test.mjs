// Unit tests of the pure logic (node --test). No DOM, no network.
import assert from "node:assert/strict";
import test from "node:test";
import {
  assignmentResultLines,
  cleanReason,
  commandResultLines,
  commandStatus,
  connectionText,
  effectiveRole,
  examCreatePayload,
  examEditPayloads,
  expiryText,
  fmtDuration,
  isStaleEdit,
  journalRow,
  latestCommand,
  lockStatus,
  newIdempotencyKey,
  normalizeError,
  OneShotRequest,
  policyPayload,
  reasonCheck,
  roleDenial,
  splitByAvailability,
  splitInput,
  STATUS_GROUPS,
  validateForm,
} from "../src/model.js";

const SERVER_KEY_RE = /^[A-Za-z0-9._:-]{8,128}$/; // api.py KeyStr

test("status mapping: every server group -> its Russian label, icon shape and tone", () => {
  const expected = {
    pending: "ожидает доставки",
    executing: "выполняется",
    done: "выполнено",
    error: "ошибка",
    no_connection: "нет связи",
    cancelled: "отменена",
  };
  const icons = new Set();
  for (const [group, label] of Object.entries(expected)) {
    const st = commandStatus({ group, label_ru: `X-${group}` });
    assert.equal(st.groupLabel, label);
    assert.equal(st.label, `X-${group}`, "detail text is the server's label_ru verbatim");
    icons.add(st.icon);
  }
  assert.equal(icons.size, Object.keys(expected).length, "each group has its own icon shape (not colour only)");
  assert.deepEqual(Object.keys(STATUS_GROUPS).sort(), Object.keys(expected).sort());
});

test("status mapping: unknown or missing group is never shown as done", () => {
  const st = commandStatus({ group: "succeeded", label_ru: "?" });
  assert.equal(st.group, "unknown");
  assert.notEqual(st.groupLabel, "выполнено");
  assert.equal(commandStatus(null), null);
  assert.equal(commandStatus(undefined), null);
});

test("lock column text is lock.label_ru verbatim; icon follows lock.state only", () => {
  const pending = lockStatus({ state: "lock_pending", label_ru: "Блокировка: ожидает доставки: отправлена, ждём ответа клиента" });
  assert.equal(pending.label, "Блокировка: ожидает доставки: отправлена, ждём ответа клиента");
  assert.equal(pending.icon, "hourglass");
  assert.ok(!/^Заблокирован/.test(pending.label));
  const locked = lockStatus({ state: "locked", label_ru: "Заблокирован (по статусу клиента)" });
  assert.equal(locked.label, "Заблокирован (по статусу клиента)");
  assert.equal(locked.icon, "lock");
  assert.equal(lockStatus(null).label, "Нет данных о блокировке");
  assert.equal(lockStatus({ state: "locked" }).label, "Нет данных о блокировке", "no label_ru -> no claim");
});

test("latestCommand: POST answer shown until the poll knows the command; newer seq wins", () => {
  const polled = [{ command_id: "a", seq: 3, group: "done" }];
  const posted = { command_id: "b", seq: 4, group: "pending" };
  assert.equal(latestCommand(polled, posted).command_id, "b");
  assert.equal(latestCommand([{ command_id: "b", seq: 4, group: "executing" }, ...polled], posted).group, "executing");
  assert.equal(latestCommand([], posted).command_id, "b");
  assert.equal(latestCommand(polled, { command_id: "old", seq: 1 }).command_id, "a");
  assert.equal(latestCommand(undefined, undefined), null);
});

test("connection text uses server timestamps", () => {
  assert.match(connectionText({ connected: true, connected_at: "2026-10-08T10:00:00.000Z" }), /^на связи с \d\d:\d\d:\d\d$/);
  assert.match(connectionText({ connected: false, disconnected_at: "2026-10-08T10:00:00.000Z" }), /^нет связи с /);
  assert.equal(connectionText({ connected: false }), "нет связи");
});

test("roles mirror backend access.check(): owner, staff, account role cap, outsider", () => {
  const exam = { owner_id: "t-a", staff: { "t-obs": "observer", "t-as": "assistant", "t-co": "teacher" } };
  assert.equal(effectiveRole(exam, "t-a", "teacher"), "teacher");
  assert.equal(effectiveRole(exam, "t-obs", "observer"), "observer");
  assert.equal(effectiveRole(exam, "t-as", "teacher"), "assistant");
  assert.equal(effectiveRole(exam, "t-co", "observer"), "observer", "observer account never commands");
  assert.equal(effectiveRole(exam, "t-bolat", "teacher"), "none");
  assert.equal(effectiveRole(exam, null, null), "unknown", "real server (PIN): the server decides");
  assert.equal(roleDenial("teacher", "command"), null);
  assert.equal(roleDenial("assistant", "command"), null);
  assert.match(roleDenial("assistant", "edit"), /Ассистент/);
  assert.match(roleDenial("observer", "command"), /наблюдатель/);
  assert.match(roleDenial("observer", "edit"), /наблюдатель/);
  assert.equal(roleDenial("observer", "view"), null);
  assert.match(roleDenial("none", "view"), /Нет доступа/);
  assert.equal(roleDenial("unknown", "edit"), null);
});

test("bulk: selected students split into will-send / unavailable with the server's reason", () => {
  const students = [
    { student_id: "s1", label: "Студент 1", actions: { lock: { available: true, reason_ru: null } } },
    { student_id: "s8", label: "Студент 8", actions: { lock: { available: false, reason_ru: "Клиент студента не поддерживает действие «Заблокировать»" } } },
    { student_id: "s9", label: "Студент 9", actions: {} },
  ];
  const sp = splitByAvailability(students, ["s1", "s8", "s9", "gone"], "lock");
  assert.deepEqual(sp.available.map((x) => x.student_id), ["s1"]);
  assert.deepEqual(
    sp.unavailable.map((x) => [x.student_id, x.reason]),
    [
      ["s8", "Клиент студента не поддерживает действие «Заблокировать»"],
      ["s9", "Сервер не сообщил, доступно ли действие"],
      ["gone", "Студента больше нет в списке экзамена"],
    ],
  );
});

test("lock reason: 1..200 characters after the same cleaning as the server", () => {
  assert.equal(reasonCheck("   ").ok, false);
  assert.equal(reasonCheck("\u0007\u0000").ok, false);
  assert.equal(cleanReason("  Телефон\u0007 на столе \n"), "Телефон на столе");
  assert.equal(reasonCheck("a".repeat(200)).ok, true);
  const long = reasonCheck("a".repeat(201));
  assert.equal(long.ok, false);
  assert.match(long.message, /200/);
  assert.equal(reasonCheck("😀".repeat(3)).length, 3, "counts characters, not UTF-16 units");
});

test("form -> payload: url mode drops app fields, app mode drops url fields", () => {
  const url = policyPayload({
    mode: "url",
    start_url: "  https://exam.kz/t/1 ",
    allowed_urls: [" https://cdn.kz/* ", "", "https://cdn.kz/*"],
    auth_domains: ["https://accounts.google.com/*"],
    allowed_apps: ["calc.exe"],
    instructions_ru: "  Удачи ",
  });
  assert.deepEqual(url, {
    mode: "url",
    start_url: "https://exam.kz/t/1",
    allowed_urls: ["https://cdn.kz/*"],
    auth_domains: ["https://accounts.google.com/*"],
    allowed_apps: [],
    instructions_ru: "Удачи",
  });
  const app = policyPayload({ mode: "app", start_url: "https://x.kz", allowed_urls: ["a.kz"], auth_domains: ["b.kz"], allowed_apps: ["Test Client.exe", "Test Client.exe"] });
  assert.deepEqual(app, { mode: "app", start_url: null, allowed_urls: [], auth_domains: [], allowed_apps: ["Test Client.exe"], instructions_ru: "" });
  assert.equal(policyPayload({ mode: "url", start_url: "  " }).start_url, null);
  const create = examCreatePayload({ title: "  Экзамен ", mode: "url", start_url: "https://e.kz", staff: [{ id: " t-obs ", role: "observer" }, { id: "", role: "teacher" }] });
  assert.deepEqual(create.staff, { "t-obs": "observer" });
  assert.equal(create.title, "Экзамен");
});

test("list input: URLs split on spaces/commas/new lines, program names keep spaces", () => {
  assert.deepEqual(splitInput("a.kz, b.kz\nc.kz  a.kz"), ["a.kz", "b.kz", "c.kz"]);
  assert.deepEqual(splitInput("Test Client.exe\nother.exe; x.exe", { allowSpaces: true }), ["Test Client.exe", "other.exe", "x.exe"]);
});

test("client-side required fields (server stays authoritative)", () => {
  assert.deepEqual(Object.keys(validateForm({ title: "", mode: "url", start_url: "" }, { withTitle: true })).sort(), ["start_url", "title"]);
  assert.deepEqual(Object.keys(validateForm({ mode: "app", allowed_apps: [] })), ["allowed_apps"]);
  assert.deepEqual(validateForm({ mode: "url", start_url: "https://e.kz" }), {});
  assert.deepEqual(Object.keys(validateForm({ mode: "url", start_url: "x" }, { withName: true, name: " " })), ["name"]);
});

test("edit payloads: only changed parts, with the revision/version the form was filled from", () => {
  const exam = {
    exam_id: "e1",
    title: "Экзамен",
    revision: 4,
    staff: { "t-obs": "observer" },
    default_policy_id: "p1",
    policies: [{ policy_id: "p1", version: 2, mode: "url", start_url: "https://e.kz/", allowed_urls: ["https://e.kz/*"], auth_domains: [], allowed_apps: [], instructions_ru: "" }],
  };
  const same = { title: "Экзамен", mode: "url", start_url: "https://e.kz/", allowed_urls: ["https://e.kz/*"], auth_domains: [], allowed_apps: [], instructions_ru: "", staff: [{ id: "t-obs", role: "observer" }] };
  assert.deepEqual(examEditPayloads(same, exam, { canEditStaff: true }), { examPatch: null, policyPatch: null, policyId: "p1" });
  const t = examEditPayloads({ ...same, title: "Новое" }, exam);
  assert.deepEqual(t.examPatch, { revision: 4, title: "Новое" });
  assert.equal(t.policyPatch, null);
  const p = examEditPayloads({ ...same, auth_domains: ["https://accounts.google.com/*"] }, exam);
  assert.equal(p.examPatch, null);
  assert.equal(p.policyPatch.version, 2);
  assert.deepEqual(p.policyPatch.policy.auth_domains, ["https://accounts.google.com/*"]);
  const s = examEditPayloads({ ...same, staff: [] }, exam, { canEditStaff: true });
  assert.deepEqual(s.examPatch, { revision: 4, staff: {} });
  assert.equal(examEditPayloads({ ...same, staff: [] }, exam, { canEditStaff: false }).examPatch, null, "non-owner never sends staff");
});

test("errors: T04 format with field, FastAPI validation format, empty body", () => {
  const e = normalizeError(422, { error: { code: "required", message_ru: "Укажите адрес экзамена", details: { field: "start_url" } } });
  assert.deepEqual([e.status, e.code, e.message, e.field], [422, "required", "Укажите адрес экзамена", "start_url"]);
  const stale = normalizeError(409, { error: { code: "stale_edit", message_ru: "уже изменён", details: { current: 5 } } });
  assert.equal(isStaleEdit(stale), true);
  assert.equal(isStaleEdit(normalizeError(409, { error: { code: "conflict", message_ru: "x", details: {} } })), false);
  const v = normalizeError(422, { detail: [{ type: "string_too_long", loc: ["body", "policy", "instructions_ru"], ctx: { max_length: 2000 } }] });
  assert.equal(v.field, "instructions_ru");
  assert.match(v.message, /слишком длинное значение \(предел 2000\)/);
  const forbidden = normalizeError(403, null);
  assert.equal(forbidden.message, "Нет доступа");
  assert.match(normalizeError(502, "<html>").message, /Ошибка сервера \(HTTP 502\)/);
});

test("idempotency key: UUID accepted by the server's KeyStr pattern; fallback without randomUUID", () => {
  const k = newIdempotencyKey();
  assert.match(k, SERVER_KEY_RE);
  const fb = newIdempotencyKey({ getRandomValues: (a) => a.fill(171) });
  assert.match(fb, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  assert.match(fb, SERVER_KEY_RE);
  assert.throws(() => newIdempotencyKey({}));
});

test("idempotency: one click = one key, reused for a retry after a network error", async () => {
  const sent = [];
  let fail = true;
  const post = async (body) => {
    sent.push(body);
    if (fail) return { ok: false, network: true, status: 0, error: { code: "network", message: "нет связи" } };
    return { ok: true, status: 202, data: { results: [] } };
  };
  const req = new OneShotRequest({ kind: "lock", student_ids: ["s1"], payload: { reason_ru: "x" } });
  await req.send(post);
  assert.equal(req.retryable, true);
  fail = false;
  await req.send(post);
  assert.equal(sent.length, 2);
  assert.equal(sent[0].idempotency_key, sent[1].idempotency_key, "same key on retry");
  assert.deepEqual(sent[0], sent[1], "same body on retry");
  assert.equal(req.retryable, false);
  const other = new OneShotRequest({ kind: "lock", student_ids: ["s1"], payload: { reason_ru: "x" } });
  assert.notEqual(other.key, req.key, "a new click gets a new key");
  const denied = new OneShotRequest({ kind: "lock", student_ids: ["s1"] });
  await denied.send(async () => ({ ok: false, network: false, status: 403, error: { code: "forbidden" } }));
  assert.equal(denied.retryable, false, "no retry offered after a server answer");
  assert.throws(() => Object.assign(req.body, { idempotency_key: "x" }), TypeError, "body is frozen");
});

test("idempotency: a second send while the first is in flight is refused", async () => {
  let release;
  const req = new OneShotRequest({ kind: "unlock", student_ids: ["s1"] });
  const p = req.send(() => new Promise((r) => (release = r)));
  await assert.rejects(req.send(async () => ({ ok: true })), /in flight/);
  release({ ok: true });
  await p;
});

test("POST results never claim an effect: created/repeat/unavailable wording", () => {
  const resp = {
    kind: "lock",
    results: [
      { student_id: "s1", created: true, unavailable_ru: null, command: { group: "pending", label_ru: "Ожидает доставки" } },
      { student_id: "s2", created: false, unavailable_ru: null, command: { group: "done", label_ru: "Выполнено" } },
      { student_id: "s8", created: false, unavailable_ru: "Клиент не поддерживает", command: null },
    ],
  };
  const lines = commandResultLines(resp, (id) => `L-${id}`);
  assert.deepEqual(lines.map((l) => l.outcome), ["created", "repeat", "unavailable"]);
  assert.equal(lines[0].label, "L-s1");
  assert.ok(!/выполнено|заблокирован/i.test(lines[0].text), "accepted != executed");
  assert.match(lines[0].text, /принята сервером/);
  assert.match(lines[1].text, /повтор того же запроса — новая команда не создана/);
  assert.match(lines[1].text, /Сейчас: выполнено/, "repeat reports the existing command's real state");
  assert.match(lines[2].text, /недоступно — Клиент не поддерживает/);
});

test("assignment results include unavailable_ru and delivery_ru", () => {
  const lines = assignmentResultLines(
    {
      results: [
        { student_id: "s1", assigned: true, unavailable_ru: null, delivery_ru: "Отправлена клиенту командой «Применить политику»", command: { group: "pending" } },
        { student_id: "s7", assigned: true, unavailable_ru: null, delivery_ru: "Клиент не сообщил о смене политики на лету", command: null },
        { student_id: "s8", assigned: false, unavailable_ru: "Клиент студента не поддерживает режим «Отдельная программа»", command: null },
      ],
    },
    (id) => id,
  );
  assert.deepEqual(lines.map((l) => l.outcome), ["assigned", "assigned", "unavailable"]);
  assert.match(lines[0].text, /Применить политику.*Доставка: ожидает доставки/);
  assert.match(lines[1].text, /на лету/);
  assert.match(lines[2].text, /не назначена — Клиент студента не поддерживает режим/);
});

test("journal row: who / to whom / what (+reason) / result", () => {
  const issued = journalRow({
    seq: 7,
    at: "2026-10-08T10:03:48.424Z",
    action: "command_issued",
    action_ru: "Отправлена команда",
    actor_id: "t-aigerim",
    actor_name: "Айгерим Сейтова (преподаватель)",
    student_labels: ["Студент 1"],
    kind: "lock",
    details: { kind_ru: "Заблокировать", reason_ru: "Телефон", expires_at: "2026-10-08T10:05:48.424Z" },
  });
  assert.equal(issued.who, "Айгерим Сейтова (преподаватель)");
  assert.equal(issued.whom, "Студент 1");
  assert.match(issued.what, /Отправлена команда · «Заблокировать» · причина: «Телефон»/);
  assert.match(issued.result, /срок действия до/);
  const state = journalRow({ seq: 8, at: "x", action: "command_state", action_ru: "Изменилось состояние команды", actor_id: null, actor_name: null, student_labels: ["Студент 1"], kind: "lock", details: { state: "succeeded", detail_ru: "подтверждено клиентом" } });
  assert.equal(state.who, "клиент/система");
  assert.equal(state.result, "выполнено (подтверждено клиентом) · подтверждено клиентом");
  assert.equal(state.when, "—");
  const denied = journalRow({ seq: 9, at: "x", action: "access_denied", action_ru: "Отказано в доступе", actor_id: "t-bolat", actor_name: "Болат", student_labels: [], details: { attempted: "edit" } });
  assert.equal(denied.whom, "—");
  assert.match(denied.result, /отказано \(изменение\)/);
});

test("expiry text explains that an expired command is not executed later", () => {
  assert.match(expiryText("lock", { ttl_default_s: { lock: 120 } }), /2 мин.*НЕ выполнится позже/);
  assert.match(expiryText("lock", null), /задаёт сервер/);
  assert.equal(fmtDuration(45), "45 с");
  assert.equal(fmtDuration(1800), "30 мин");
  assert.equal(fmtDuration(90), "1 мин 30 с");
  assert.equal(fmtDuration(3600), "1 ч");
});
