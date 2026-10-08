import assert from "node:assert/strict";
import { test } from "node:test";
import { accessTable, decideAccess, OperatorSession, type AccessContext } from "../shell/access";
import { INVOKE, type InvokeName } from "../ipc/channels";

const ALL = Object.keys(INVOKE) as InvokeName[];
const ctx = (examActive: boolean, operatorUnlocked: boolean, bound: string | null = "s1"): AccessContext => ({
  examActive,
  operatorUnlocked,
  boundSessionId: bound,
});

test("default policy: during the exam no review/history/report/evidence, even when unlocked", () => {
  for (const m of ["listSessions", "listIncidents", "getIncident", "addReview", "getEvidence", "getSummary", "exportReport", "deleteSession"] as const) {
    for (const unlocked of [false, true]) {
      const d = decideAccess(m, "s1", ctx(true, unlocked), "review_after_pause");
      assert.equal(d.allow, false, `${m} unlocked=${unlocked}`);
      assert.equal(!d.allow && d.shellCode, "exam_mode_active");
    }
  }
});

test("default policy: after pause/finish the teacher reviews with the unlock", () => {
  assert.equal(decideAccess("listIncidents", "s1", ctx(false, false), "review_after_pause").allow, true);
  assert.equal(decideAccess("getSummary", "s1", ctx(false, false), "review_after_pause").allow, true);
  const locked = decideAccess("addReview", "s1", ctx(false, false), "review_after_pause");
  assert.equal(!locked.allow && locked.shellCode, "operator_locked");
  assert.equal(decideAccess("addReview", "s1", ctx(false, true), "review_after_pause").allow, true);
  assert.equal(decideAccess("exportReport", "s1", ctx(false, true), "review_after_pause").allow, true);
});

test("live-review policy: only unlocked, only the bound session, never history/export/delete", () => {
  const p = "operator_live_review" as const;
  const lockedTry = decideAccess("listIncidents", "s1", ctx(true, false), p);
  assert.equal(!lockedTry.allow && lockedTry.shellCode, "operator_locked");
  assert.equal(decideAccess("listIncidents", "s1", ctx(true, true), p).allow, true);
  assert.equal(decideAccess("addReview", "s1", ctx(true, true), p).allow, true);
  assert.equal(decideAccess("getEvidence", "s1", ctx(true, true), p).allow, true);
  const other = decideAccess("listIncidents", "older-session", ctx(true, true), p);
  assert.equal(!other.allow && other.shellCode, "session_not_bound");
  const nonString = decideAccess("getIncident", { toString: () => "s1" }, ctx(true, true), p);
  assert.equal(nonString.allow, false, "only a string equal to the bound id passes");
  for (const m of ["listSessions", "exportReport", "deleteSession"] as const) {
    assert.equal(decideAccess(m, "s1", ctx(true, true), p).allow, false, m);
  }
});

test("operator-only actions need the unlock in every mode and policy", () => {
  for (const p of ["review_after_pause", "operator_live_review"] as const) {
    for (const m of ["pauseExam", "resumeExam", "calibrationSkip", "addReview", "getEvidence", "exportReport", "deleteSession"] as const) {
      assert.equal(decideAccess(m, "s1", ctx(false, false), p).allow, false, `${p} ${m}`);
    }
    assert.equal(decideAccess("pauseExam", "s1", ctx(true, true), p).allow, true);
  }
});

test("student and lifecycle methods are never closed by the teacher policy", () => {
  for (const m of ["getShellState", "health", "getSession", "getExam", "saveAnswer", "listAnswers", "finishExam", "abortExam", "requestEmergencyExit", "operatorUnlock", "operatorLock"] as const) {
    for (const exam of [true, false]) assert.equal(decideAccess(m, "s1", ctx(exam, false), "review_after_pause").allow, true, `${m} exam=${exam}`);
  }
});

test("accessTable covers every bridge method (for the A07 handoff)", () => {
  const t = accessTable(ALL, "review_after_pause");
  assert.equal(t.length, ALL.length);
  const row = t.find((r) => r.method === "listIncidents")!;
  assert.deepEqual(row, { method: "listIncidents", exam_locked: false, exam_unlocked: false, idle_locked: true, idle_unlocked: true });
  const live = accessTable(ALL, "operator_live_review").find((r) => r.method === "listIncidents")!;
  assert.equal(live.exam_unlocked, true);
  assert.equal(live.exam_locked, false);
});

test("operator unlock expires after inactivity and after the absolute limit", async () => {
  let now = 0;
  const expired: string[] = [];
  const s = new OperatorSession({ idleMs: 1_000, maxMs: 5_000, now: () => now, onExpire: (r) => expired.push(r) });
  assert.equal(s.check(), false, "not unlocked yet");
  s.unlocked();
  now = 900;
  assert.equal(s.check(), true);
  s.touch();
  now = 1_800;
  assert.equal(s.check(), true, "touch extended the idle window");
  now = 2_900;
  assert.equal(s.check(), false, "idle expiry");
  assert.deepEqual(expired, ["idle"]);
  s.unlocked();
  for (let t = 3_500; t <= 8_000; t += 500) {
    now = t;
    s.touch();
  }
  assert.equal(s.check(), false, "absolute limit even with activity");
  assert.deepEqual(expired, ["idle", "max"]);
  s.locked();
});

test("expiry timer locks proactively without any call (UI updates)", async () => {
  const expired: string[] = [];
  const s = new OperatorSession({ idleMs: 60, maxMs: 10_000, onExpire: (r) => expired.push(r) });
  s.unlocked();
  await new Promise((r) => setTimeout(r, 150));
  assert.deepEqual(expired, ["idle"]);
  assert.equal(s.active, false);
});
