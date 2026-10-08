import assert from "node:assert/strict";
import test from "node:test";
import { currentLock, commandState, unavailable } from "../classroom-model.js";

const card = { student_id: "s", session_id: "class-1", connected: true, stale: false, capabilities: ["lock", "unlock", "start_exam", "finish_exam"], exam_state: "preflight" };
const confirmed = { ...card, locked: false, lock_state: "applied", lock_confirmed: true, lock_scope: "app_overlay" };
test("legacy false and invalid confirmation cannot claim an open screen", () => {
  assert.equal(currentLock({ ...card, locked: false }).state, "unknown");
  assert.equal(currentLock({ ...confirmed, lock_confirmed: "true" }).state, "unknown");
  assert.equal(currentLock({ ...confirmed, lock_scope: "os" }).state, "unknown");
  assert.equal(currentLock(confirmed).state, "unlocked");
  assert.equal(currentLock({ ...confirmed, locked: true }).state, "locked");
});
test("pending, failed, stale and disconnected replace current confirmation", () => {
  assert.equal(currentLock({ ...confirmed, lock_state: "requested", lock_requested: true }).state, "pending");
  assert.equal(currentLock({ ...confirmed, lock_state: "failed" }).state, "failed");
  assert.equal(currentLock({ ...confirmed, stale: true }).state, "unknown");
  assert.equal(currentLock({ ...confirmed, connected: false }).state, "unknown");
});
test("command acceptance and old/late/foreign ACKs never claim an effective lock", () => {
  const cmd = { kind: "lock", status: "succeeded", ack: { ok: true, result: { locked: true } } };
  assert.equal(commandState({ ...cmd, status: "sent" }, card).state, "pending");
  assert.equal(commandState(cmd, card).state, "unconfirmed");
  const result = { locked: true, lock_scope: "app_overlay", lock_state: "applied", backend_instance_id: "backend-1", class_session_id: "class-1" };
  assert.equal(commandState({ ...cmd, ack: { ok: true, result } }, card).state, "confirmed");
  assert.equal(commandState({ ...cmd, ack: { ok: true, late: true, result } }, card).state, "unconfirmed");
  assert.equal(commandState({ ...cmd, ack: { ok: true, result: { ...result, class_session_id: "other" } } }, card).state, "unconfirmed");
  assert.equal(commandState({ ...cmd, status: "failed", ack: { error_ru: "Нет подтверждения экрана" } }, card).state, "failed");
});
test("only the current class and fresh supported actions are available", () => {
  const session = { session_id: "class-1", state: "open" };
  assert.equal(unavailable(session, card, "lock"), null);
  for (const change of [{ session_id: "old" }, { connected: false }, { stale: true }, { capabilities: [] }]) assert.ok(unavailable(session, { ...card, ...change }, "lock"));
  assert.ok(unavailable({ ...session, state: "closed" }, card, "lock"));
  assert.ok(unavailable(session, { ...card, exam_state: "running" }, "start_exam"));
});
