// T02 panel module with a fake ctx (the T02 panel itself is not available in this branch).
import assert from "node:assert/strict";
import test from "node:test";
import { installFakeDocument } from "./fake-dom.mjs";

const doc = installFakeDocument();
const { createCommandsModule, register, MODULE_ID } = await import("../t02-module.js");

const flush = async (n = 20) => {
  for (let i = 0; i < n; i++) await new Promise((r) => setImmediate(r));
};

function student(over = {}) {
  return {
    student_id: "s1",
    label: "Студент <b>1</b>",
    connected: true,
    lock: { state: "unlocked", label_ru: "Не заблокирован (по статусу клиента)" },
    actions: {
      start_exam: { available: true, reason_ru: null },
      lock: { available: true, reason_ru: null },
      unlock: { available: true, reason_ru: null },
      finish_exam: { available: true, reason_ru: null },
    },
    site_timer_ru: "Таймер внешнего сайта при этом НЕ останавливается",
    commands: [],
    ...over,
  };
}

function backend() {
  const state = { students: [student()], posts: [], failNextPost: false, gets: 0 };
  const fetchImpl = async (url, init) => {
    const method = init.method;
    const body = init.body ? JSON.parse(init.body) : undefined;
    const reply = (status, data) => ({ ok: status < 300, status, text: async () => JSON.stringify(data) });
    if (url.endsWith("/meta")) return reply(200, { kinds: { lock: "Заблокировать" }, reason_max: 200, ttl_default_s: { lock: 120 }, site_timer_note_ru: "Таймер не останавливается" });
    if (url.endsWith("/exams")) return reply(200, [{ exam_id: "e1", students: ["s1", "s2"] }]);
    if (url.endsWith("/exams/e1/students")) {
      state.gets += 1;
      return reply(200, state.students);
    }
    if (url.endsWith("/exams/e1/commands") && method === "POST") {
      state.posts.push(body);
      if (state.failNextPost) {
        state.failNextPost = false;
        throw new TypeError("Failed to fetch");
      }
      return reply(202, {
        kind: body.kind,
        results: [{ student_id: "s1", created: state.posts.length < 3, unavailable_ru: null, command: { command_id: "c1", seq: 1, kind: body.kind, kind_ru: "Заблокировать", group: "pending", label_ru: "Ожидает доставки", issued_at: "2026-10-08T10:00:00Z" } }],
      });
    }
    return reply(404, { error: { code: "not_found", message_ru: "Не найдено", details: {} } });
  };
  return { state, fetchImpl };
}

function fakeTimers() {
  const q = new Map();
  let id = 0;
  return {
    q,
    setTimeout: (fn) => {
      q.set(++id, fn);
      return id;
    },
    clearTimeout: (i) => q.delete(i),
    runAll() {
      const fns = [...q.values()];
      q.clear();
      for (const f of fns) f();
    },
  };
}

const q = (el, sel) => el.querySelector(sel);

test("DEMO mode: renders 'Недоступно в DEMO-режиме' and makes no requests", async () => {
  let called = 0;
  const mod = createCommandsModule({ fetchImpl: async () => (called++, { ok: true, status: 200, text: async () => "[]" }), stylesheet: null });
  const el = doc.createElement("div");
  const un = mod.mount(el, { studentId: "s1", mode: "demo" });
  await flush();
  assert.match(el.textContent, /Недоступно в DEMO-режиме/);
  assert.equal(called, 0);
  assert.equal(typeof un, "function");
});

test("real mode: finds the student's exam, shows server lock label and command status, buttons follow actions", async () => {
  const be = backend();
  be.state.students = [
    student({
      lock: { state: "lock_pending", label_ru: "Блокировка: ожидает доставки" },
      actions: { ...student().actions, lock: { available: false, reason_ru: "Клиент студента не поддерживает действие «Заблокировать»" } },
      commands: [{ command_id: "c0", seq: 1, kind: "lock", kind_ru: "Заблокировать", group: "no_connection", label_ru: "Нет связи — будет доставлена при подключении", issued_at: "2026-10-08T10:00:00Z" }],
    }),
  ];
  const timers = fakeTimers();
  const mod = createCommandsModule({ fetchImpl: be.fetchImpl, timers, stylesheet: null });
  assert.equal(mod.id, MODULE_ID);
  assert.equal(mod.slot, "commands");
  const el = doc.createElement("div");
  const un = mod.mount(el, { studentId: "s1", mode: "real" });
  await flush();
  assert.equal(q(el, "[data-testid=\"t04-lock\"]").textContent, "Экран: Блокировка: ожидает доставки");
  const last = q(el, "[data-testid=\"t04-last\"]");
  assert.match(last.textContent, /нет связи/);
  assert.match(last.textContent, /Нет связи — будет доставлена при подключении/);
  assert.equal(q(el, "[data-t04-action=\"lock\"]").disabled, true);
  assert.equal(q(el, "[data-t04-action=\"unlock\"]").disabled, false);
  assert.match(q(el, "[data-testid=\"t04-why\"]").textContent, /Заблокировать: недоступно — Клиент студента не поддерживает/);
  assert.equal(be.state.gets, 1);
  timers.runAll();
  await flush();
  assert.equal(be.state.gets, 2, "polls again");
  un();
  assert.equal(timers.q.size, 0, "unmount stops polling");
  timers.runAll();
  await flush();
  assert.equal(be.state.gets, 2);
});

test("real mode: lock needs a reason, sends one idempotency key, retries with the SAME key after a network error", async () => {
  const be = backend();
  const timers = fakeTimers();
  const mod = createCommandsModule({ fetchImpl: be.fetchImpl, timers, stylesheet: null });
  const el = doc.createElement("div");
  mod.mount(el, { studentId: "s1", mode: "real" });
  await flush();
  q(el, "[data-t04-action=\"lock\"]").click();
  const form = q(el, "[data-testid=\"t04-lockform\"]");
  assert.equal(form.hidden, false);
  assert.match(q(el, "[data-testid=\"t04-timer\"]").textContent, /НЕ останавливается/);
  assert.match(q(el, "[data-testid=\"t04-expiry\"]").textContent, /2 мин/);
  q(el, "[data-testid=\"t04-lock-send\"]").click();
  assert.equal(be.state.posts.length, 0, "empty reason is not sent");
  assert.match(form.textContent, /Укажите причину/);
  const reason = q(el, "[data-testid=\"t04-reason\"]");
  reason.value = "  Телефон на столе ";
  be.state.failNextPost = true;
  q(el, "[data-testid=\"t04-lock-send\"]").click();
  await flush();
  assert.equal(be.state.posts.length, 1);
  assert.deepEqual(be.state.posts[0].payload, { reason_ru: "Телефон на столе" });
  assert.deepEqual(be.state.posts[0].student_ids, ["s1"]);
  assert.match(be.state.posts[0].idempotency_key, /^[A-Za-z0-9._:-]{8,128}$/);
  const retry = q(el, "[data-testid=\"t04-retry\"]");
  assert.ok(retry, "retry offered after a network error");
  retry.click();
  await flush();
  assert.equal(be.state.posts.length, 2);
  assert.equal(be.state.posts[1].idempotency_key, be.state.posts[0].idempotency_key, "same key on retry");
  const res = q(el, "[data-testid=\"t04-result\"]").textContent;
  assert.match(res, /принята сервером/);
  assert.ok(!/выполнено|заблокирован/i.test(res.replace(/Сейчас: ожидает доставки/, "")), "no success claimed from 202");
  assert.match(q(el, "[data-testid=\"t04-last\"]").textContent, /ожидает доставки/, "posted command shown until the poll knows it");
  q(el, "[data-t04-action=\"unlock\"]").click();
  await flush();
  assert.equal(be.state.posts.length, 3);
  assert.notEqual(be.state.posts[2].idempotency_key, be.state.posts[0].idempotency_key, "new click, new key");
});

test("real mode: student without a T04 exam -> explicit message, all buttons disabled", async () => {
  const be = backend();
  const mod = createCommandsModule({ fetchImpl: be.fetchImpl, timers: fakeTimers(), stylesheet: null });
  const el = doc.createElement("div");
  mod.mount(el, { studentId: "nobody", mode: "real" });
  await flush();
  assert.match(el.textContent, /не подключён ни к одному экзамену/);
  for (const b of el.querySelectorAll("[data-t04-action]")) assert.equal(b.disabled, true);
});

test("labels from the server stay text", async () => {
  const be = backend();
  be.state.students = [student({ lock: { state: "unknown", label_ru: "<img src=x onerror=alert(1)>" } })];
  const mod = createCommandsModule({ fetchImpl: be.fetchImpl, timers: fakeTimers(), stylesheet: null });
  const el = doc.createElement("div");
  mod.mount(el, { studentId: "s1", mode: "real" });
  await flush();
  assert.match(el.textContent, /<img src=x onerror=alert\(1\)>/);
  assert.equal(el.querySelectorAll("img").length, 0);
});

test("register(): uses the panel's registerStudentModule, or queues for a panel that loads later", () => {
  const got = [];
  assert.equal(register({ QorgauClassPanel: { registerStudentModule: (m) => got.push(m) } }, { stylesheet: null }), true);
  assert.equal(got[0].id, MODULE_ID);
  assert.equal(got[0].slot, "commands");
  const win = {};
  register(win);
  assert.equal(win.QorgauClassPanelModules.length, 1);
  assert.equal(win.QorgauClassPanelModules[0].id, MODULE_ID);
  assert.equal(register(null), false);
});
