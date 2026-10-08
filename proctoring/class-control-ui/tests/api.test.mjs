// API client: headers, URLs, error normalization, "no answer" vs "server answered".
import assert from "node:assert/strict";
import test from "node:test";
import { createApi } from "../src/api.js";

function fakeFetch(handler) {
  const calls = [];
  const fn = async (url, init) => {
    calls.push({ url, init, body: init.body ? JSON.parse(init.body) : undefined });
    const r = await handler(url, init);
    if (r instanceof Error) throw r;
    return { ok: r.status >= 200 && r.status < 300, status: r.status, text: async () => (r.body === undefined ? "" : typeof r.body === "string" ? r.body : JSON.stringify(r.body)) };
  };
  fn.calls = calls;
  return fn;
}

test("DEV teacher header is sent only when a DEV teacher is chosen", async () => {
  let teacher = "t-aigerim";
  const f = fakeFetch(() => ({ status: 200, body: [] }));
  const api = createApi({ fetchImpl: f, getTeacher: () => teacher });
  await api.exams();
  assert.equal(f.calls[0].init.headers["X-Qorgau-Dev-Teacher"], "t-aigerim");
  assert.equal(f.calls[0].init.credentials, "same-origin", "PIN cookie of the real server is sent");
  teacher = null;
  await api.exams();
  assert.equal(f.calls[1].init.headers["X-Qorgau-Dev-Teacher"], undefined);
});

test("routes and query strings", async () => {
  const f = fakeFetch(() => ({ status: 200, body: {} }));
  const api = createApi({ fetchImpl: f });
  await api.students("exam 1");
  await api.commands("e1", "sim-01");
  await api.commands("e1", null);
  await api.journal("e1", { student_id: "s/1" });
  await api.cancel("e1", "cmd-1");
  await api.sendCommand("e1", { kind: "lock", student_ids: ["s1"], payload: { reason_ru: "x" }, idempotency_key: "k-12345678" });
  assert.deepEqual(
    f.calls.map((c) => `${c.init.method} ${c.url}`),
    [
      "GET /api/teacher/control/exams/exam%201/students",
      "GET /api/teacher/control/exams/e1/commands?student_id=sim-01",
      "GET /api/teacher/control/exams/e1/commands",
      "GET /api/teacher/control/exams/e1/journal?student_id=s%2F1&limit=200",
      "POST /api/teacher/control/exams/e1/commands/cmd-1/cancel",
      "POST /api/teacher/control/exams/e1/commands",
    ],
  );
  assert.equal(f.calls[5].body.idempotency_key, "k-12345678");
  assert.equal(f.calls[5].init.headers["Content-Type"], "application/json");
});

test("202 is just 'accepted': the client returns the data, it does not interpret it", async () => {
  const f = fakeFetch(() => ({ status: 202, body: { kind: "lock", results: [] } }));
  const r = await createApi({ fetchImpl: f }).sendCommand("e1", {});
  assert.deepEqual(r, { ok: true, status: 202, data: { kind: "lock", results: [] } });
});

test("network failure -> {network: true}; server error -> normalized error, network false", async () => {
  const down = await createApi({ fetchImpl: fakeFetch(() => new TypeError("Failed to fetch")) }).exams();
  assert.equal(down.ok, false);
  assert.equal(down.network, true);
  assert.equal(down.status, 0);
  assert.match(down.error.message, /Нет связи с сервером/);
  const denied = await createApi({ fetchImpl: fakeFetch(() => ({ status: 403, body: { error: { code: "forbidden", message_ru: "Нет доступа к этому экзамену", details: {} } } })) }).exam("e1");
  assert.equal(denied.network, false);
  assert.equal(denied.error.status, 403);
  assert.equal(denied.error.message, "Нет доступа к этому экзамену");
  const html = await createApi({ fetchImpl: fakeFetch(() => ({ status: 500, body: "<h1>oops</h1>" })) }).exams();
  assert.equal(html.error.message, "Ошибка сервера (HTTP 500)");
});
