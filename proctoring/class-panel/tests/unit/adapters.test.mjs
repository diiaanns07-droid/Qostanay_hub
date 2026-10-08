import assert from "node:assert/strict";
import { test } from "node:test";
import { createDemoAdapter, rng } from "../../src/adapters/demo.js";
import { createRealAdapter } from "../../src/adapters/real.js";
import { ModuleRegistry } from "../../src/extensions.js";
import { PanelStore } from "../../src/store.js";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
globalThis.location ??= /** @type {any} */ ({ protocol: "http:", host: "127.0.0.1:8790" });

class FakeWS {
  static last = /** @type {FakeWS|null} */ (null);
  constructor(url) {
    this.url = url;
    FakeWS.last = this;
    this.onopen = null;
    this.onmessage = null;
    this.onclose = null;
    this.onerror = null;
    setTimeout(() => this.onopen?.(), 5);
  }
  send() {}
  close() {
    this.onclose?.({ code: 1000 });
  }
  emit(obj) {
    this.onmessage?.({ data: typeof obj === "string" ? obj : JSON.stringify(obj) });
  }
}

const json = (status, body) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const store = () => new PanelStore({ schedule: () => {} });

test("REAL: snapshot from GET /api/teacher/students (array or {students}) then live stream", async () => {
  for (const body of [[{ student_id: "a", zone: "red", exam_state: "running", camera: "ok" }], { students: [{ student_id: "a", zone: "red", exam_state: "running", camera: "ok" }] }]) {
    const calls = [];
    const s = store();
    const a = createRealAdapter({ fetchImpl: async (u) => (calls.push(String(u)), json(200, body)), WebSocketImpl: /** @type {any} */ (FakeWS) });
    a.start(s);
    await sleep(30);
    assert.equal(s.connection.status, "live");
    assert.equal(s.students.get("a")?.view.zone, "red");
    assert.deepEqual(calls, ["/api/teacher/students"]);
    assert.equal(FakeWS.last?.url, "ws://127.0.0.1:8790/ws/teacher", "same origin, no token in the URL");
    FakeWS.last?.emit({ type: "student_update", student: { student_id: "a", zone: "green" } });
    FakeWS.last?.emit({ type: "incident", student_id: "a", incident_id: "i1", priority: "high", state: "open" });
    FakeWS.last?.emit({ type: "preview", student_id: "a", jpeg_b64: "/9j/4AAQSkZJRg==", frame_wall: "2026-10-08T09:00:00Z" });
    FakeWS.last?.emit({ type: "preview", student_id: "a", jpeg_b64: "<script>" });
    FakeWS.last?.emit({ type: "mystery" });
    FakeWS.last?.emit("{not json");
    assert.equal(s.students.get("a")?.view.zone, "green");
    assert.equal(s.incidentsOf("a").length, 1);
    assert.match(s.students.get("a")?.preview?.url ?? "", /^data:image\/jpeg;base64,/);
    assert.equal(s.diag.droppedMessages, 3, "bad preview, unknown type, bad JSON");
    a.stop();
  }
});

test("REAL: 401 → login needed (no PIN sent by the panel); 403 → teacher computer only", async () => {
  for (const [code, status] of [[401, "auth"], [403, "forbidden"]]) {
    const s = store();
    const a = createRealAdapter({ fetchImpl: async () => json(code, {}), WebSocketImpl: /** @type {any} */ (FakeWS) });
    a.start(s);
    await sleep(30);
    assert.equal(s.connection.status, status);
    a.stop();
  }
});

test("REAL: server error → error + retry scheduled; unexpected shape → reported, not guessed", async () => {
  const s = store();
  const a = createRealAdapter({ fetchImpl: async () => json(500, {}), WebSocketImpl: /** @type {any} */ (FakeWS) });
  a.start(s);
  await sleep(30);
  assert.equal(s.connection.status, "error");
  assert.ok(s.connection.retryAt !== null);
  a.stop();
  const s2 = store();
  const b = createRealAdapter({ fetchImpl: async () => json(200, { cards: "?" }), WebSocketImpl: /** @type {any} */ (FakeWS) });
  b.start(s2);
  await sleep(30);
  assert.equal(s2.connection.status, "error");
  assert.match(s2.connection.detail, /Формат списка/);
  assert.equal(s2.students.size, 0);
  b.stop();
});

test("REAL: stream loss → reconnecting (data kept, marked old), reconnect → resync", async () => {
  let n = 0;
  const s = store();
  const a = createRealAdapter({
    fetchImpl: async () => (n++, json(200, [{ student_id: "a", zone: "green", exam_state: "running", camera: "ok" }])),
    WebSocketImpl: /** @type {any} */ (FakeWS),
  });
  a.start(s);
  await sleep(30);
  const first = FakeWS.last;
  first?.onclose?.({ code: 1006 });
  assert.equal(s.connection.status, "reconnecting");
  assert.equal(s.students.size, 1, "last known data stays (shown as stale)");
  await sleep(1100);
  assert.notEqual(FakeWS.last, first);
  assert.equal(s.connection.status, "live");
  assert.ok(n >= 2, "list re-read after reconnect");
  a.stop();
});

test("REAL: incidents of one student via the contract route", async () => {
  const urls = [];
  const a = createRealAdapter({ fetchImpl: async (u) => (urls.push(String(u)), json(200, [{ incident_id: "i1" }])), WebSocketImpl: /** @type {any} */ (FakeWS) });
  const r = await a.getIncidents("st/../x");
  assert.equal(r.ok, true);
  assert.equal(urls[0], "/api/teacher/students/st%2F..%2Fx/incidents", "id is encoded, cannot change the route");
});

test("DEMO: deterministic for a seed; 2 / 30 / 100 students; zones imitate A05 thresholds", async () => {
  assert.equal(rng(3)(), rng(3)());
  for (const n of [2, 30, 100]) {
    const s = store();
    const a = createDemoAdapter({ students: n, seed: 7, setTimer: () => 0, clearTimer: () => {} });
    a.start(s);
    await sleep(400);
    assert.equal(s.students.size, n);
    assert.equal(s.connection.status, "live");
    for (const e of s.students.values()) {
      const p = e.view.byPriority;
      if (!p || e.view.zone === "grey") continue;
      const expect = p.high >= 1 || p.medium >= 3 ? "red" : p.medium >= 1 || p.low >= 3 ? "yellow" : "green";
      assert.equal(e.view.zone, expect, `${e.view.id} ${JSON.stringify(p)}`);
    }
    a.stop();
  }
});

test("module registry: validation, external module replaces the built-in of its slot", () => {
  const r = new ModuleRegistry();
  r.register({ id: "builtin-episodes", slot: "history", title: "Эпизоды", mount() {}, builtin: true });
  assert.equal(r.forSlot("history")[0]?.id, "builtin-episodes");
  const off = r.register({ id: "a08-history", slot: "history", title: "История", mount() {} });
  assert.deepEqual(r.forSlot("history").map((m) => m.id), ["a08-history"]);
  off();
  assert.deepEqual(r.forSlot("history").map((m) => m.id), ["builtin-episodes"]);
  assert.throws(() => r.register({ id: "Bad Id", slot: "history", title: "", mount() {} }));
  assert.throws(() => r.register({ id: "x", slot: "video", title: "", mount() {} }));
  assert.equal(r.forSlot("commands").length, 0);
});
