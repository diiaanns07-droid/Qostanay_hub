// Real Electron + real Python service. Opens preparation only: no session, camera or exam guard.
// Build first. QORGAU_PYTHON may point at a shared venv; sources always come from this checkout.
// PLAYWRIGHT_MODULE selects an existing Playwright install (not a production dependency).
// node renderer/tests/electron-startup.mjs [screenshot.png]
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, resolve, join, delimiter } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { _electron } = require(process.env.PLAYWRIGHT_MODULE ?? "playwright");
const desktop = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const root = resolve(desktop, "..");
const work = mkdtempSync(join(tmpdir(), "qorgau-electron-startup-"));
const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith("QORGAU_") && !["ELECTRON_RUN_AS_NODE", "NODE_OPTIONS"].includes(key)));
Object.assign(env, {
  QORGAU_PYTHON: process.env.QORGAU_PYTHON ?? join(root, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python"),
  QORGAU_PROCTORING_ROOT: root,
  PYTHONPATH: [root, join(root, "backend"), join(root, "contracts/python")].join(delimiter),
  PYTHONIOENCODING: "utf-8", QORGAU_DATA_DIR: join(work, "data"),
  QORGAU_SHELL_NATIVE_ENFORCE: "0", QORGAU_SHELL_SELFTEST: "0",
  QORGAU_SHELL_LOG_LEVEL: "error", QORGAU_LOG_LEVEL: "WARNING",
});
let app;
try {
  const executablePath = require(join(desktop, "node_modules/electron"));
  app = await _electron.launch({ executablePath, args: [desktop, `--user-data-dir=${join(work, "profile")}`], cwd: desktop, env, timeout: 45000 });
  const page = await app.firstWindow();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.getByRole("button", { name: "Проверить устройства", exact: true }).waitFor();
  // Assert the user's visible readiness, not just an internal process flag.
  await page.getByText("на связи", { exact: true }).waitFor({ timeout: 20000 });
  await page.locator(".preflight-diagnostics > summary .badge").waitFor({ timeout: 20000 });
  assert.equal(await page.getByText(/Состояние сервиса: Нет связи/).count(), 0, "Stale preflight connection error after readiness");
  const state = await page.evaluate(() => window.qorgau.getShellState());
  assert.equal(state.backend, "ready");
  assert.equal(state.session_id, null, "Startup must not begin a session/device capture");
  assert.deepEqual(errors, []);
  if (process.argv[2]) await page.screenshot({ path: resolve(process.argv[2]), fullPage: true });
  console.log(JSON.stringify({ test: "actual-electron-startup", passed: true, backend_ready: true, session_started: false, renderer_errors: 0 }));
} finally {
  if (app) await app.close();
  // Only the unique directory created above is removed, never an output or caller-supplied path.
  rmSync(work, { recursive: true, force: true, maxRetries: 5, retryDelay: 300 });
}
