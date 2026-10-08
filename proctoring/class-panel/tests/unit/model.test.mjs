import assert from "node:assert/strict";
import { test } from "node:test";
import { attentionReasons, displayState, mergeStudent, naturalCompare, normalizeIncident, normalizeStudent, ZONE_LABEL } from "../../src/model.js";

const NOW = 1_800_000_000_000;
const status = (over = {}) => ({
  student_id: "s1",
  computer_name: "ПК-01",
  student_label: "Студент 01",
  exam_state: "running",
  camera: "ok",
  monitoring: "ok",
  zone: "green",
  zone_reasons_ru: ["Эпизодов нет, наблюдение полное"],
  incidents_total: 0,
  incidents_by_priority: { low: 0, medium: 0, high: 0 },
  locked: false,
  mic_active: false,
  ...over,
});

test("normalizeStudent reads protocol status fields; invalid values become null, never a default", () => {
  const n = normalizeStudent(status({ camera: "maybe", zone: "purple", incidents_total: -3, locked: "yes", extra_field: 1 }), NOW);
  assert.ok(n);
  assert.equal(n.view.camera, null);
  assert.equal(n.view.zone, null);
  assert.equal(n.view.incidentsTotal, null);
  assert.equal(n.view.locked, null);
  assert.deepEqual(n.unknownKeys, ["extra_field"]);
  assert.equal(n.view.lastStatusAt, NOW, "local receipt time when the server sends none");
  assert.equal(normalizeStudent({ zone: "red" }, NOW), null, "no id → dropped");
  assert.equal(normalizeStudent("x", NOW), null);
  const many = normalizeStudent(status({ zone_reasons_ru: ["a", "b", "c", "d", 5] }), NOW);
  assert.deepEqual(many?.view.zoneReasons, ["a", "b", "c"], "≤ 3 reasons (protocol)");
  const bad = normalizeStudent(status({ incidents_by_priority: { low: 1, medium: "2", high: 0 } }), NOW);
  assert.equal(bad?.view.byPriority, null);
});

test("mergeStudent applies only the fields present in a partial update", () => {
  const a = /** @type {NonNullable<ReturnType<typeof normalizeStudent>>} */ (normalizeStudent(status({ zone: "yellow" }), NOW));
  const rawUpd = { student_id: "s1", connected: false };
  const b = /** @type {NonNullable<ReturnType<typeof normalizeStudent>>} */ (normalizeStudent(rawUpd, NOW + 5000));
  const m = mergeStudent(a.view, b.view, rawUpd);
  assert.equal(m.zone, "yellow", "zone kept");
  assert.equal(m.connected, false);
  assert.equal(m.connectedSource, "server");
  assert.equal(m.lastStatusAt, NOW, "a connection-only update is not a status");
});

test("displayState: grey means missing/old data, with the reason; colour never invented", () => {
  const v = /** @type {NonNullable<ReturnType<typeof normalizeStudent>>} */ (normalizeStudent(status({ zone: "green" }), NOW)).view;
  assert.equal(displayState(v, NOW + 1000, true).zone, "green");
  const old = displayState(v, NOW + 12_000, true);
  assert.equal(old.zone, "grey");
  assert.match(old.reasons[0] ?? "", /Нет статуса 12 с/);
  assert.equal(displayState({ ...v, camera: "off" }, NOW + 1000, true).zone, "grey");
  assert.equal(displayState({ ...v, zone: null }, NOW + 1000, true).zone, "grey");
  const off = displayState({ ...v, connected: false, connectedSource: "server" }, NOW + 1000, true);
  assert.equal(off.link, "offline");
  assert.equal(off.zone, "grey", "a student without link is never shown as current green");
  const feedDown = displayState(v, NOW + 1000, false);
  assert.equal(feedDown.link, "unknown", "panel without server: no online/offline claims");
  assert.equal(feedDown.zone, "grey");
  assert.match(feedDown.reasons[0] ?? "", /Нет связи панели с сервером/);
  assert.equal(displayState({ ...v, zone: "red", zoneReasons: ["Телефон в кадре — 10:01"] }, NOW + 1000, true).reasons[0], "Телефон в кадре — 10:01");
});

test("attention reasons come only from delivered data", () => {
  const v = /** @type {NonNullable<ReturnType<typeof normalizeStudent>>} */ (normalizeStudent(status(), NOW)).view;
  const r = (x, t = NOW + 1000, live = true) => attentionReasons(x, displayState(x, t, live)).map((y) => y.key);
  assert.deepEqual(r(v), [], "green, online, camera ok → not in the queue");
  assert.deepEqual(r({ ...v, zone: "red" }), ["zone-red"]);
  assert.deepEqual(r({ ...v, zone: "yellow" }), ["zone-yellow"]);
  assert.ok(r({ ...v, connected: false, connectedSource: "server" }).includes("link"));
  assert.ok(r({ ...v, camera: "busy" }).includes("camera"));
  assert.deepEqual(r({ ...v, examState: "finished", connected: false, connectedSource: "server" }), [], "a finished student who left is not 'attention'");
  assert.equal(ZONE_LABEL.yellow, "Требует внимания");
});

test("normalizeIncident keeps protocol fields and never upgrades an episode to a verdict", () => {
  const i = normalizeIncident({ incident_id: "i1", student_id: "s1", rule_id: "phone_visible", priority: "medium", state: "open", t_start_wall: "2026-10-08T09:00:00Z", explanation_ru: "Телефон в кадре" });
  assert.equal(i?.priority, "medium");
  assert.equal(i?.decision, null, "no decision unless the data says so");
  assert.equal(normalizeIncident({ rule_id: "x" }), null);
});

test("natural order of computer names", () => {
  assert.ok(naturalCompare("ПК-2", "ПК-10") < 0);
  assert.ok(naturalCompare("Студент 9", "Студент 10") < 0);
});

test("source provenance survives partial updates and never defaults to real", () => {
  const initial = normalizeStudent(status({ origin: "simulated" }), NOW).view;
  const raw = { student_id: "s1", connected: true };
  assert.equal(mergeStudent(initial, normalizeStudent(raw, NOW).view, raw).origin, "simulated");
  for (const origin of ["simulated", "replay", "real", "unknown"]) {
    assert.equal(normalizeStudent(status({ origin }), NOW).view.origin, origin);
    assert.equal(normalizeIncident({ incident_id: "i1", origin }).origin, origin);
  }
  assert.equal(normalizeStudent(status(), NOW).view.origin, "unknown");
  assert.equal(normalizeStudent(status({ origin: "LIVE" }), NOW).view.origin, "unknown");
  assert.equal(normalizeIncident({ incident_id: "i1" }).origin, "unknown");
});
