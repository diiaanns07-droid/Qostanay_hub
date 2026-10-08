// A11 ISOLATED REPRO (not an integration run): A06 @62a7fb1 BUILT main.cjs under A06's own Electron API
// stub + REAL A01r2 @29cadde backend (synthetic source, bootstrap engine). Calls the IPC handlers in exactly
// the order/argument shapes the A07 @3fef6fb renderer uses (file:line references printed per step).
// Never opens a camera, never uses real Electron, no OS keyboard hooks.
import { createRequire } from "node:module";
import { randomBytes, scryptSync } from "node:crypto";
import { resolve } from "node:path";

const require = createRequire(import.meta.url);
const desktop = resolve(process.argv[2] ?? "./proctoring/desktop");
const PIN = "246810";
const salt = randomBytes(16);
process.env.QORGAU_OPERATOR_PIN_HASH = `scrypt:${salt.toString("hex")}:${scryptSync(PIN, salt, 32, { N: 16384, r: 8, p: 1 }).toString("hex")}`;
process.env.QORGAU_SHELL_LOG_LEVEL ??= "error";
process.env.QORGAU_LOG_LEVEL ??= "WARNING";
process.env.QORGAU_SHELL_SELFTEST = "0";
process.env.QORGAU_SHELL_DEMO_OPERATOR = "0";

const stub = require(resolve(desktop, "main/tests/electron-stub.cjs"));
const { state } = stub;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms = 20_000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    const v = await fn();
    if (v) return v;
    await sleep(50);
  }
  return null;
}
require(resolve(desktop, "dist/main/main.cjs"));
stub.start();
await until(() => state.windows.some((w) => !w.destroyed && w.opts.webPreferences.preload));
const win = state.windows.find((w) => !w.destroyed && w.opts.webPreferences.preload);
const trusted = { sender: win.webContents, senderFrame: win.webContents.mainFrame };
const ch = {
  getShellState: "qorgau:shell:get-state", operatorUnlock: "qorgau:shell:operator-unlock", operatorLock: "qorgau:shell:operator-lock",
  emergencyExit: "qorgau:shell:emergency-exit", health: "qorgau:api:health", createSession: "qorgau:api:create-session",
  getSession: "qorgau:api:get-session", runPreflight: "qorgau:api:preflight", calibrationSkip: "qorgau:api:calibration-skip",
  startExam: "qorgau:api:start", pauseExam: "qorgau:api:pause", resumeExam: "qorgau:api:resume", finishExam: "qorgau:api:finish",
  abortExam: "qorgau:api:abort", getExam: "qorgau:api:exam", saveAnswer: "qorgau:api:save-answer", listAnswers: "qorgau:api:list-answers",
  listIncidents: "qorgau:api:list-incidents", getIncident: "qorgau:api:get-incident", addReview: "qorgau:api:add-review",
  getEvidence: "qorgau:api:get-evidence", getSummary: "qorgau:api:summary", exportReport: "qorgau:api:export-report",
  deleteSession: "qorgau:api:delete-session", listSessions: "qorgau:api:list-sessions",
};
const call = (name, ...args) => state.handles.get(ch[name])(trusted, ...args);
const sh = async () => {
  const s = await call("getShellState");
  return `mode=${s.mode} exam_mode_active=${s.exam_mode_active} operator_unlocked=${s.operator_unlocked} session=${s.session_id}`;
};
const res = (r) => (r && r.ok === false ? `FAIL ${r.error.code}/${r.error.details?.shell_code ?? "-"}: ${r.error.message}` : r && r.ok ? "ok" : JSON.stringify(r));
const step = async (label, ref, p) => {
  const r = await p;
  console.log(`${label.padEnd(58)} -> ${res(r)}   [A07 ${ref}]`);
  return r;
};

const health = await until(async () => ((await call("health")).ok ? true : null), 60_000);
console.log(`backend READY via stub: ${!!health}`);

console.log("\n=== Scenario 1: A07 student flow, teacher opens console DURING the running exam ===");
const body = {
  source: { mode: "synthetic", camera_index: 0, replay_id: null, width: 640, height: 480, fps: 30 },
  exam_id: "demo-exam-1", student_label: null,
  consent: { accepted: true, text_version: "consent-ru-1", accepted_at: new Date().toISOString() },
  retain_media: false,
};
const c = await step("createSession(body)", "Preflight.tsx:77", call("createSession", body));
const sid = c.data.session_id;
await step("runPreflight(sid)", "Preflight.tsx:55", call("runPreflight", sid));
await step("operatorUnlock(PIN)  (Skip needs teacher)", "App.tsx:299 / Preflight.tsx:278", call("operatorUnlock", PIN));
await step("calibrationSkip(sid,{reason})", "Preflight.tsx:116", call("calibrationSkip", sid, { reason: "teacher decided" }));
await step("startExam(sid)", "Calibration.tsx:133", call("startExam", sid));
console.log(`  shell: ${await sh()}`);
await step("getExam(sid)", "Exam.tsx:43", call("getExam", sid));
await step("saveAnswer(sid,'q1',{value:['b'],client_seq})", "Exam.tsx:75", call("saveAnswer", sid, "q1", { value: ["b"], client_seq: Date.now() }));
// produce an environment incident through the shell's own guard path (Ctrl+V in exam window -> env event -> bootstrap engine)
const kev = { defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } };
win.webContents.emit("before-input-event", kev, { type: "keyDown", key: "v", code: "KeyV", control: true, alt: false, shift: false, meta: false });
const incMsg = await until(() =>
  state.sent.find((s) => s.channel === "qorgau:push:stream-event" && s.args[0].message.type === "incident" && s.args[0].message.change.incident.session_id === sid),
);
const iid = incMsg?.args[0].message.change.incident.incident_id;
console.log(`  stream pushed incident to renderer: ${iid ?? "none"} (${incMsg?.args[0].message.change.incident.rule_id ?? ""})`);
await step("teacher: operatorUnlock(PIN) during exam", "App.tsx:299", call("operatorUnlock", PIN));
console.log(`  shell: ${await sh()}`);
await step("OperatorScreen(live) load: listIncidents(sid)", "Operator.tsx:35", call("listIncidents", sid));
await step("IncidentCard: getIncident(sid,iid)", "IncidentCard.tsx:41", call("getIncident", sid, iid));
await step("IncidentCard: addReview(sid,iid,body)", "IncidentCard.tsx:92", call("addReview", sid, iid, { decision: "dismissed", comment: "", operator: "T1" }));
await step("IncidentCard: getEvidence(sid,'ev-x')", "IncidentCard.tsx:64", call("getEvidence", sid, "ev-x"));
await step("App.resync on seq gap: listIncidents(sid)", "App.tsx:63", call("listIncidents", sid));
await step("OperatorScreen: pauseExam(sid,{reason})", "Operator.tsx:82", call("pauseExam", sid, { reason: "operator_pause" }));
console.log(`  shell: ${await sh()}`);
await step("same listIncidents(sid) while PAUSED", "Operator.tsx:35", call("listIncidents", sid));
await step("same getIncident(sid,iid) while PAUSED", "IncidentCard.tsx:41", call("getIncident", sid, iid));
await step("OperatorScreen: resumeExam(sid)", "Operator.tsx:84", call("resumeExam", sid));
console.log(`  shell: ${await sh()}`);
await step("OperatorScreen: finishExam(sid)", "Operator.tsx:86", call("finishExam", sid));
console.log(`  shell: ${await sh()}`);
await step("review: listIncidents(sid) after finish", "Operator.tsx:35", call("listIncidents", sid));
await step("review: getSummary(sid)", "Operator.tsx:40", call("getSummary", sid));
await step("review: addReview(sid,iid,body)", "IncidentCard.tsx:92", call("addReview", sid, iid, { decision: "dismissed", comment: "ok", operator: "T1" }));

console.log("\n=== Scenario 2: Summary 'Новая сессия' (A07 newSession: no operatorLock) -> next student ===");
console.log(`  shell before next student: ${await sh()}`);
const c2 = await step("next student: createSession(body)", "Preflight.tsx:77", call("createSession", { ...body, consent: { ...body.consent, accepted_at: new Date().toISOString() } }));
const sid2 = c2.data.session_id;
console.log(`  shell after createSession: ${await sh()}`);
await step("runPreflight(sid2)", "Preflight.tsx:55", call("runPreflight", sid2));
console.log("  A07 role stays 'teacher' (wantTeacher=true && operator_unlocked=true) -> Skip opens without PIN (Preflight.tsx:278)");
const sk = await step("calibrationSkip(sid2,{reason}) with NO new PIN", "Preflight.tsx:116", call("calibrationSkip", sid2, { reason: "student skip" }));
console.log(`  backend calibration.message_code = ${sk.ok ? sk.data.message_code : "-"}`);
await step("exportReport(sid_prev,'json') still allowed for next student", "Summary.tsx:53", call("exportReport", sid, "json"));
await step("abortExam(sid2) cleanup", "Preflight.tsx:130", call("abortExam", sid2, { reason: "cleanup" }));

console.log("\n=== Scenario 3: calibrationSkip is not operator-gated in main at all ===");
await call("operatorLock");
const c3 = await call("createSession", { ...body, consent: { ...body.consent, accepted_at: new Date().toISOString() } });
const sid3 = c3.data.session_id;
await call("runPreflight", sid3);
console.log(`  shell: ${await sh()}`);
await step("calibrationSkip(sid3) with operator LOCKED", "Calibration.tsx:119", call("calibrationSkip", sid3, { reason: "no pin" }));
await step("pauseExam is gated for comparison (not running)", "-", call("pauseExam", sid3, { reason: "x" }));

console.log("\n=== Scenario 4: student locks the teacher out via the always-visible PIN dialog during the exam ===");
await step("startExam(sid3)", "Calibration.tsx:133", call("startExam", sid3));
for (let i = 1; i <= 5; i++) await step(`student: operatorUnlock('0000') #${i}`, "App.tsx:299", call("operatorUnlock", "0000"));
await step("teacher: operatorUnlock(correct PIN)", "App.tsx:299", call("operatorUnlock", PIN));
await step("teacher: pauseExam(sid3) impossible (locked)", "Operator.tsx:82", call("pauseExam", sid3, { reason: "x" }));
await step("student: requestEmergencyExit(reason)", "Exam.tsx:133", call("emergencyExit", "student_emergency_exit"));
const g = await step("Exam.tsx emergency: getSession(sid3)", "Exam.tsx:140", call("getSession", sid3));
console.log(`  session state after emergency exit: ${g.ok ? g.data.state : "-"}; shell: ${await sh()}`);

state.appEmit("before-quit", { defaultPrevented: false, preventDefault() {} });
await until(() => state.exited !== null, 20_000);
console.log("\nshell stopped; backend stopped");
process.exit(0);
