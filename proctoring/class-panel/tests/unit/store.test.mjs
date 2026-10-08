import assert from "node:assert/strict";
import { test } from "node:test";
import { ESCALATE_MIN_MS, PanelStore, REORDER_MS } from "../../src/store.js";

function mk(start = 1_800_000_000_000) {
  let now = start;
  const s = new PanelStore({ now: () => now, schedule: () => {} });
  s.setConnection({ status: "live" });
  return { s, tick: (ms) => (now += ms), at: () => now };
}

const st = (id, over = {}) => ({
  student_id: id,
  computer_name: `ПК-${id}`,
  student_label: `Студент ${id}`,
  exam_state: "running",
  camera: "ok",
  zone: "green",
  incidents_total: 0,
  ...over,
});

test("priority order follows the protocol: red → yellow → grey → green", () => {
  const { s } = mk();
  s.snapshot([st("1"), st("2", { zone: "yellow" }), st("3", { zone: "red" }), st("4", { camera: "off" })]);
  assert.deepEqual(s.order, ["3", "2", "4", "1"]);
});

test("cards do not jump on every event: same-zone event changes wait for the slow cadence", () => {
  const { s, tick, at } = mk();
  s.snapshot([st("1", { last_event_at: new Date(at() - 60_000).toISOString() }), st("2", { last_event_at: new Date(at() - 30_000).toISOString() })]);
  assert.deepEqual(s.order, ["2", "1"]);
  tick(500);
  s.studentUpdate(st("1", { last_event_at: new Date(at()).toISOString() }));
  s.tick();
  assert.deepEqual(s.order, ["2", "1"], "newer event, same zone: no move yet");
  tick(REORDER_MS);
  s.tick();
  assert.deepEqual(s.order, ["1", "2"], "moves on the periodic reorder");
});

test("a zone escalation reorders quickly but never more often than ESCALATE_MIN_MS", () => {
  const { s, tick } = mk();
  s.snapshot([st("1"), st("2")]);
  const before = [...s.order];
  tick(200);
  s.studentUpdate(st(before[1] ?? "2", { zone: "red" }));
  s.tick();
  assert.deepEqual(s.order, before, "too soon after the last reorder");
  tick(ESCALATE_MIN_MS);
  s.tick();
  assert.equal(s.order[0], before[1], "escalated student moves to the top");
});

test("pinned order: nothing moves, newcomers are appended", () => {
  const { s, tick } = mk();
  s.snapshot([st("1"), st("2")]);
  s.setFrozen(true);
  const before = [...s.order];
  s.studentUpdate(st("2", { zone: "red" }));
  s.studentUpdate(st("9", { zone: "red" }));
  tick(REORDER_MS * 2); // several reorder periods, but statuses are still fresh (< 10 s)
  s.tick();
  assert.deepEqual(s.order, [...before, "9"]);
  s.setFrozen(false);
  assert.ok(s.order[0] === "2" || s.order[0] === "9", `red first after unpinning, got ${s.order.join(",")}`);
});

test("queue + acknowledgement: 'просмотрено' hides until something new happens", () => {
  const { s, tick, at } = mk();
  s.snapshot([st("1", { zone: "yellow", incidents_total: 1 }), st("2")]);
  assert.deepEqual(s.queue().map((x) => x.entry.view.id), ["1"]);
  s.acknowledge("1");
  assert.equal(s.queue().length, 0);
  tick(2000);
  s.studentUpdate(st("1", { zone: "yellow", incidents_total: 1 }));
  assert.equal(s.queue().length, 0, "same state again: still acknowledged");
  s.incident({ incident_id: "i9", student_id: "1", priority: "low", t_start_wall: new Date(at()).toISOString() });
  assert.equal(s.queue().length, 1, "new event brings it back");
});

test("filters: zone chips, link, case-insensitive Cyrillic search; sort by name is natural", () => {
  const { s } = mk();
  s.snapshot([st("1"), st("2", { zone: "red" }), st("10", { zone: "red", student_label: "Айгерим" })]);
  s.setFilter({ zones: new Set(["red"]) });
  assert.deepEqual([...s.students.keys()].filter((id) => s.matchesFilter(id)).sort(), ["10", "2"]);
  s.setFilter({ zones: new Set(), query: "айгер" });
  assert.deepEqual([...s.students.keys()].filter((id) => s.matchesFilter(id)), ["10"]);
  s.setFilter({ query: "", sort: "computer" });
  assert.deepEqual(s.order, ["1", "2", "10"]);
});

test("counters: online/offline from data; unknown while the panel has no server", () => {
  const { s } = mk();
  s.snapshot([st("1"), st("2", { connected: false })]);
  assert.deepEqual([s.counters().online, s.counters().offline], [1, 1]);
  s.setConnection({ status: "reconnecting" });
  const c = s.counters();
  assert.equal(c.online + c.offline, 0);
  assert.equal(c.unknown, 2);
  assert.equal(c.zones.grey, 2, "nothing is current without the server");
});

test("snapshot removes students that the server no longer lists; flushes are coalesced", () => {
  let scheduled = 0;
  const s = new PanelStore({ now: () => 1, schedule: () => void (scheduled += 1) });
  s.snapshot([st("1"), st("2")]);
  s.studentUpdate(st("1", { zone: "red" }));
  s.studentUpdate(st("2", { zone: "red" }));
  assert.equal(scheduled, 1, "one flush for many updates");
  s.flush();
  s.snapshot([st("2")]);
  assert.deepEqual([...s.students.keys()], ["2"]);
});

test("malformed messages are counted, not guessed", () => {
  const { s } = mk();
  s.studentUpdate({ zone: "red" });
  s.incident({ incident_id: "x" });
  assert.equal(s.diag.droppedMessages, 2);
  assert.equal(s.students.size, 0);
});

test("without a live server feed the attention queue is empty (the panel's link is the problem, not the students)", () => {
  const { s } = mk();
  s.snapshot([st("1", { zone: "red" }), st("2", { zone: "yellow" })]);
  assert.equal(s.queue().length, 2);
  s.setConnection({ status: "reconnecting" });
  assert.equal(s.queue().length, 0);
  assert.equal(s.counters().queue, 0);
});
