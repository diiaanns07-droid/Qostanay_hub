// A11 ISOLATED REPRO (not an integration run, not real Electron):
//   A07 @3fef6fb renderer (vite build) in Playwright Chromium
//   + a window.qorgau shim that forwards every call 1:1 to A06 @62a7fb1 dist/main/main.cjs IPC handlers
//     (loaded under A06's own electron-stub.cjs, trusted main-frame sender) — same channel names as preload.ts
//   + REAL A01r2 @29cadde backend (synthetic source, bootstrap engine; no camera, no models).
// Shell pushes (shell-state, stream-event) are forwarded to the page; preview frames are NOT forwarded.
import { createRequire } from "node:module";
import { readFileSync, mkdirSync } from "node:fs";
import { randomBytes, scryptSync } from "node:crypto";
import { join, resolve, extname } from "node:path";

const require = createRequire(import.meta.url);
const { chromium } = require(join(process.env.PW_ROOT ?? "/opt/node22/lib/node_modules", "playwright"));
const desktop = resolve(process.argv[2] ?? "./proctoring/desktop");
const OUT = resolve(process.argv[3] ?? "./shots");
mkdirSync(OUT, { recursive: true });
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
await until(async () => ((await state.handles.get("qorgau:api:health")(trusted)).ok ? true : null), 60_000);

// channel map copied from A06 main/src/ipc/channels.ts (preload uses the same)
const channelsSrc = readFileSync(resolve(desktop, "main/src/ipc/channels.ts"), "utf8");
const INVOKE = Object.fromEntries([...channelsSrc.matchAll(/^\s+(\w+): "(qorgau:(?:shell|api):[\w-]+)",$/gm)].map((m) => [m[1], m[2]]));

const browser = await chromium.launch();
const page = await (await browser.newContext({ viewport: { width: 1366, height: 768 }, locale: "ru-RU" })).newPage();
const callLog = [];
await page.exposeBinding("__qorgauInvoke", async (_src, channel, args) => {
  const r = await state.handles.get(channel)(trusted, ...args);
  const name = Object.keys(INVOKE).find((k) => INVOKE[k] === channel);
  if (r && r.ok === false) callLog.push(`${name} -> ${r.error.code}/${r.error.details?.shell_code}`);
  return r;
});
const origSend = win.webContents.send.bind(win.webContents);
win.webContents.send = (channel, ...args) => {
  origSend(channel, ...args);
  if (channel === "qorgau:push:preview-frame") return;
  void page.evaluate(([c, a]) => window.__qorgauPush?.(c, a), [channel, args]).catch(() => {});
};
await page.addInitScript((INV) => {
  const L = { "qorgau:push:shell-state": new Set(), "qorgau:push:stream-event": new Set(), "qorgau:push:preview-frame": new Set() };
  window.__qorgauPush = (c, a) => L[c]?.forEach((l) => l(...a));
  const call = (n) => (...args) => window.__qorgauInvoke(INV[n], args);
  const fan = (c) => (l) => (L[c].add(l), () => L[c].delete(l));
  const b = { bridgeVersion: "1.0.0", transport: "electron", onShellState: fan("qorgau:push:shell-state"),
    subscribeEvents: fan("qorgau:push:stream-event"), subscribePreview: fan("qorgau:push:preview-frame") };
  for (const n of Object.keys(INV)) b[n] = call(n);
  window.qorgau = Object.freeze(b);
}, INVOKE);
const dist = resolve(desktop, "dist/renderer");
const MIME = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".map": "application/json" };
await page.route("http://qorgau.test/**", (route) => {
  const p = new URL(route.request().url()).pathname;
  const f = join(dist, p === "/" ? "index.html" : p);
  try {
    route.fulfill({ status: 200, contentType: MIME[extname(f)] ?? "application/octet-stream", body: readFileSync(f) });
  } catch {
    route.fulfill({ status: 404, body: "nf" });
  }
});
const shot = (n) => page.screenshot({ path: join(OUT, `${n}.png`) });
const shellState = () => state.handles.get("qorgau:shell:get-state")(trusted);
const banners = async () => (await page.locator(".banner, [role=alert], [role=status]").allInnerTexts()).map((t) => t.replace(/\s+/g, " ").trim()).filter(Boolean);
const log = (...a) => console.log(...a);

try {
  await page.goto("http://qorgau.test/index.html");
  await page.getByText("на связи").waitFor({ timeout: 30000 });
  await page.getByText("синтетический тест").click();
  await page.getByText("Студент ознакомлен").click();
  await page.getByRole("button", { name: "Создать сессию и проверить" }).click();
  await page.getByText("Обязательные проверки пройдены").waitFor({ timeout: 30000 });
  await page.getByRole("button", { name: "Без калибровки…" }).click();
  await page.getByLabel("PIN").fill(PIN);
  await page.getByRole("button", { name: "Открыть" }).click();
  await page.getByRole("button", { name: "Без калибровки…" }).click();
  // A07 SkipDialog allows 300 chars, A06 validate.ts + contract allow 200
  await page.getByLabel("Причина").fill("Причина пропуска калибровки: ".padEnd(250, "x"));
  await page.getByRole("button", { name: "Пропустить калибровку" }).click();
  await sleep(800);
  log("UI skip with a 250-char reason (A07 textarea maxLength=300):");
  for (const b of await banners()) log("   | " + b.slice(0, 200));
  await page.getByLabel("Причина").fill("teacher decision");
  await page.getByRole("button", { name: "Пропустить калибровку" }).click();
  await page.getByText("Всё готово к началу").waitFor();
  await page.getByRole("button", { name: "Начать экзамен" }).click();
  await page.getByText("Демонстрационный экзамен").first().waitFor({ timeout: 20000 });
  log(`\nexam running, shell: ${JSON.stringify(await shellState()).slice(0, 110)}`);
  await page.getByRole("button", { name: "Режим преподавателя", exact: true }).click();
  await page.getByLabel("PIN").fill(PIN);
  await page.getByRole("button", { name: "Открыть" }).click();
  await page.getByText("Наблюдение за сессией").waitFor();
  log(`teacher unlocked console; shell.operator_unlocked=${(await shellState()).operator_unlocked}`);
  // renderer crash -> A06 main.ts render-process-gone handler (releases, then reloads the page)
  win.webContents.emit("render-process-gone", {}, { reason: "crashed", exitCode: 133 });
  await sleep(300);
  const sCrash = await shellState();
  log(`after render-process-gone: mode=${sCrash.mode} exam_mode_active=${sCrash.exam_mode_active} operator_unlocked=${sCrash.operator_unlocked}`);
  await page.reload();
  await page.getByText("Демонстрационный экзамен").first().waitFor({ timeout: 20000 });
  await sleep(500);
  const sRel = await shellState();
  log(`after renderer reload (A07 restores bound session via getShellState+getSession): mode=${sRel.mode} exam_mode_active=${sRel.exam_mode_active} operator_unlocked=${sRel.operator_unlocked}`);
  log(`   UI shows student exam view: ${await page.getByText("Наблюдение за сессией").count() === 0}`);
  await shot("04-after-reload-student-view");
  // anyone now clicks "Режим преподавателя"
  await page.getByRole("button", { name: "Режим преподавателя", exact: true }).click();
  await sleep(500);
  const pinShown = await page.getByLabel("PIN").count();
  const consoleShown = await page.getByText("Наблюдение за сессией").count();
  log(`click 'Режим преподавателя': PIN dialog shown=${pinShown > 0}; teacher console opened=${consoleShown > 0}`);
  await shot("05-console-without-pin");
  if (consoleShown) {
    await page.getByRole("button", { name: "Пауза" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Пауза" }).click();
    await page.getByRole("button", { name: "Продолжить" }).waitFor();
    const sP = await shellState();
    log(`paused without PIN: mode=${sP.mode} exam_mode_active=${sP.exam_mode_active} (restrictions released)`);
  }
  log(`\nfailed bridge calls seen by the renderer:\n   ${[...new Set(callLog)].join("\n   ")}`);
} catch (e) {
  await shot("zz-failure").catch(() => {});
  log("REPRO SCRIPT ERROR", e);
} finally {
  await browser.close();
  state.appEmit("before-quit", { defaultPrevented: false, preventDefault() {} });
  await until(() => state.exited !== null, 20_000);
  process.exit(0);
}
