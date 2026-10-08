// Actual production Electron main/preload/React + actual backend/C2 + actual C1 + teacher Chrome UI.
// Sessions stay CREATED/SYNTHETIC: no preflight, running exam, native guard, camera or microphone.
// Build desktop first. QORGAU_PYTHON, PLAYWRIGHT_MODULE and external output directory are required.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, resolve, join, delimiter } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { chromium, _electron } = require(process.env.PLAYWRIGHT_MODULE ?? "playwright");
const here = dirname(fileURLToPath(import.meta.url));
const proctoring = resolve(here, "../../..");
const desktop = join(proctoring, "desktop");
assert.ok(process.argv[2], "An output directory outside the checkout is required");
const out = resolve(process.argv[2]);
assert.ok(!out.startsWith(resolve(proctoring, "..")), "Store ephemeral student data outside the checkout");
mkdirSync(out, { recursive: true });
const work = mkdtempSync(join(out, "runtime-"));
const python = process.env.QORGAU_PYTHON;
assert.ok(python, "QORGAU_PYTHON must select an existing runtime");
const env = Object.fromEntries(Object.entries(process.env).filter(([k]) => !k.startsWith("QORGAU_") && !["ELECTRON_RUN_AS_NODE", "NODE_OPTIONS"].includes(k)));
Object.assign(env, { PYTHONPATH: [proctoring, join(proctoring, "backend"), join(proctoring, "contracts/python")].join(delimiter), PYTHONIOENCODING: "utf-8", PYTHONUNBUFFERED: "1" });
const service = spawn(python, [join(here, "server.py"), work], { cwd: proctoring, env, windowsHide: true, stdio: ["pipe", "pipe", "pipe"] });
const serviceExit = new Promise(resolveExit => service.on("exit", resolveExit));
service.stderr.on("data", () => {}); // no raw logs or credentials saved
const ready = new Promise((resolveReady, reject) => {
  let buffer = "";
  const timer = setTimeout(() => reject(new Error("C1 + backend B did not become ready")), 45000);
  service.stdout.on("data", chunk => {
    buffer += String(chunk);
    if (buffer.includes("\n")) { clearTimeout(timer); resolveReady(JSON.parse(buffer.split("\n")[0])); }
  });
  service.once("error", reject);
});
const results = [];
const check = (name, value, details = {}) => { assert.ok(value, name); results.push({ name, pass: true, ...details }); console.log(`PASS ${name}`); };
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function until(fn, label, timeout = 15000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) { const value = await fn(); if (value) return value; await sleep(100); }
  throw new Error(`Timed out: ${label}`);
}
let electron, chrome, student, teacher, context, connection;
const errors = [];
let diagnostics = null;
const report = () => writeFileSync(join(out, "results.json"), JSON.stringify({ source: "actual production Electron + proctor/C2 + C1; CREATED synthetic sessions", nativeGuard: false, camera: false, microphone: false, results, diagnostics }, null, 2));
// Passive production event/DOM observation only; never call the receipt bridge.
function observeStudent() {
  window.lockObservations = [];
  window.qorgau.subscribeEvents(({message:m}) => {
    if(m.type === 'class_state' && window.lockObservations.length < 80) window.lockObservations.push({state:m.lock_state, requested:m.lock_requested, locked:m.locked, error:m.lock_error_ru, recovery:m.lock_request?.recovery});
  });
  new MutationObserver(() => {
    const app = document.querySelector('[data-adal-app]'), o = document.querySelector('[data-adal-lock]');
    if(!o || window.lockObservations.length >= 80) return;
    const b = o.getBoundingClientRect(), css = getComputedStyle(o);
    window.lockObservations.push({ visible: document.visibilityState, inert: app?.inert, hidden: app?.getAttribute('aria-hidden'), reason:o.querySelector('[data-lock-reason]')?.textContent,
      box:{left:b.left, top:b.top, right:b.right, bottom:b.bottom}, viewport:{w:innerWidth,h:innerHeight,clientWidth:document.documentElement.clientWidth,clientHeight:document.documentElement.clientHeight},
      css:{visibility:css.visibility,display:css.display,opacity:css.opacity},hit:o.contains(document.elementFromPoint(innerWidth/2,innerHeight/2)) });
  }).observe(document,{childList:true,subtree:true,attributes:true});
}
async function launchStudent() {
  electron = await _electron.launch({ executablePath: require(join(desktop, "node_modules/electron")), args: [desktop, `--user-data-dir=${join(work, "electron-profile")}`], cwd: desktop, timeout: 45000,
    env: { ...env, QORGAU_PYTHON: python, QORGAU_PROCTORING_ROOT: proctoring,
      QORGAU_DATA_DIR: join(work, "student-a"), QORGAU_MODELS_DIR: join(work, "no-models"), QORGAU_REPLAY_DIR: join(work, "no-replays"),
      QORGAU_CLASS_SERVER: `127.0.0.1:${connection.port}`, QORGAU_CLASS_CODE: connection.join_code, QORGAU_CLASS_LABEL: "Electron A — SYNTHETIC",
      QORGAU_SHELL_NATIVE_ENFORCE: "0", QORGAU_SHELL_SELFTEST: "0", QORGAU_SHELL_NATIVE_HELPER: join(work, "absent-native-helper.exe"),
      QORGAU_SHELL_LOG_LEVEL: "error", QORGAU_LOG_LEVEL: "WARNING" } });
  student = await electron.firstWindow();
  student.setDefaultTimeout(12000);
  student.on("pageerror", e => errors.push(e.message));
  await student.waitForFunction(async () => (await window.qorgau.getShellState()).backend === "ready", null, { timeout: 25000 });
  await student.locator('[data-testid="class-connection"]').getByText("подключено", { exact: true }).waitFor({ timeout: 15000 });
  await student.evaluate(observeStudent);
  await student.addInitScript(observeStudent);
}
async function openStudent(id) {
  await teacher.goto(connection.base);
  await teacher.locator(`.card-hit[data-id="${id}"]`).click();
  await teacher.locator('[data-testid="adal-class"]').getByText("Класс: Синтетическая проверка цепочки", { exact: true }).waitFor();
}
async function lock(reason) {
  await teacher.locator('[data-action="lock"]').click();
  await teacher.locator('[data-testid="adal-reason"]').fill(reason);
  await teacher.locator('[data-testid="adal-lock-send"]').click();
}
const getCard = async id => (await context.request.get(`${connection.base}/api/teacher/students/${id}`)).json();
const commands = async id => (await context.request.get(`${connection.base}/api/teacher/students/${id}/commands`)).json();
try {
  connection = await ready;
  chrome = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL ?? "chrome" });
  context = await chrome.newContext({ viewport: { width: 1366, height: 900 }, locale: "ru-RU" });
  assert.equal((await context.request.post(`${connection.base}/api/teacher/login`, { data: { pin: connection.pin } })).status(), 200);
  teacher = await context.newPage();
  teacher.setDefaultTimeout(12000);
  teacher.on("pageerror", e => errors.push(e.message));
  await launchStudent();
  const created = await student.evaluate(() => window.qorgau.createSession({ source: { mode: "synthetic", camera_index: 0, replay_id: null, width: 640, height: 480, fps: 10 }, exam_id: "demo-exam-1", student_label: "Electron A", retain_media: false,
    consent: { accepted: true, text_version: "consent-ru-1", accepted_at: new Date().toISOString() } }));
  check("production IPC creates synthetic session without starting exam", created.ok && created.data.state === "created" && created.data.source_mode === "synthetic");
  const cards = await until(async () => {
    const list = await context.request.get(`${connection.base}/api/teacher/students`).then(r => r.json());
    return list.length === 2 && list.every(c => c.origin === "simulated" && !c.stale) ? list : null;
  }, "two actual synthetic student backends");
  const sidA = cards.find(c => c.student_label.startsWith("Electron A")).student_id;
  const sidB = cards.find(c => c.student_label.startsWith("Backend B")).student_id;
  check("two independent real C2 identities and synthetic provenance", sidA !== sidB);
  check("legacy initial false is not a confirmed unlock", !(await getCard(sidA)).lock_confirmed);
  await openStudent(sidA);
  await teacher.evaluate(() => {
    window.controlStates = [];
    const el = document.querySelector('[data-testid="adal-command"]');
    new MutationObserver(() => window.controlStates.push(el?.getAttribute("data-state"))).observe(el, { attributes: true, attributeFilter: ["data-state"] });
  });
  const reason = "Проверка преподавателя: уберите учебник со стола";
  await lock(reason);
  await student.locator('[data-adal-lock]').waitFor();
  await until(async () => (await getCard(sidA)).lock_confirmed, "actual painted UI lock receipt");
  const actual = (await commands(sidA)).at(-1);
  check("actual C1 → C2 → production Electron → UI receipt ACK", actual.status === "succeeded" && actual.ack.result.lock_state === "applied" && actual.ack.result.lock_scope === "app_overlay");
  check("pending appears before application confirmation", await teacher.evaluate(() => window.controlStates.includes("pending")));
  check("custom reason shown and underlying application inert", await student.evaluate(expected => document.querySelector('[data-lock-reason]')?.textContent === expected && document.querySelector('[data-adal-app]')?.inert === true, reason));
  check("targeted lock leaves other actual backend untouched", (await commands(sidB)).length === 0 && !(await getCard(sidB)).locked);
  await student.locator('[data-adal-lock]').screenshot({ path: join(out, "actual-lock.png") });
  await teacher.locator('[data-action="unlock"]').click();
  await until(async () => { const c = await getCard(sidA); return c.lock_confirmed && c.locked === false; }, "actual unlock receipt");
  check("actual unlock removes overlay and restores interaction", await student.evaluate(() => !document.querySelector('[data-adal-lock]') && !document.querySelector('[data-adal-app]').inert));
  await openStudent(sidB);
  await lock("Без интерфейса нельзя подтвердить закрытие экрана");
  await until(async () => (await commands(sidB)).at(-1)?.status === "failed", "real backend B times out without renderer");
  check("backend without renderer fails truthfully without fabricated ACK", !(await commands(sidB)).at(-1).ack.ok && !(await getCard(sidB)).lock_confirmed);
  check("failed command to B cannot lock A", await student.locator('[data-adal-lock]').count() === 0);
  await openStudent(sidA);
  await electron.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].hide());
  await lock("Окно скрыто: подтверждения быть не должно");
  await until(async () => (await commands(sidA)).at(-1)?.status === "failed", "hidden production window receipt refusal");
  check("hidden Electron window cannot confirm lock", !(await commands(sidA)).at(-1).ack.ok);
  await electron.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].show());
  await lock("Проверка после возвращения окна");
  await until(async () => { const c = await getCard(sidA); return c.lock_confirmed && c.locked && c.lock_state === "applied"; }, "new request after visible window");
  check("new visible request receives genuine renderer confirmation", (await commands(sidA)).at(-1).status === "succeeded");
  await student.reload();
  await student.locator('[data-adal-lock]').waitFor();
  await until(async () => { const c = await getCard(sidA); return c.lock_confirmed && c.lock_state === "applied"; }, "lock recovery after renderer reload");
  check("renderer reload restores lock through a new painted receipt", await student.locator('[data-adal-app]').getAttribute('aria-hidden') === "true" && await student.evaluate(() => window.lockObservations.some(o => o.recovery === true && o.state === "requested")));
  await electron.close(); electron = null;
  await until(async () => !(await getCard(sidA)).connected, "student A disconnect");
  check("student shutdown invalidates current lock confirmation", !(await getCard(sidA)).lock_confirmed);
  await launchStudent();
  await student.locator('[data-adal-lock]').waitFor({ timeout: 15000 });
  await until(async () => { const c = await getCard(sidA); return c.connected && c.lock_confirmed && c.locked; }, "persisted C2 lock after full Electron/backend restart");
  check("full Electron/backend restart resumes identity and confirms restored overlay", (await context.request.get(`${connection.base}/api/teacher/students`).then(r => r.json())).length === 2);
  await teacher.locator('[data-action="unlock"]').click();
  await until(async () => { const c = await getCard(sidA); return c.lock_confirmed && !c.locked; }, "unlock after restart");
  service.stdin.write("stop-b\n");
  await until(async () => !(await getCard(sidB)).connected, "second actual backend shutdown");
  check("second client disconnect stays isolated", (await getCard(sidA)).connected && !(await getCard(sidB)).lock_confirmed);
  const shell = await student.evaluate(() => window.qorgau.getShellState());
  check("no running exam, native guard, fullscreen or kiosk", !shell.exam_mode_active && await electron.evaluate(({ BrowserWindow }) => !BrowserWindow.getAllWindows()[0].isFullScreen() && !BrowserWindow.getAllWindows()[0].isKiosk()));
  check("production audio panel asset loads without starting audio", await teacher.locator('[data-module]').evaluateAll(els => els.some(el => el.getAttribute('data-module')?.includes('audio'))));
  check("no renderer page errors", errors.length === 0);
  report();
} catch (error) {
  diagnostics = {};
  if (student && !student.isClosed()) diagnostics.observations = await student.evaluate(() => window.lockObservations).catch(() => null);
  if (context && connection) diagnostics.cards = await context.request.get(`${connection.base}/api/teacher/students`).then(r => r.json()).then(cards => cards.map(c => ({ label: c.student_label, connected: c.connected, stale: c.stale, locked: c.locked, lock_state: c.lock_state, lock_confirmed: c.lock_confirmed }))).catch(() => null);
  if (teacher) diagnostics.controls = await teacher.locator('[data-testid="adal-controls"]').textContent({timeout: 1000}).catch(() => null);
  results.push({ name: "failure", pass: false, reason: String(error.message).replace(/\b\d{6}\b/g, "[redacted]") });
  report();
  throw error;
} finally {
  if (electron) await electron.close();
  if (chrome) await chrome.close();
  service.stdin.end("stop\n");
  const stopped = await Promise.race([serviceExit.then(() => true), sleep(16000).then(() => false)]);
  if (!stopped) service.kill();
}
