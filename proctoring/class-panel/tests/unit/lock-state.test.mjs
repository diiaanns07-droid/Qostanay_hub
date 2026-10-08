import assert from "node:assert/strict";
import test from "node:test";
import { normalizeStudent, mergeStudent, displayState, STATUS_STALE_MS } from "../../src/model.js";

const now = Date.now();
const raw = { student_id: "s", connected: true, camera: "ok", zone: "green", last_status_at: now,
  locked: false, lock_state: "applied", lock_confirmed: true, lock_requested: false, lock_scope: "app_overlay" };
const view = (data) => normalizeStudent(data, now).view;
test("only fresh scoped receipt changes the public lock label to confirmed", () => {
  assert.equal(displayState(view({ ...raw, lock_confirmed: false }), now, true).locked, null);
  assert.equal(displayState(view({ student_id: "s", locked: false, connected: true, last_status_at: now }), now, true).locked, null);
  const s = view(raw);
  assert.equal(displayState(s, now, true).locked, false);
  assert.match(displayState(s, now, true).lockLabel, /подтверждено приложением/);
  assert.equal(displayState(s, now + STATUS_STALE_MS + 1, true).locked, null);
  assert.equal(displayState(s, now, false).locked, null);
  assert.equal(displayState(view({ ...raw, connected: false }), now, true).locked, null);
  assert.equal(displayState(view({ ...raw, lock_state: "requested" }), now, true).locked, null);
});
test("later legacy status invalidates confirmation while unrelated updates preserve it", () => {
  const previous = view(raw);
  const patch = { student_id: "s", student_label: "New label" };
  assert.equal(mergeStudent(previous, view(patch), patch).lockConfirmed, true);
  const legacy = { student_id: "s", locked: false };
  const next = mergeStudent(previous, view(legacy), legacy);
  assert.equal(next.lockConfirmed, false);
  assert.equal(displayState(next, now, true).locked, null);
});
