// Focused preparation UX regression. Labelled FixtureBridge only; no backend/camera/native code.
// Build with VITE_QORGAU_FIXTURE=1, then pass the asset directory and screenshot directory.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import { resolve, extname, sep } from "node:path";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE ?? "playwright");
const root = resolve(process.argv[2] ?? "dist/renderer-fixture");
const output = resolve(process.argv[3] ?? "preflight-shots");
await mkdir(output, { recursive: true });
const server = createServer(async (req, res) => {
  const file = resolve(root, "." + decodeURIComponent(new URL(req.url, "http://localhost").pathname));
  if (file !== root && !file.startsWith(root + sep)) { res.writeHead(403).end(); return; }
  try {
    const path = file === root ? resolve(root, "index.html") : file;
    const data = await readFile(path);
    res.setHeader("Content-Type", ({ ".html": "text/html", ".js": "text/javascript", ".css": "text/css" })[extname(path)] ?? "application/octet-stream");
    res.end(data);
  } catch { res.writeHead(404).end(); }
});
await new Promise((done) => server.listen(0, "127.0.0.1", done));
let browser;
const results = [];
try {
  browser = await chromium.launch({ channel: process.env.CHROME_CHANNEL ?? "chrome" });
  for (const width of [1366, 1024]) {
    const context = await browser.newContext({ viewport: { width, height: 768 }, reducedMotion: "reduce", locale: "ru-RU" });
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(String(error)));
    await page.addInitScript(() => {
      window.__gumCalls = 0;
      if (navigator.mediaDevices) navigator.mediaDevices.getUserMedia = async () => { window.__gumCalls++; throw new Error("No camera in fixture tests"); };
    });
    await page.goto(`http://127.0.0.1:${server.address().port}/?bridge=fixture`);
    await page.getByText("FIXTURE-режим").waitFor();
    const primary = page.getByRole("button", { name: "Проверить устройства", exact: true });
    assert(await primary.isDisabled(), "Consent must gate creation");
    assert.equal(await page.locator(".preflight-settings").evaluate((e) => e.open), false);
    assert.equal(await page.locator(".preflight-diagnostics").evaluate((e) => e.open), false);
    assert(await page.getByText("SYNTHETIC · тест без камеры", { exact: true }).isVisible());
    assert(await page.getByRole("heading", { name: "Класс", exact: true }).isVisible());
    await page.getByText("Защита частичная", { exact: true }).waitFor({ timeout: 12000 });
    assert(await page.getByText("Защита частичная", { exact: true }).isVisible(), "Protection warnings must remain visible");
    await page.getByPlaceholder("Имя или псевдоним").fill("SYNTHETIC UX check");
    await page.getByText("Студент ознакомлен", { exact: false }).click();
    assert(await primary.isEnabled());
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "Horizontal overflow");
    await page.screenshot({ path: resolve(output, `preflight-${width}.png`), fullPage: true });

    await page.locator(".preflight-settings > summary").click();
    await page.getByText("запись (replay)", { exact: true }).click();
    await page.locator(".preflight-settings > summary").click();
    assert(await primary.isDisabled());
    assert(await page.getByText("Для REPLAY нужен идентификатор записи.", { exact: false }).isVisible(), "Hidden settings must not hide the blocking reason");
    assert(await page.getByText("REPLAY · записанное видео", { exact: true }).isVisible());

    await page.locator(".preflight-settings > summary").click();
    await page.getByText("камера (live)", { exact: true }).click();
    await primary.click();
    await page.getByText("Начать нельзя", { exact: false }).waitFor();
    assert(await page.getByRole("button", { name: "К калибровке", exact: true }).isDisabled());
    assert.equal(await page.evaluate(() => window.__gumCalls), 0);
    assert.deepEqual(errors, []);
    results.push({ width, status: "pass", checks: ["consent_gate", "collapsed_settings", "collapsed_diagnostics", "visible_synthetic_label", "visible_class_connection", "visible_protection_warning", "no_overflow", "replay_reason_outside_details", "live_failure_visible", "no_camera", "no_page_errors"] });
    console.log(`PASS preflight fixture ${width}x768`);
    await context.close();
  }
} finally {
  await browser?.close();
  await new Promise((done) => server.close(done));
  await writeFile(resolve(output, "preflight-results.json"), JSON.stringify({ source: "FIXTURE", results }, null, 2) + "\n");
}
