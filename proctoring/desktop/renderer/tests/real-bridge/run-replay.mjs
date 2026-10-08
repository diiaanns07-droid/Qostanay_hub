// A07 end-to-end through the REAL bridge logic (A06 ipc/api.ts in Node, see shell-node.ts) and the REAL backend
// process (python -m proctor serve, A01 + whatever modules the checkout integrates, e.g. A08 storage).
// The renderer is the PRODUCT build (no FixtureBridge), served with the shell's CSP header.
// What is NOT covered: Electron itself (IPC transport, window, kiosk/keyboard guard), Windows, a camera.
//
// Usage (from proctoring/desktop of an integration checkout with main/src + proctoring/.venv):
//   npm run build:renderer && node renderer/tests/real-bridge/run-replay.mjs <outDir>
// Env: QORGAU_PYTHON (default ../.venv/bin/python), PLAYWRIGHT_MODULE (path to a playwright install).
// QORGAU_REPLAY_BACKEND_ROOT optionally selects an unchanged candidate backend.
// QORGAU_REPLAY_UI_DELAY_MS simulates time spent by a human after preflight (default 0).
// Models/media: QORGAU_MODELS_DIR, QORGAU_REPLAY_DIR. Transport setup mirrors run-real.mjs.
import { createServer } from "node:http";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, extname, join, resolve } from "node:path";
import { randomBytes } from "node:crypto";
import { execFileSync } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const desktop = resolve(here, "..", "..", "..");
const proctoring = resolve(desktop, "..");
const backendRoot = process.env.QORGAU_REPLAY_BACKEND_ROOT || proctoring;
const backendRevision = () => execFileSync("git", ["rev-parse", "HEAD"], { cwd: backendRoot, encoding: "utf8" }).trim();
const backendSha = backendRevision();
const backendDirty = execFileSync("git", ["status", "--porcelain", "--", "backend", "contracts"], { cwd: backendRoot, encoding: "utf8" }).trim();
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
  proctoringRoot: backendRoot,
  env: { ...process.env, QORGAU_CLASS_SERVER: "", QORGAU_CLASS_CODE: "", QORGAU_DATA_DIR: dataDir, QORGAU_LOG_LEVEL: "WARNING", QORGAU_SHELL_LOG_LEVEL: "error" },
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


const cases = [];
try {
  await page.goto(BASE);
  await page.getByText("Подготовка к экзамену").waitFor();
  await until(async () => (await shell.api.health()).ok);
  check("product renderer, no FixtureBridge", (await page.locator(".fixture-strip").count()) === 0);
  for (const [replayId, expected] of [["zone_a_green_01", "green"], ["zone_b_yellow_02", "yellow"], ["zone_c_red_01", "red"]]) {
    console.log(`START ${replayId}`);
    if (!shell.shellState().operator_unlocked) await unlock(PIN);
    await until(() => shell.shellState().operator_unlocked);
    await page.locator(".preflight-settings > summary").click();
    await page.getByText("запись (replay)", { exact: true }).click();
    await page.getByLabel("Идентификатор записи").fill(replayId);
    await page.getByText("Студент ознакомлен").click();
    check(`${replayId}: preflight marked REPLAY`, await page.getByText("REPLAY · записанное видео", { exact: true }).isVisible());
    const preflightAt = Date.now();
    await page.getByRole("button", { name: "Проверить устройства", exact: true }).click();
    await page.getByText("Обязательные проверки пройдены", { exact: true }).waitFor({ timeout: 90000 });
    const uiDelayMs = Number(process.env.QORGAU_REPLAY_UI_DELAY_MS || 0);
    if (uiDelayMs > 0) await page.waitForTimeout(uiDelayMs);
    await page.getByRole("button", { name: "Без калибровки…", exact: true }).click();
    await page.getByLabel("Причина", { exact: true }).fill("REPLAY: записанное видео; пропуск калибровки преподавателем для демонстрации.");
    await page.getByRole("button", { name: "Пропустить калибровку", exact: true }).click();
    await page.getByRole("button", { name: "Начать экзамен", exact: true }).click();
    await page.locator(".question").waitFor({ timeout: 20000 });
    const startDelayMs = Date.now() - preflightAt;
    const sid = shell.shellState().session_id;
    check(`${replayId}: exam opened through UI`, !!sid, `preflight-to-exam ${startDelayMs} ms`);
    await page.locator(".option").first().click();
    await page.getByText("✓ Сохранено", { exact: true }).waitFor();
    await until(async () => {
      const h = await shell.api.health();
      return h.ok && h.data.components.some(c => c.component === "capture" && c.code === "replay_ended");
    }, 150000);
    await page.getByText("REPLAY — запись закончилась", { exact: true }).waitFor({ timeout: 10000 });
    check(`${replayId}: recording end is visible`, true);
    await page.getByRole("button", { name: "Завершить экзамен", exact: true }).last().click();
    await page.getByRole("dialog").getByRole("button", { name: "Завершить", exact: true }).click();
    await page.getByText("Экзамен завершён", { exact: true }).waitFor();
    await unlock(PIN);
    await page.getByRole("button", { name: "К итогу →", exact: true }).click();
    await page.getByTestId("review-zone").waitFor();
    const r = await shell.api.getSummary(sid);
    if (!r.ok) throw new Error(`getSummary: ${r.error.code}`);
    const actual = r.data.review_zone;
    check(`${replayId}: expected ${expected}`, actual === expected, `actual ${actual}`);
    check(`${replayId}: UI zone matches backend`, await page.getByTestId("review-zone").getAttribute("data-zone") === actual);
    check(`${replayId}: summary REPLAY visible`, await page.getByText("REPLAY — воспроизведение записи", { exact: true }).isVisible());
    await page.screenshot({ path: join(SHOTS, `${replayId}-summary.png`), fullPage: true });
    await page.getByRole("button", { name: "Сохранить отчёт HTML", exact: true }).click();
    await page.getByText(/Файл записан:/).waitFor();
    const file = shell.saved.at(-1);
    const reportPage = await ctx.newPage();
    await reportPage.goto(pathToFileURL(file).href);
    check(`${replayId}: exported HTML opens with zone`, await reportPage.locator(`.zone-${actual}`).count() > 0);
    check(`${replayId}: HTML is marked REPLAY`, (await reportPage.locator("body").innerText()).includes("REPLAY — ВОСПРОИЗВЕДЕНИЕ ЗАПИСИ"));
    await reportPage.screenshot({ path: join(SHOTS, `${replayId}-html.png`), fullPage: true });
    await reportPage.close();
    const manifest = JSON.parse(readFileSync(join(process.env.QORGAU_REPLAY_DIR, `${replayId}.json`), "utf8"));
    cases.push({ replay_id: replayId, expected, actual, session_id: sid, start_delay_ms: startDelayMs,
      summary: r.data, media_sha256: manifest.media.sha256, pacing: manifest.pacing, html_file: file });
    writeFileSync(join(OUT, "cases.json"), JSON.stringify(cases, null, 2));
    await page.getByRole("button", { name: "Новая сессия", exact: true }).click();
    await page.getByText("Подготовка к экзамену").waitFor();
  }
  check("renderer never opens camera/microphone", await page.evaluate(() => window.__gum) === 0);
  check("no CSP violations", cspViolations.length === 0, cspViolations.join(" | "));
  check("no renderer errors", consoleErrors.length === 0, consoleErrors.join(" | "));
  check("backend checkout stayed at the recorded revision", backendRevision() === backendSha && !backendDirty, backendSha);
} catch (e) {
  await shot("zz-failure").catch(() => {});
  check("REPLAY flow completed", false, String(e));
} finally {
  clearInterval(pump);
  await browser.close();
  server.close();
  await shell.stop();
}
writeFileSync(join(OUT, "results.json"), JSON.stringify({
  recorded_at: new Date().toISOString(), platform: process.platform,
  scope: "product renderer + real main API + real backend/CV; Chromium transport, no OS guard or native save dialog",
  backend_root: backendRoot, backend_sha: backendSha, backend_dirty: backendDirty, cases, checks: results,
}, null, 2));
const failed = results.filter(r => !r.ok).length;
console.log(`${results.length - failed}/${results.length} checks passed`);
process.exit(failed ? 1 : 0);
