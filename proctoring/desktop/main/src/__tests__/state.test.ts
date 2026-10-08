import assert from "node:assert/strict";
import { test } from "node:test";
import { ShellStateMachine } from "../shell/state";
import { FakeGuard, sessionInfo } from "./helpers";

const mk = () => {
  const guard = new FakeGuard();
  const m = new ShellStateMachine(guard, { shell_version: "0.1.0", platform: "test" });
  return { guard, m };
};

test("starts in normal mode with nothing engaged", () => {
  const { m } = mk();
  assert.equal(m.state.mode, "normal");
  assert.equal(m.state.exam_mode_active, false);
  assert.equal(m.state.operator_unlocked, false);
});

test("bind -> preflight; running engages exam; finish releases to normal", async () => {
  const { guard, m } = mk();
  await m.bind(sessionInfo("s1", "created"));
  assert.equal(m.state.mode, "preflight");
  assert.equal(guard.active, false, "preflight never engages restrictions");
  await m.observe(sessionInfo("s1", "ready"));
  assert.equal(guard.active, false);
  await m.observe(sessionInfo("s1", "running"));
  assert.equal(m.state.mode, "exam");
  assert.equal(m.state.exam_mode_active, true);
  await m.observe(sessionInfo("s1", "running")); // stream duplicate: no second engage
  assert.deepEqual(guard.calls, ["engage:s1"]);
  await m.observe(sessionInfo("s1", "finished"));
  assert.equal(m.state.mode, "normal");
  assert.equal(m.state.exam_mode_active, false);
  assert.deepEqual(guard.calls, ["engage:s1", "release:session_finished"]);
});

test("pause releases (operator), resume re-engages, abort releases", async () => {
  const { guard, m } = mk();
  await m.bind(sessionInfo("s1", "ready"));
  await m.observe(sessionInfo("s1", "running"));
  await m.observe(sessionInfo("s1", "paused"));
  assert.equal(m.state.mode, "preflight");
  assert.equal(guard.active, false);
  await m.observe(sessionInfo("s1", "running"));
  assert.equal(m.state.mode, "exam");
  await m.observe(sessionInfo("s1", "aborted"));
  assert.equal(guard.active, false);
  assert.deepEqual(guard.calls, ["engage:s1", "release:session_paused", "engage:s1", "release:session_aborted"]);
});

test("observations for other (unbound) sessions are ignored", async () => {
  const { guard, m } = mk();
  await m.bind(sessionInfo("s1", "ready"));
  await m.observe(sessionInfo("other", "running"));
  assert.equal(guard.active, false);
  assert.equal(m.state.mode, "preflight");
});

test("backend loss releases immediately and reports error", async () => {
  const { guard, m } = mk();
  await m.bind(sessionInfo("s1", "ready"));
  await m.observe(sessionInfo("s1", "running"));
  await m.backendLost("killed");
  assert.equal(guard.active, false);
  assert.equal(m.state.mode, "error");
  assert.equal(m.state.last_error?.details.shell_code, "backend_unavailable");
});

test("engage failure leaves everything released and mode=error", async () => {
  const { guard, m } = mk();
  guard.failNextEngage = true;
  await m.bind(sessionInfo("s1", "ready"));
  await m.observe(sessionInfo("s1", "running"));
  assert.equal(guard.active, false);
  assert.equal(m.state.mode, "error");
  assert.equal(m.state.exam_mode_active, false);
  assert.match(m.state.last_error?.message ?? "", /kiosk refused/);
  assert.deepEqual(guard.calls, ["engage:s1", "release:engage_failed"]);
});

test("finish arriving while engage is in flight still ends released (serialized)", async () => {
  const { guard, m } = mk();
  guard.engageDelayMs = 30;
  await m.bind(sessionInfo("s1", "ready"));
  const a = m.observe(sessionInfo("s1", "running"));
  const b = m.observe(sessionInfo("s1", "finished"));
  await Promise.all([a, b]);
  await m.settle();
  assert.equal(guard.active, false);
  assert.equal(m.state.mode, "normal");
});

test("emergency latch: a session can never re-engage after an emergency exit", async () => {
  const { guard, m } = mk();
  await m.bind(sessionInfo("s1", "ready"));
  await m.observe(sessionInfo("s1", "running"));
  m.forbidEngage("s1");
  await m.releaseTo("normal", "emergency_exit", null);
  await m.observe(sessionInfo("s1", "running")); // abort failed, backend still says running
  assert.equal(guard.active, false);
  assert.equal(m.state.mode, "error");
});

test("operator unlock is cleared on EVERY engage: start, resume, recovery", async () => {
  const { m } = mk();
  await m.bind(sessionInfo("s1", "ready"));
  m.setOperator(true);
  await m.observe(sessionInfo("s1", "running"));
  assert.equal(m.state.operator_unlocked, false, "start");
  m.setOperator(true); // teacher unlocks to pause
  await m.observe(sessionInfo("s1", "paused"));
  assert.equal(m.state.operator_unlocked, true, "still unlocked while paused (teacher present)");
  await m.observe(sessionInfo("s1", "running"));
  assert.equal(m.state.operator_unlocked, false, "resume: student at the keyboard again, PIN needed to pause");
  m.setOperator(true);
  await m.backendLost("x"); // release path ...
  await m.bind(sessionInfo("s2", "ready"));
  m.setOperator(true);
  await m.releaseTo("error", "renderer_gone", null);
  await m.observe(sessionInfo("s2", "running")); // ... and recovery re-engage
  assert.equal(m.state.operator_unlocked, false, "recovery re-engage");
});

test("engageForbidden reports the latch", async () => {
  const { m } = mk();
  assert.equal(m.engageForbidden("s1"), false);
  m.forbidEngage("s1");
  assert.equal(m.engageForbidden("s1"), true);
});

test("rebinding releases a still-engaged previous session", async () => {
  const { guard, m } = mk();
  await m.bind(sessionInfo("s1", "ready"));
  await m.observe(sessionInfo("s1", "running"));
  await m.bind(sessionInfo("s2", "created"));
  assert.equal(guard.active, false);
  assert.equal(m.state.session_id, "s2");
  assert.equal(m.state.mode, "preflight");
});

test("listeners get every change; a throwing listener does not break the machine", async () => {
  const { m } = mk();
  const modes: string[] = [];
  m.onChange(() => {
    throw new Error("bad listener");
  });
  m.onChange((s) => modes.push(s.mode));
  await m.bind(sessionInfo("s1", "ready"));
  await m.observe(sessionInfo("s1", "running"));
  await m.observe(sessionInfo("s1", "finished"));
  assert.ok(modes.includes("exam"));
  assert.ok(modes.includes("releasing"));
  assert.equal(modes.at(-1), "normal");
});
