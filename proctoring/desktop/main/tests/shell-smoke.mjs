// Wiring smoke of the BUILT dist/main/main.cjs under the Electron API stub + the REAL backend
// (owner: A06). Run after `npm run build:electron`:   node main/tests/shell-smoke.mjs
// Proves: main.cjs boots, registers only the fixed IPC channels, rejects untrusted senders, serves
// qorgau://app with CSP (no traversal), spawns the backend (READY), drives a synthetic exam through
// IPC, engages/releases the guard on the stub window, and that EVERY release path leaves no
// restriction behind: finish, emergency hotkey, renderer crash (+ re-engage after recovery),
// renderer unresponsive (+ re-engage), backend SIGKILL, uncaught exception in main, quit during the
// exam. Does NOT prove anything about real Electron rendering, focus or OS key handling.
import { randomBytes, scryptSync } from "node:crypto";
import { readdirSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const here = dirname(fileURLToPath(import.meta.url));
const desktop = resolve(here, "..", "..");
process.env.QORGAU_SHELL_LOG_LEVEL ??= "warn";
process.env.QORGAU_LOG_LEVEL ??= "WARNING";
process.env.QORGAU_SHELL_DEMO_OPERATOR = "0";
const PIN = "2468";
const salt = randomBytes(16);
process.env.QORGAU_OPERATOR_PIN_HASH = `scrypt:${salt.toString("hex")}:${scryptSync(PIN, salt, 32, { N: 16384, r: 8, p: 1 }).toString("hex")}`;

const stub = require("./electron-stub.cjs");
const { state, sessions } = stub;
const rows = [];
const check = (name, ok, detail = "") => {
  rows.push({ name, ok: !!ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? `  (${detail})` : ""}`);
};
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

require(resolve(desktop, "dist", "main", "main.cjs"));
check("privileged scheme registered before ready", state.calls.includes("privileged qorgau"));
check("sandbox enabled for all renderers", state.calls.includes("enableSandbox"));
stub.start();
await until(() => state.windows.some((w) => !w.destroyed && w.opts.webPreferences.preload));
const win = state.windows.find((w) => !w.destroyed && w.opts.webPreferences.preload);
check("startup self-test ran before the exam window (hidden probe window used)", state.windows.length >= 1);
const wp = win.opts.webPreferences;
check(
  "window webPreferences hardened",
  wp.contextIsolation === true && wp.sandbox === true && wp.nodeIntegration === false && wp.webviewTag === false && wp.webSecurity === true && wp.devTools === false,
);
check("application menu removed", state.calls.includes("menu null"));
check("renderer loaded from qorgau://app", state.calls.some((c) => c === "loadURL qorgau://app/index.html"));

const invokeNames = [...state.handles.keys()];
check("only fixed qorgau:* IPC channels", invokeNames.length === 31 && invokeNames.every((c) => c.startsWith("qorgau:")), `${invokeNames.length} channels`);

const trusted = { sender: win.webContents, senderFrame: win.webContents.mainFrame };
const call = (ch, ...args) => state.handles.get(ch)(trusted, ...args);
const untrusted = await state.handles.get("qorgau:api:health")({ sender: { id: 99 }, senderFrame: { url: "https://evil.example/" } });
check("untrusted IPC sender rejected", untrusted.ok === false && untrusted.error.code === "FORBIDDEN_ORIGIN");
const subframe = await state.handles.get("qorgau:api:health")({ sender: win.webContents, senderFrame: { url: "qorgau://app/x", parent: {} } });
check("IPC from a sub-frame rejected", subframe.ok === false);

const ses = sessions.get("qorgau-exam");
const handler = state.protocolHandlers.get("qorgau");
const page = await handler(new Request("qorgau://app/index.html"));
const csp = page.headers.get("content-security-policy") ?? "";
check("app page served with CSP", page.status === 200 && csp.includes("script-src 'self'") && !csp.includes("unsafe-eval"));
const trav = await handler(new Request("qorgau://app/%2e%2e%2f%2e%2e%2fpackage.json"));
check("protocol traversal -> 404", trav.status === 404);
let perm;
ses.handlers.setPermissionRequestHandler({}, "media", (v) => (perm = v));
check("permission requests denied (camera/mic/etc.)", perm === false && ses.handlers.setPermissionCheckHandler() === false);
let cancel;
ses.handlers.onBeforeRequest({ url: "https://example.com/x.js" }, (r) => (cancel = r.cancel));
check("renderer network requests cancelled", cancel === true);
const dl = { defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } };
ses.emit("will-download", dl, {});
check("downloads cancelled", dl.defaultPrevented);
const open = win.webContents.windowOpenHandler({ url: "https://example.com" });
check("window.open denied", open.action === "deny");
const nav = { defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } };
win.webContents.emit("will-navigate", nav, "https://example.com/");
check("navigation away blocked", nav.defaultPrevented);

const health = await until(async () => {
  const r = await call("qorgau:api:health");
  return r.ok ? r : null;
}, 60_000);
check("backend spawned, READY, health via IPC", !!health, health ? health.data.overall : "no READY");
const capsOk = await until(async () => {
  const r = await call("qorgau:shell:get-capabilities");
  return r.ok ? r : null;
});
check("capability matrix available", !!capsOk && capsOk.data.items.length > 0, capsOk ? `${capsOk.data.items.length} items` : "");

const fixture = {
  source: { mode: "synthetic", camera_index: 0, replay_id: null, width: 640, height: 480, fps: 30 },
  exam_id: "demo-exam-1",
  student_label: "smoke",
  consent: { accepted: true, text_version: "consent-ru-1", accepted_at: "2026-10-08T09:00:00Z" },
  retain_media: false,
};
const created = await call("qorgau:api:create-session", fixture);
check("createSession via IPC", created.ok, created.ok ? created.data.session_id : JSON.stringify(created.error));
const sid = created.data.session_id;
const pf = await call("qorgau:api:preflight", sid);
check("preflight (synthetic)", pf.ok && pf.data.ready);
const skipLocked = await call("qorgau:api:calibration-skip", sid, { reason: "smoke" });
check("calibration skip needs the teacher PIN", !skipLocked.ok && skipLocked.error.details.shell_code === "operator_locked");
const wrong = await call("qorgau:shell:operator-unlock", "0000");
check("wrong PIN rejected", !wrong.ok && wrong.error.code === "UNAUTHORIZED");
const unl = await call("qorgau:shell:operator-unlock", PIN);
check("correct PIN unlocks", unl.ok && unl.data.operator_unlocked);
await call("qorgau:api:calibration-skip", sid, { reason: "smoke" });
check("no restriction before start", win.flags.Kiosk !== true && state.shortcuts.size === 0);
const st = await call("qorgau:api:start", sid);
const shell1 = await call("qorgau:shell:get-state");
check("start -> exam mode engaged", st.ok && shell1.mode === "exam" && shell1.exam_mode_active, shell1.mode);
check("exam start clears the teacher unlock", shell1.operator_unlocked === false);
check("window kiosk/top/content-protection engaged", win.flags.Kiosk === true && win.flags.AlwaysOnTop === "screen-saver" && win.flags.ContentProtection === true);
check("emergency hotkey registered", state.shortcuts.has("CommandOrControl+Alt+Shift+F12"));
const keyEv = { defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } };
win.webContents.emit("before-input-event", keyEv, { type: "keyDown", key: "v", code: "KeyV", control: true, alt: false, shift: false, meta: false });
check("Ctrl+V prevented in exam window", keyEv.defaultPrevented);
check("window close prevented in exam", win.close() === false);
check("shell state pushed to renderer", state.sent.some((s) => s.channel === "qorgau:push:shell-state" && s.args[0].mode === "exam"));
const reviewDuring = await call("qorgau:api:list-incidents", sid);
check("default policy: review closed during the exam", !reviewDuring.ok && reviewDuring.error.details.shell_code === "exam_mode_active");

const fin = await call("qorgau:api:finish", sid);
const shell2 = await call("qorgau:shell:get-state");
check("finish -> released, normal", fin.ok && shell2.mode === "normal" && !shell2.exam_mode_active && win.flags.Kiosk === false && state.shortcuts.size === 0);
const reviewAfter = await call("qorgau:api:list-incidents", sid);
check("review open after finish", reviewAfter.ok);

/** Restriction footprint on the stub: nothing may remain after any release path. */
const clean = () => win.flags.Kiosk === false && win.flags.AlwaysOnTop === false && win.flags.Closable === true && state.shortcuts.size === 0;

async function startExam(label) {
  const c = await call("qorgau:api:create-session", { ...fixture, student_label: label });
  if (!c.ok) throw new Error(`create ${label}: ${JSON.stringify(c.error)}`);
  const id = c.data.session_id;
  await call("qorgau:api:preflight", id);
  await call("qorgau:shell:operator-unlock", PIN);
  await call("qorgau:api:calibration-skip", id, { reason: "smoke" });
  const r = await call("qorgau:api:start", id);
  if (!r.ok || win.flags.Kiosk !== true) throw new Error(`start ${label} did not engage`);
  return id;
}
const shellState = () => call("qorgau:shell:get-state");

// emergency hotkey
const sid2 = await startExam("emergency");
state.shortcuts.get("CommandOrControl+Alt+Shift+F12")?.();
const released = await until(async () => {
  const s = await shellState();
  const info = await call("qorgau:api:get-session", sid2);
  return !s.exam_mode_active && info.ok && info.data.state === "aborted" ? info : null;
});
check("emergency hotkey -> released + session aborted", !!released && clean());
await call("qorgau:api:get-session", sid2);
check("emergency latch: aborted session never re-engages", clean());

// renderer crash during the exam -> release, reload attempt, re-engage when the UI is back
const sid3 = await startExam("renderer-crash");
const loadsBefore = state.calls.filter((c) => c.startsWith("loadURL")).length;
win.webContents.emit("render-process-gone", {}, { reason: "crashed", exitCode: 1 });
const crashRel = await until(async () => {
  const s = await shellState();
  return !s.exam_mode_active && s.mode === "error" ? s : null;
}, 5_000);
check("renderer crash -> restrictions released, mode=error", !!crashRel && clean());
const reloaded = await until(() => state.calls.filter((c) => c.startsWith("loadURL")).length > loadsBefore, 5_000);
check("renderer crash -> window reloaded", !!reloaded);
await call("qorgau:api:get-session", sid3); // the recovered renderer reads its session
const reeng = await shellState();
check("recovered renderer + RUNNING session -> exam mode re-engaged", reeng.exam_mode_active && win.flags.Kiosk === true);
await call("qorgau:api:finish", sid3);
check("finish after recovery -> clean", clean());

// renderer unresponsive for 5 s -> release; responsive again -> re-engage
const sid4 = await startExam("unresponsive");
win.emit("unresponsive");
const hangRel = await until(async () => {
  const s = await shellState();
  return !s.exam_mode_active ? s : null;
}, 9_000);
check("renderer unresponsive 5 s -> restrictions released", !!hangRel && clean(), hangRel?.mode);
win.emit("responsive");
const back = await until(async () => ((await shellState()).exam_mode_active ? true : null), 5_000);
check("renderer responsive again -> exam mode re-engaged", !!back && win.flags.Kiosk === true);
await call("qorgau:api:finish", sid4);

// uncaught exception in main -> synchronous release
const sid5 = await startExam("main-exception");
process.emit("uncaughtException", new Error("smoke: simulated main failure"));
check("uncaught exception in main -> released synchronously", clean());
await until(async () => ((await shellState()).mode === "error" ? true : null), 3_000);
await call("qorgau:api:get-session", sid5); // renderer polls its RUNNING session
check("after a main exception the session is latched (no engage/crash loop)", clean() && !(await shellState()).exam_mode_active);
await call("qorgau:api:finish", sid5);

// backend SIGKILL during the exam -> release, restart with a new connection
function backendPid() {
  for (const d of readdirSync("/proc")) {
    if (!/^\d+$/.test(d)) continue;
    try {
      const stat = readFileSync(`/proc/${d}/stat`, "utf8");
      const ppid = Number(stat.slice(stat.lastIndexOf(")") + 2).split(" ")[1]);
      if (ppid !== process.pid) continue;
      if (readFileSync(`/proc/${d}/cmdline`, "utf8").includes("proctor")) return Number(d);
    } catch {
      /* raced */
    }
  }
  return null;
}
const sid6 = await startExam("backend-crash");
const bpid = process.platform === "linux" ? backendPid() : null;
if (bpid) {
  process.kill(bpid, "SIGKILL");
  const lost = await until(async () => {
    const s = await shellState();
    return !s.exam_mode_active && s.mode === "error" ? s : null;
  }, 10_000);
  check("backend SIGKILL during exam -> released, mode=error", !!lost && clean(), `pid ${bpid}`);
  const healthy = await until(async () => ((await call("qorgau:api:health")).ok ? true : null), 60_000);
  check("backend restarted (new launch answers)", !!healthy && backendPid() !== bpid);
  const lostSession = await call("qorgau:api:get-session", sid6);
  check("in-memory session of the dead backend is not resurrected", !lostSession.ok && clean());
} else {
  check("backend SIGKILL during exam (needs Linux /proc)", false, "NOT RUN: backend pid not found");
}

// quit while an exam is running -> release + backend stopped
await startExam("quit-during-exam");
state.appEmit("before-quit", { defaultPrevented: false, preventDefault() {} });
// main handles before-quit asynchronously, then calls app.quit() again
const quitDone = await until(() => state.quitRequested > 0 && state.exited !== null, 20_000);
const after = await call("qorgau:api:health");
check("quit during exam: released, backend stopped, clean exit", !!quitDone && after.ok === false && clean(), `exit ${state.exited}`);

const failed = rows.filter((r) => !r.ok);
console.log(`\nshell-smoke (Electron API STUB, real backend): ${rows.length - failed.length}/${rows.length} PASS`);
process.exit(failed.length ? 1 : 0);
