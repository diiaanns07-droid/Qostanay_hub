// Real C1, real panel and synthetic WS peer. No mocked HTTP, camera, microphone or OS guard.
// PYTHON points at a prepared 3.12 runtime; PLAYWRIGHT_MODULE at installed Playwright.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, resolve, join, delimiter } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const proctoring = resolve(here, "../..");
const out = resolve(process.argv[2]);
mkdirSync(out, { recursive: true });
const work = mkdtempSync(join(out, "runtime-"));
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE ?? "playwright");
const env = { ...process.env, PYTHONPATH: [proctoring, join(proctoring, "backend"), join(proctoring, "contracts/python")].join(delimiter), PYTHONIOENCODING: "utf-8" };
const child = spawn(process.env.PYTHON, [join(here, "real-c1-peer.py"), work], { cwd: proctoring, env, windowsHide: true, stdio: ["pipe", "pipe", "pipe"] });
const exit = new Promise(resolve => child.on("exit", resolve));
child.stderr.on("data", () => {}); // fixture output may contain credentials; deliberately never log it
const ready = new Promise((resolve, reject) => {
  let buffer = "";
  const timeout = setTimeout(() => reject(new Error("C1 fixture did not become ready")), 30000);
  child.stdout.on("data", chunk => {
    buffer += chunk.toString();
    if (buffer.includes("\n")) { clearTimeout(timeout); resolve(JSON.parse(buffer.split("\n")[0])); }
  });
  child.once("error", reject);
});
const results = [];
const check = (name, value) => { assert.ok(value, name); results.push({ name, pass: true }); console.log(`PASS ${name}`); };
let browser;
try {
  const connection = await ready;
  browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL ?? "chrome" });
  const context = await browser.newContext({ viewport: { width: 1366, height: 900 }, locale: "ru-RU" });
  const login = await context.request.post(`${connection.base}/api/teacher/login`, { data: { pin: connection.pin } });
  check("teacher authenticated by real C1", login.status() === 200);
  const page = await context.newPage();
  page.setDefaultTimeout(9000);
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(connection.base);
  const config = await context.request.get(`${connection.base}/config.json`).then(r => r.json());
  check("C1 enables mounted exams in panel metadata", config.features?.includes("exams"));
  await page.locator(`.card-hit[data-id="${connection.student_id}"]`).click();
  const controls = page.locator('[data-testid="adal-controls"]');
  await controls.getByText("Класс: Контрольный класс", { exact: true }).waitFor();
  check("module uses current C1 class without a T04 exam", (await context.request.get(`${connection.base}/api/teacher/control/exams`).then(r => r.json())).length === 0);
  const screen = controls.locator('[data-testid="adal-lock"]');
  const command = controls.locator('[data-testid="adal-command"]');
  check("legacy false displays unknown", await screen.getAttribute("data-state") === "unknown");
  await controls.locator('[data-action="lock"]').click();
  await controls.locator('[data-testid="adal-lock-send"]').click();
  check("empty reason is rejected", (await controls.textContent()).includes("Укажите причину"));
  await controls.getByRole("button", { name: "Уберите телефон и дождитесь преподавателя", exact: true }).click();
  check("preset fills editable student-facing reason", (await controls.locator('textarea').inputValue()).includes("телефон"));
  await controls.locator('[data-testid="adal-lock-send"]').click();
  await page.waitForFunction(() => document.querySelector('[data-testid="adal-command"]')?.getAttribute("data-state") === "pending");
  check("accepted command remains pending before receipt", await command.getAttribute("data-state") === "pending");
  await page.waitForFunction(() => document.querySelector('[data-testid="adal-lock"]')?.getAttribute("data-state") === "locked");
  check("fresh receipt confirms Adal screen only", /Adal.*подтверждено/.test(await screen.textContent()));
  check("current card flag confirms app screen", (await page.locator(`.card[data-id="${connection.student_id}"] .flags`).textContent()).includes("экран Adal закрыт"));
  child.stdin.write("legacy\n");
  await new Promise(resolve => setTimeout(resolve, 150));
  await controls.locator('[data-action="unlock"]').click();
  await page.waitForFunction(() => document.querySelector('[data-testid="adal-command"]')?.getAttribute("data-state") === "unconfirmed");
  check("legacy ACK never confirms unlock", await screen.getAttribute("data-state") === "unknown");
  child.stdin.write("fail\n");
  await new Promise(resolve => setTimeout(resolve, 150));
  await controls.locator('[data-action="unlock"]').click();
  await page.waitForFunction(() => document.querySelector('[data-testid="adal-command"]')?.getAttribute("data-state") === "failed");
  check("actual negative ACK shows failed command", (await command.textContent()).includes("Экран не подтвердил"));
  child.stdin.write("normal\n");
  await new Promise(resolve => setTimeout(resolve, 150));
  await controls.locator('[data-action="unlock"]').click();
  await page.waitForFunction(() => document.querySelector('[data-testid="adal-lock"]')?.getAttribute("data-state") === "unlocked");
  check("new valid receipt confirms unlock", await command.getAttribute("data-state") === "confirmed");
  await controls.locator('[data-action="start_exam"]').click();
  await page.waitForFunction(() => document.querySelector('[data-testid="adal-command"]')?.getAttribute("data-state") === "confirmed" && document.querySelector('[data-testid="adal-command"]')?.textContent.includes("Начать"));
  check("start uses canonical command delivery", true);
  await page.setViewportSize({ width: 390, height: 844 });
  await controls.screenshot({ path: join(out, "controls-mobile.png") });
  check("mobile layout has no horizontal overflow", await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  await controls.locator('[data-action="finish_exam"]').click();
  check("finish requires explicit confirmation", await controls.locator('[data-testid="adal-finish-send"]').isVisible());
  await controls.locator('[data-testid="adal-finish-send"]').click();
  await page.waitForFunction(() => document.querySelector('[data-testid="adal-command"]')?.getAttribute("data-state") === "confirmed" && document.querySelector('[data-testid="adal-command"]')?.textContent.includes("Завершить"));
  check("finish outcome comes from C1 ACK", true);
  child.stdin.write("stale\n");
  await page.waitForFunction(() => document.querySelector('[data-testid="adal-lock"]')?.getAttribute("data-state") === "unknown");
  check("stale status invalidates lock and actions", await controls.locator('[data-action="lock"]').isDisabled());
  await context.request.post(`${connection.base}/api/teacher/session`, { data: { title: "Новый класс", mode: "url", allowed_urls: ["https://exam.example/*"] } });
  await page.waitForFunction(() => document.querySelector('[data-testid="adal-class"]')?.textContent.includes("не относится"));
  check("class replacement disables old student controls", await controls.locator('[data-action="unlock"]').isDisabled());
  check("no application page errors", errors.length === 0);
  writeFileSync(join(out, "results.json"), JSON.stringify({ source: "real C1, synthetic WS peer, no hardware", results }, null, 2));
} finally {
  await browser?.close();
  child.stdin.end("stop\n");
  const stopped = await Promise.race([exit.then(() => true), new Promise(resolve => setTimeout(() => resolve(false), 5000))]);
  if (!stopped) child.kill();
}
