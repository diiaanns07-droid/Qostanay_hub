// A07 end-to-end through the REAL bridge logic (A06 ipc/api.ts in Node, see shell-node.ts) and the REAL backend
// process (python -m proctor serve, A01 + whatever modules the checkout integrates, e.g. A08 storage).
// The renderer is the PRODUCT build (no FixtureBridge), served with the shell's CSP header.
// What is NOT covered: Electron itself (IPC transport, window, kiosk/keyboard guard), Windows, a camera.
//
// Usage (from proctoring/desktop of an integration checkout with main/src + proctoring/.venv):
//   npm run build:renderer && node renderer/tests/real-bridge/run-real.mjs <outDir>
// Env: QORGAU_PYTHON (default ../.venv/bin/python), PLAYWRIGHT_MODULE (path to a playwright install).
import { createServer } from "node:http";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, statSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, extname, join, resolve } from "node:path";
import { randomBytes } from "node:crypto";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const desktop = resolve(here, "..", "..", "..");
const proctoring = resolve(desktop, "..");
const require = createRequire(import.meta.url);
const { build } = require(join(desktop, "node_modules", "esbuild"));
const { chromium } = require(process.env.PLAYWRIGHT_MODULE ?? "playwright");

const OUT = resolve(process.argv[2] ?? "real-bridge-out");
const SHOTS = join(OUT, "screens");
const EXPORTS = join(OUT, "exports");
mkdirSync(SHOTS, { recursive: true });
const python = process.env.QORGAU_PYTHON ?? join(proctoring, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
if (!existsSync(join(desktop, "main", "src", "ipc", "api.ts"))) {
  console.error("NOT_RUN: this checkout has no A06 main/src (needs the A01 integration candidate)");
  process.exit(2);
}
if (!existsSync(join(desktop, "dist", "renderer", "index.html"))) {
  console.error("run `npm run build:renderer` first");
  process.exit(2);
}

const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? ` — ${detail}` : ""}`);
};

// ---------------------------------------------------------------- shell logic (A06) in Node
const bundle = join(desktop, "dist", "test-a07", "shell-node.cjs");
await build({
  entryPoints: [join(here, "shell-node.ts")],
  outfile: bundle,
  bundle: true,
  platform: "node",
  format: "cjs",
  target: "node22",
  external: ["electron", "bufferutil", "utf-8-validate"],
  tsconfig: join(desktop, "tsconfig.json"),
  logLevel: "warning",
});
const harness = require(bundle);
const PIN = randomBytes(6).toString("hex"); // per run, never printed into the UI
const dataDir = mkdtempSync(join(tmpdir(), "qorgau-a07-real-"));
const shell = await harness.startShell({
  python,
  proctoringRoot: proctoring,
  env: { ...process.env, QORGAU_DATA_DIR: dataDir, QORGAU_LOG_LEVEL: "WARNING", QORGAU_SHELL_LOG_LEVEL: "error" },
  operatorPinHash: harness.hashPin(PIN, randomBytes(16)),
  exportDir: EXPORTS,
});
check("real backend READY through A06 BackendSupervisor", true, `pid ${shell.backendPid()}`);

// ---------------------------------------------------------------- static server with the shell's CSP
const root = join(desktop, "dist", "renderer");
const MIME = { ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css", ".map": "application/json", ".svg": "image/svg+xml" };
const server = createServer((req, res) => {
  const p = new URL(req.url ?? "/", "http://x").pathname;
  const file = resolve(root, "." + (p === "/" ? "/index.html" : p));
  if (!file.startsWith(root) || !existsSync(file) || statSync(file).isDirectory()) {
    res.writeHead(404).end();
    return;
  }
  res.writeHead(200, { "Content-Type": MIME[extname(file)] ?? "application/octet-stream", "Content-Security-Policy": harness.CSP });
  res.end(readFileSync(file));
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const BASE = `http://127.0.0.1:${server.address().port}/`;

// ---------------------------------------------------------------- browser + bridge transport
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
const ctx = await browser.newContext({ viewport: { width: 1366, height: 768 }, locale: "ru-RU", reducedMotion: "reduce" });
const page = await ctx.newPage();
const consoleErrors = [];
const cspViolations = [];
page.on("console", (m) => {
  if (m.type() === "error") consoleErrors.push(m.text());
  if (/Content Security Policy/i.test(m.text())) cspViolations.push(m.text());
});
page.on("pageerror", (e) => consoleErrors.push(String(e)));

const enc = (v) => {
  if (v instanceof Uint8Array) return { __b64: Buffer.from(v).toString("base64") };
  if (Array.isArray(v)) return v.map(enc);
  if (v && typeof v === "object") return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, enc(x)]));
  return v;
};
let previewWanted = false;
const calls = [];
await page.exposeBinding("__qorgauInvoke", async (_src, name, args) => {
  if (name === "__previewWanted") {
    previewWanted = !!args[0];
    return null;
  }
  const fn = shell.api[name];
  if (!fn) return { ok: false, error: { code: "INVALID_ARGUMENT", message: `no method ${name}`, retryable: false, details: {} } };
  const r = await fn(...args);
  calls.push({ name, ok: r && typeof r === "object" && "ok" in r ? r.ok : true, code: r?.error?.code, shell: r?.error?.details?.shell_code });
  return enc(r);
});
let queue = [];
let lastFrame = null;
shell.onShell((s) => queue.push(["shell", s]));
shell.onEnvelope((e) => queue.push(["event", e]));
shell.onPreview((m, jpeg) => {
  if (previewWanted) lastFrame = ["preview", m, Buffer.from(jpeg).toString("base64")];
});
const pump = setInterval(() => {
  const batch = queue;
  queue = [];
  if (lastFrame) batch.push(lastFrame);
  lastFrame = null;
  if (batch.length) page.evaluate((b) => window.__qorgauPush?.(b), batch).catch(() => {});
}, 100);

await page.addInitScript(() => {
  const dec = (v) => {
    if (v && typeof v === "object") {
      if (typeof v.__b64 === "string") {
        const bin = atob(v.__b64);
        const u = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i);
        return u;
      }
      if (Array.isArray(v)) return v.map(dec);
      const o = {};
      for (const k of Object.keys(v)) o[k] = dec(v[k]);
      return o;
    }
    return v;
  };
  const fan = () => {
    const ls = new Set();
    return { ls, sub: (l) => (ls.add(l), () => ls.delete(l)) };
  };
  const shellF = fan();
  const evF = fan();
  const pvF = fan();
  window.__qorgauPush = (batch) => {
    for (const [kind, a, b] of batch) {
      if (kind === "shell") shellF.ls.forEach((l) => l(a));
      else if (kind === "event") evF.ls.forEach((l) => l(a));
      else if (kind === "preview") {
        const u = dec({ __b64: b });
        pvF.ls.forEach((l) => l(a, u));
      }
    }
  };
  window.__gum = 0;
  if (navigator.mediaDevices) navigator.mediaDevices.getUserMedia = async () => { window.__gum += 1; throw new Error("blocked"); };
  const names = ["getShellState", "getEnvironmentCapabilities", "operatorUnlock", "operatorLock", "requestEmergencyExit", "health", "listSessions", "createSession", "getSession", "runPreflight", "calibrationStart", "calibrationTarget", "calibrationState", "calibrationFinish", "calibrationCancel", "calibrationSkip", "startExam", "pauseExam", "resumeExam", "finishExam", "abortExam", "getExam", "saveAnswer", "listAnswers", "listIncidents", "getIncident", "addReview", "getEvidence", "getSummary", "exportReport", "deleteSession"];
  const bridge = { bridgeVersion: "1.0.0", transport: "electron" };
  for (const n of names) bridge[n] = async (...args) => dec(await window.__qorgauInvoke(n, args));
  bridge.onShellState = shellF.sub;
  bridge.subscribeEvents = evF.sub;
  bridge.subscribePreview = (l) => {
    const u = pvF.sub(l);
    void window.__qorgauInvoke("__previewWanted", [true]);
    return () => {
      u();
      void window.__qorgauInvoke("__previewWanted", [pvF.ls.size > 0]);
    };
  };
  Object.defineProperty(window, "qorgau", { value: Object.freeze(bridge) });
});

const shot = (n) => page.screenshot({ path: join(SHOTS, `real-${n}.png`) });
const until = async (fn, ms = 20000) => {
  const end = Date.now() + ms;
  for (;;) {
    const v = await fn();
    if (v) return v;
    if (Date.now() > end) throw new Error("condition not met");
    await new Promise((r) => setTimeout(r, 100));
  }
};
async function unlock(pin) {
  await page.getByRole("button", { name: /^Режим преподавателя(…)?$/ }).first().click();
  await page.getByLabel("PIN").fill(pin);
  await page.getByRole("button", { name: "Открыть" }).click();
}

try {
  await page.goto(BASE);
  await page.getByText("Подготовка к экзамену").waitFor();
  await page.getByText("Версия сервиса").waitFor({ timeout: 20000 });
  check("renderer (product build, CSP of A06) talks to the real backend", (await page.locator(".fixture-strip").count()) === 0);
  await shot("01-preflight");

  // Synthetic session (only mode the backend can serve without CV modules) — labelled SYNTHETIC.
  await page.getByText("синтетический тест").click();
  await page.getByText("Студент ознакомлен").click();
  await page.getByRole("button", { name: "Создать сессию и проверить" }).click();
  await page.getByText(/Обязательные проверки пройдены|Начать нельзя/).waitFor({ timeout: 60000 });
  const ready = await page.getByText("Обязательные проверки пройдены").count();
  check("real preflight report rendered", true, ready ? "ready" : "NOT ready");
  await shot("02-preflight-report");
  await page.getByRole("button", { name: "К калибровке" }).click();
  await page.getByText("Калибровка взгляда").waitFor();
  await page.getByRole("button", { name: "Начать калибровку" }).click();  // A07-student: full-screen intro
  await until(() => page.evaluate(() => {
    const b = [...document.querySelectorAll("button")].find((x) => x.textContent?.includes("Завершить калибровку"));
    return b && !b.disabled;
  }), 60000);
  await shot("03-calibration-collected");
  check("calibration progressed from backend samples (no UI timer)", true);
  await page.getByRole("button", { name: "Завершить калибровку" }).click();
  await page.getByText("Всё готово к началу").waitFor();
  await page.getByRole("button", { name: "Начать экзамен" }).click();
  await page.locator(".question").waitFor({ timeout: 20000 });
  await until(() => shell.shellState().exam_mode_active);
  check("shell state machine engaged exam mode (guard stand-in)", shell.guard.active);

  // Answers through A08.
  const firstOption = page.locator(".option").nth(1);
  await firstOption.click();
  await page.getByText("✓ Сохранено").waitFor({ timeout: 15000 });
  await page.getByRole("button", { name: "Далее →" }).click();
  if (await page.getByLabel("Ваш ответ").count()) {
    await page.getByLabel("Ваш ответ").fill("HTTP");
    await page.getByText("✓ Сохранено").waitFor({ timeout: 15000 });
  }
  const sid = shell.shellState().session_id;
  const answers = await shell.api.listAnswers(sid);
  check("answers stored by the backend", answers.ok && answers.data.length >= 1, answers.ok ? `${answers.data.length} records` : answers.error.code);
  await shot("04-exam");

  const readTimer = async () => {
    const t = (await page.locator(".timer-value").textContent()) ?? "";
    const m = /(\d+):(\d\d)/.exec(t);
    return m ? (Number(m[1]) * 60 + Number(m[2])) * 1000 : NaN;
  };
  const t1 = await readTimer();
  const w1 = Date.now();

  // Wrong PIN → real OperatorAuth; then teacher console during exam mode.
  await unlock("0000");
  await page.getByText("Неверный PIN.").waitFor();
  check("wrong PIN rejected by A06 OperatorAuth with readable reason", true);
  await page.getByRole("button", { name: "Отмена" }).click();
  await unlock(PIN);
  await page.getByText("Наблюдение за сессией").waitFor();
  await page.getByText("Идёт экзамен: показан только поток событий").waitFor();
  await page.locator(".preview-box img").waitFor({ timeout: 20000 });
  check("preview frames via WS /v1/preview", true);
  await page.locator(".inc-item").first().waitFor({ timeout: 45000 });
  check("incidents arrive on the stream", true);
  const blockedBefore = calls.filter((c) => c.shell === "exam_mode_active").length;
  await page.locator(".inc-item").first().click();
  await page.getByText("Решение сейчас записать нельзя").waitFor();
  await page.waitForTimeout(1500);
  const blockedAfter = calls.filter((c) => c.shell === "exam_mode_active").length;
  check("UI made no call the shell rejects in exam mode", blockedAfter === blockedBefore && blockedBefore === 0, `${blockedAfter} rejected`);
  await shot("05-operator-exam-mode");

  // Pause → review stored by A08 → stream "pending" must not roll it back.
  await page.getByRole("button", { name: "Пауза" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Пауза" }).click();
  await page.getByRole("button", { name: "Продолжить" }).waitFor();
  const wp0 = Date.now();
  await until(() => !shell.shellState().exam_mode_active);
  await page.locator(".inc-item").first().click();
  await page.getByRole("radio", { name: "Отклонить" }).click();
  await page.getByLabel("Комментарий").fill("проверка интеграции");
  await page.getByLabel("Проверяющий").fill("A07");
  await page.getByRole("button", { name: /Записать (новое )?решение/ }).click();
  await page.getByText("записано в хранилище").waitFor({ timeout: 15000 });
  const incs = await shell.api.listIncidents(sid);
  const reviewed = incs.ok && incs.data.some((i) => i.review_status === "dismissed");
  check("review persisted in A08 storage", reviewed);
  await page.waitForTimeout(1200);
  check("list shows the stored decision", (await page.locator(".inc-item .rs-dismissed").count()) >= 1);
  await shot("06-review-at-pause");
  await page.getByRole("button", { name: "Продолжить" }).click();
  await until(() => shell.shellState().exam_mode_active);
  const wp1 = Date.now();

  // Resume clears the operator unlock in A06: the UI must return to the student automatically.
  await page.locator(".question").waitFor();
  check("resume automatically returns to student and clears operator access", !shell.shellState().operator_unlocked && (await page.getByRole("button", { name: /Преподаватель ✕/ }).count()) === 0);
  await page.waitForTimeout(1200);
  const t2 = await readTimer();
  const w2 = Date.now();
  const expected = w2 - w1 - (wp1 - wp0);
  const drift = t1 - t2 - expected;
  check("timer excludes the pause (server paused_total_ms), no drift", Number.isFinite(drift) && Math.abs(drift) <= 2500, `drift ${Math.round(drift)} ms, pause ${wp1 - wp0} ms`);
  await page.getByRole("button", { name: "Завершить экзамен" }).last().click();
  await page.getByRole("dialog").getByRole("button", { name: "Завершить" }).click();
  await page.getByText("Экзамен завершён").waitFor({ timeout: 30000 });
  check("finish → released exam mode", !shell.shellState().exam_mode_active && !shell.guard.active);

  // Review + summary + exports written to disk by the shell's saveFile.
  await page.getByRole("button", { name: "Режим преподавателя…" }).click();
  if (await page.getByLabel("PIN").count()) {
    await page.getByLabel("PIN").fill(PIN);
    await page.getByRole("button", { name: "Открыть" }).click();
  }
  await page.getByText("Проверка эпизодов").first().waitFor();
  await page.getByText("Решение преподавателя").waitFor();
  await shot("07-review-after-finish");
  await page.getByRole("button", { name: "К итогу →" }).click();
  await page.getByText("Покрытие наблюдением").waitFor({ timeout: 20000 });
  await shot("08-summary");
  await page.getByRole("button", { name: "Сохранить отчёт HTML" }).click();
  await page.getByText(/Файл записан|не выполнен/).first().waitFor({ timeout: 30000 });
  await page.getByRole("button", { name: "Экспорт JSON" }).click();
  await page.waitForTimeout(1500);
  await shot("09-exported");
  const html = shell.saved.find((p) => p.endsWith(".html"));
  const json = shell.saved.find((p) => p.endsWith(".json"));
  const htmlText = html ? readFileSync(html, "utf8") : "";
  check("HTML report written to disk", !!html && htmlText.length > 500 && !/<script/i.test(htmlText), html ? `${htmlText.length} B` : "missing");
  let manifestOk = false;
  if (json) {
    try {
      const j = JSON.parse(readFileSync(json, "utf8"));
      manifestOk = JSON.stringify(j).includes(sid);
    } catch {
      manifestOk = false;
    }
  }
  check("JSON export written to disk and names the session", manifestOk, json ?? "missing");

  // Restart: a new session reaches preflight on the same backend.
  await page.getByRole("button", { name: "Новая сессия" }).click();
  await page.getByText("синтетический тест").click();
  await page.getByText("Студент ознакомлен").click();
  await page.getByRole("button", { name: "Создать сессию и проверить" }).click();
  await page.getByText(/Обязательные проверки пройдены|Начать нельзя/).waitFor({ timeout: 60000 });
  const sid2 = shell.shellState().session_id;
  check("restart: second session bound", !!sid2 && sid2 !== sid, String(sid2));

  // Backend crash: the UI shows the outage and recovers when the shell has restarted the backend.
  const pid = shell.backendPid();
  process.kill(pid, "SIGKILL");
  await page.locator(".conn-down, .conn-wait").first().waitFor({ timeout: 15000 });
  await shot("10-backend-killed");
  check("backend loss visible", true);
  await page.locator(".conn-ok").waitFor({ timeout: 90000 });
  check("recovered after shell restarted the backend", shell.backendPid() !== pid, `pid ${pid} → ${shell.backendPid()}`);
  await page.getByText(/Сессия недоступна|Сессия прервана перезапуском сервиса/).first().waitFor({ timeout: 20000 });
  check("session lost by the crash is reported, not shown as ready", true);
  await shot("11-after-restart");
  await page.getByRole("button", { name: "Новая сессия" }).first().click();
  await page.getByRole("button", { name: "Создать сессию и проверить" }).waitFor();

  const gum = await page.evaluate(() => window.__gum);
  check("getUserMedia never called", gum === 0, String(gum));
  const rejected = calls.filter((c) => c.shell && c.shell !== "operator_pin_wrong" && c.shell !== "backend_unavailable");
  check("no call rejected by shell gating during the whole run", rejected.length === 0, rejected.map((c) => `${c.name}:${c.shell}`).join(", "));
  check("no CSP violations under the shell CSP", cspViolations.length === 0, cspViolations.slice(0, 2).join(" | "));
  check("no console errors", consoleErrors.length === 0, consoleErrors.slice(0, 3).join(" | "));
} catch (e) {
  await shot("zz-failure").catch(() => {});
  check("real-bridge flow completed", false, String(e).slice(0, 500));
} finally {
  clearInterval(pump);
  await browser.close();
  server.close();
  await shell.stop();
}
const failed = results.filter((r) => !r.ok).length;
console.log(`\n${results.length - failed}/${results.length} checks passed · exports in ${EXPORTS}`);
console.log(`shell calls: ${calls.length}, rejected by shell: ${calls.filter((c) => c.shell).map((c) => `${c.name}:${c.shell}`).join(", ") || "none"}`);
process.exit(failed ? 1 : 0);
