import assert from "node:assert/strict";
import { test } from "node:test";
import type { BrowserWindow } from "electron";
import type { BackendClient } from "../backend/client";
import { ClassLockController } from "../class-lock";
import { receiptFor, type LockRequest } from "../../../shared/class-lock";

const request = (locked = true): LockRequest => ({ command_id: locked ? "cmd-lock" : "cmd-unlock", student_id: "student-1",
  class_session_id: "class-1", source_session_id: "local-1", backend_instance_id: "backend-1", request_token: locked ? "token-1" : "token-2",
  locked, reason_ru: locked ? "Проверка телефона" : null, expires_at: new Date(Date.now() + 5000).toISOString(), recovery: false });
function setup(visible = true, rendered = true) {
  const calls: unknown[] = [], blocked: boolean[] = [];
  const client = { json: async (...args: unknown[]) => { calls.push(args); return { ok: true, data: { accepted: true } }; } } as unknown as BackendClient;
  const window = { isDestroyed: () => false, isVisible: () => visible,
    webContents: { isDestroyed: () => false, executeJavaScript: async () => rendered } } as unknown as BrowserWindow;
  const controller = new ClassLockController({ client, window: () => window, setExamBlocked: value => blocked.push(value) });
  return { controller, calls, blocked };
}
test("pending lock hides exam surface before any receipt; successful DOM verification posts exact scope", async () => {
  const { controller, calls, blocked } = setup();
  const r = request();
  controller.consumeClassState({ type: "class_state", locked: false, lock_request: r });
  assert.deepEqual(blocked, [true]);
  assert.equal(calls.length, 0);
  assert.deepEqual(await controller.confirmApplied(receiptFor(r, true)), { accepted: true });
  assert.deepEqual(calls[0], ["POST", "/v1/class/lock/ack", receiptFor(r, true), 2500]);
});
test("hidden renderer and wrong scopes cannot acknowledge", async () => {
  const hidden = setup(false), normal = setup();
  const r = request();
  for (const { controller } of [hidden, normal]) controller.consumeClassState({ type: "class_state", locked: false, lock_request: r });
  assert.equal((await hidden.controller.confirmApplied(receiptFor(r, true))).accepted, false);
  for (const key of ["command_id", "student_id", "class_session_id", "source_session_id", "backend_instance_id", "request_token"]) {
    assert.equal((await normal.controller.confirmApplied({ ...receiptFor(r, true), [key]: "wrong" })).accepted, false);
  }
  assert.equal(hidden.calls.length + normal.calls.length, 0);
});
test("DOM rejection posts applied=false; unlock only releases after verified removal", async () => {
  const failed = setup(true, false), ok = setup();
  const lock = request(), unlock = request(false);
  failed.controller.consumeClassState({ type: "class_state", locked: false, lock_request: lock });
  await failed.controller.confirmApplied(receiptFor(lock, true));
  assert.equal(((failed.calls[0] as unknown[])[2] as { applied: boolean }).applied, false);
  ok.controller.consumeClassState({ type: "class_state", locked: true, lock_request: unlock });
  assert.deepEqual(ok.blocked, [true]);
  await ok.controller.confirmApplied(receiptFor(unlock, true));
  assert.deepEqual(ok.blocked, [true, false]);
});
test("reset rejects old renderer replies and malformed messages cannot release", async () => {
  const { controller, calls, blocked } = setup();
  const r = request();
  controller.consumeClassState({ type: "class_state", locked: true, lock_request: r });
  controller.consumeClassState({ type: "class_state", locked: false, lock_request: { locked: false } });
  assert.deepEqual(blocked, [true]);
  controller.reset();
  assert.equal((await controller.confirmApplied(receiptFor(r, true))).accepted, false);
  assert.equal(calls.length, 0);
});
test("renderer loss blocks immediately and invalidates only the current backend", async () => {
  const { controller, calls, blocked } = setup();
  controller.consumeClassState({ type: "class_state", locked: true, lock_request: request() });
  await controller.rendererLost();
  assert.equal(blocked.at(-1), true);
  assert.deepEqual(calls[0], ["POST", "/v1/class/lock/lost", { backend_instance_id: "backend-1" }, 2500]);
  controller.consumeClassState({ type: "class_state", locked: false, lock_requested: true, lock_confirmed: false, lock_request: null });
  assert.equal(blocked.at(-1), true);
});
