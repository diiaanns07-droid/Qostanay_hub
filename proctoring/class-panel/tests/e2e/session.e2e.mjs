// Real C1 + teacher session UI, no API/WebSocket route mocks and no students/devices.
// Run: node proctoring/class-panel/tests/e2e/session.e2e.mjs [output-directory-outside-repo]
// QORGAU_TEST_ROOT: repository under test (default: this checkout).
// PYTHON: Python 3.12 executable (default: tested checkout's proctoring/.venv).
// PLAYWRIGHT_MODULE: installed Playwright module (default: normal Node resolution).
// PLAYWRIGHT_CHANNEL: installed browser channel (default: chrome, headless).
// Screenshots mask codes. PIN/cookies/codes and server output are never written to logs/files.
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { delimiter, dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { createConnection } from "node:net";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE ?? "playwright");
const here = dirname(fileURLToPath(import.meta.url));
const ownRoot = resolve(here, "../../../..");
const root = resolve(process.env.QORGAU_TEST_ROOT ?? ownRoot);
const proctoring = join(root, "proctoring");
const python = process.env.PYTHON ?? join(proctoring, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
const channel = process.env.PLAYWRIGHT_CHANNEL ?? "chrome";
const under = (parent, path) => {
  const r = relative(parent, path);
  return !r || (!r.startsWith(`..${sep}`) && r !== ".." && !isAbsolute(r));
};
if (!existsSync(join(proctoring, "classroom/server/__main__.py"))) throw new Error("QORGAU_TEST_ROOT must be a repository containing proctoring/classroom/server");
if (!existsSync(python)) throw new Error("Python executable absent; set PYTHON to a prepared Python 3.12 environment");
const out = process.argv[2] ? resolve(process.argv[2]) : mkdtempSync(join(tmpdir(), "qorgau-session-evidence-"));
if (under(root, out) || under(ownRoot, out)) throw new Error("Evidence directory must be outside both repository checkouts");
mkdirSync(out, { recursive: true });
const work = mkdtempSync(join(tmpdir(), "qorgau-session-runtime-"));
const results = [];
const secrets = new Set();
const redact = (value) => {
  let text = String(value);
  for (const secret of secrets) if (secret) text = text.replaceAll(secret, "[redacted]");
  return text.replace(/(?<!\d)\d{6}(?!\d)/g, "[code redacted]");
};
const check = (name, ok, detail = "") => {
  const record = { name, ok: Boolean(ok), ...(detail ? { detail: redact(detail) } : {}) };
  results.push(record);
  console.log(`${ok ? "PASS" : "FAIL"} ${name}${record.detail ? ` — ${record.detail}` : ""}`);
  return Boolean(ok);
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let browser, context, page, server, port, pin;
let serverExit = null;
let stopping = false;
let runtimeRemoved = false;
let graceful = false;
let receivedUnexpectedServerExit = false;
const environment = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith("QORGAU_") && key !== "PYTHONPATH"));
Object.assign(environment, {
  PYTHONPATH: [proctoring, join(proctoring, "backend"), join(proctoring, "contracts/python")].join(delimiter),
  PYTHONUNBUFFERED: "1", PYTHONIOENCODING: "utf-8", PYTHONDONTWRITEBYTECODE: "1",
});

function startServer() {
  return new Promise((done, reject) => {
    server = spawn(python, ["-m", "classroom.server", "--host", "127.0.0.1", "--port", "0", "--ui", "class-panel",
      "--data-dir", join(work, "classroom"), "--exit-on-stdin-eof"], { cwd: proctoring, env: environment, windowsHide: true, stdio: ["pipe", "pipe", "pipe"] });
    let buffer = "";
    let settled = false;
    const timeout = setTimeout(() => finish(new Error("C1 did not become ready within 30 seconds (server output withheld to protect credentials)")), 30_000);
    const finish = (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      if (error) reject(error); else done();
    };
    serverExit = new Promise((res) => server.once("exit", (code) => {
      if (!stopping) receivedUnexpectedServerExit = true;
      if (!settled) finish(new Error(`C1 exited before readiness (exit ${code})`));
      res(code);
    }));
    server.once("error", () => finish(new Error("Cannot launch Python/C1; check PYTHON and dependencies")));
    server.stdout.setEncoding("utf8");
    server.stdout.on("data", (chunk) => {
      buffer += chunk;
      let newline;
      while ((newline = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, newline).trim();
        buffer = buffer.slice(newline + 1);
        if (line.startsWith("QORGAU_CLASS_READY ")) {
          try { port = JSON.parse(line.slice("QORGAU_CLASS_READY ".length)).port; } catch { finish(new Error("Invalid C1 readiness message")); }
        }
        if (line.startsWith("QORGAU_CLASS_PIN ")) {
          pin = line.slice("QORGAU_CLASS_PIN ".length);
          secrets.add(pin);
        }
        if (Number.isInteger(port) && port > 0 && port < 65536 && /^\d{6}$/.test(pin ?? "")) finish();
      }
    });
    // Drain both pipes, but never retain/print stderr: C1 writes its PIN there too.
    server.stderr.resume();
    server.stdin.on("error", () => {});
  });
}

async function stopServer() {
  if (!server || !serverExit) return;
  if (!server.pid) return; // spawn failed; there is no child to wait for
  if (server.exitCode !== null) return;
  stopping = true;
  server.stdin.end();
  const timer = setTimeout(() => server.kill("SIGTERM"), 8_000);
  try { graceful = (await serverExit) === 0; } finally { clearTimeout(timer); }
}

const isListening = () => new Promise((res) => {
  const socket = createConnection({ host: "127.0.0.1", port });
  socket.setTimeout(1_000);
  const finish = (yes) => { socket.destroy(); res(yes); };
  socket.once("connect", () => finish(true));
  socket.once("error", () => finish(false));
  socket.once("timeout", () => finish(false));
});
process.on("exit", () => { if (server && server.exitCode === null) server.kill(); });

async function login(base) {
  await page.goto(base);
  await page.locator("#pin").waitFor();
  check("real login form is required", new URL(page.url()).pathname === "/login");
  await page.locator("#pin").fill(pin);
  await Promise.all([page.waitForURL(base), page.locator('button[type="submit"]').click()]);
  await page.locator(".session-bar").waitFor();
  await page.waitForFunction(() => {
    const button = document.querySelector(".session-bar > button.btn-primary");
    return button && !button.disabled;
  });
}

async function snapshot(name) {
  // All screenshots use masks, including failure evidence. No browser trace/storageState is saved.
  // Paint the mask in the element's own layer so a code behind <dialog> cannot obscure modal controls.
  await page.screenshot({ path: join(out, `${name}.png`), fullPage: true, animations: "disabled",
    style: ".join-code { color: transparent !important; background: #222 !important; } #pin { color: transparent !important; }" });
}

async function geometry(name, dialog = false) {
  const metrics = await page.evaluate((modal) => {
    const d = document.querySelector(".session-dialog");
    const r = d?.getBoundingClientRect();
    return { viewport: innerWidth, document: document.documentElement.scrollWidth,
      dialogOverflow: modal && d ? d.scrollWidth - d.clientWidth : 0,
      dialogInside: !modal || (r && r.left >= -1 && r.right <= innerWidth + 1) };
  }, dialog);
  check(`${name}: no horizontal overflow`, metrics.document <= metrics.viewport + 1 && metrics.dialogOverflow <= 1 && metrics.dialogInside,
    `document ${metrics.document}, viewport ${metrics.viewport}, dialog overflow ${metrics.dialogOverflow}`);
}

async function currentSession(base) {
  const response = await context.request.get(`${base}api/teacher/session`);
  if (response.status() !== 200) throw new Error(`GET session returned ${response.status()}`);
  const body = await response.json();
  if (body?.join_code) secrets.add(body.join_code);
  return body;
}

async function main() {
  await startServer();
  check("actual C1 ready on ephemeral loopback port", true);
  const base = `http://127.0.0.1:${port}/`;
  browser = await chromium.launch({ headless: true, channel });
  context = await browser.newContext({ viewport: { width: 1366, height: 768 }, locale: "ru-RU" });
  page = await context.newPage();
  page.setDefaultTimeout(8_000);
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(redact(error.message)));
  let postCount = 0;
  page.on("request", (request) => {
    if (request.method() === "POST" && new URL(request.url()).pathname === "/api/teacher/session") postCount++;
  });
  await login(base);
  check("panel uses REAL adapter", await page.evaluate(() => document.documentElement.dataset.mode === "real" && !document.querySelector(".demo-strip")));
  check("new database has no classroom session", (await currentSession(base)) === null);
  const create = page.locator(".session-bar > button.btn-primary");
  const dialog = page.locator(".session-dialog");
  const submit = dialog.getByRole("button", { name: "Создать и получить код", exact: true });
  const confirm = dialog.locator('.session-replace input[type="checkbox"]');
  const sessionError = dialog.locator(".session-error");
  await create.focus();
  await page.keyboard.press("Enter");
  await dialog.waitFor();
  check("keyboard Enter opens dialog and focuses name", await page.evaluate(() => document.activeElement?.id === "session-name"));
  let trapped = true;
  const focusTrail = [];
  for (let i = 0; i < 12; i++) {
    await page.keyboard.press("Tab");
    const focus = await page.evaluate(() => ({ inside: !!document.activeElement?.closest(".session-dialog"),
      documentFocused: document.hasFocus(), tag: document.activeElement?.tagName, id: document.activeElement?.id }));
    // Native <dialog> may let Tab visit browser chrome. It must never visit page controls behind the modal.
    trapped &&= focus.inside || (!focus.documentFocused && focus.tag === "BODY");
    focusTrail.push(`${focus.tag}#${focus.id}:${focus.inside ? "modal" : focus.documentFocused ? "background" : "browser"}`);
  }
  check("keyboard Tab does not reach controls behind modal", trapped, trapped ? "" : focusTrail.join(" → "));
  await page.keyboard.press("Escape");
  check("Escape cancels and restores trigger focus", await dialog.isHidden() && await create.evaluate((button) => button === document.activeElement));
  check("opening/cancelling dialog creates no session", postCount === 0 && (await currentSession(base)) === null);

  await create.focus();
  await page.keyboard.press("Enter");
  const title = "Проверка C1 · группа 12";
  const url = "https://exam.example.invalid/task?group=12";
  await page.locator("#session-name").fill(title);
  await page.keyboard.press("Tab");
  check("Tab from name reaches exam URL", await page.evaluate(() => document.activeElement?.id === "session-site"));
  await page.keyboard.type(url);
  check("first class does not require replacement confirmation", await dialog.locator(".session-replace").isHidden());
  await geometry("1366 first-class dialog", true);
  await snapshot("1366-create-dialog");
  await submit.focus();
  const createdResponse = page.waitForResponse((r) => r.request().method() === "POST" && new URL(r.url()).pathname === "/api/teacher/session");
  await page.keyboard.press("Enter");
  check("UI creates session through actual C1 POST", (await createdResponse).status() === 201);
  await dialog.waitFor({ state: "hidden" });
  await page.locator(".join-code").waitFor();
  const first = await currentSession(base);
  check("GET session preserves title, URL, mode and open state", first.title === title && first.exam.start_url === url &&
    first.exam.mode === "url" && first.exam.allowed_urls.length === 1 && first.exam.allowed_urls[0] === url && first.state === "open");
  check("displayed six-digit code comes from real session", /^\d{6}$/.test(first.join_code ?? "") && (await page.locator(".join-code").textContent()) === first.join_code);
  await page.reload();
  await page.waitForFunction(() => /^\d{6}$/.test(document.querySelector(".join-code")?.textContent ?? ""));
  const persisted = await currentSession(base);
  check("page reload retains session and code", persisted.session_id === first.session_id && persisted.join_code === first.join_code &&
    (await page.locator(".join-code").textContent()) === first.join_code);
  for (const viewport of [{ width: 1366, height: 768 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    await geometry(`${viewport.width} session summary`);
    await snapshot(`${viewport.width}-class-created`);
  }

  await create.click();
  await dialog.waitFor();
  check("new class requires unchecked confirmation", await confirm.isVisible() && !await confirm.isChecked() && await confirm.getAttribute("required") !== null);
  await page.locator("#session-name").fill("Проверка замены · группа 13");
  const beforeUnconfirmed = postCount;
  await submit.click();
  check("unchecked replacement is blocked by actual form validation", await confirm.evaluate((el) => el.validity.valueMissing) && await dialog.isVisible());
  await page.locator("#session-name").press("Enter");
  await sleep(150);
  check("click and Enter cannot silently replace current class", postCount === beforeUnconfirmed && (await currentSession(base)).session_id === first.session_id);
  for (const viewport of [{ width: 390, height: 844 }, { width: 1366, height: 768 }]) {
    await page.setViewportSize(viewport);
    await geometry(`${viewport.width} replacement dialog`, true);
    await snapshot(`${viewport.width}-replacement-confirmation`);
  }

  await confirm.check();
  await page.locator("#session-site").fill("ftp://exam.example.invalid/task");
  const beforeInvalid = postCount;
  await submit.click();
  await sessionError.waitFor();
  check("unsupported URL has clear localized validation", /адрес|ссылк|http/i.test(await sessionError.textContent()) && /[А-Яа-яЁё]/.test(await sessionError.textContent()));
  check("invalid URL does not send a create request", postCount === beforeInvalid && (await currentSession(base)).session_id === first.session_id);
  await page.locator("#session-site").fill(url);
  await dialog.locator("summary").click();
  await page.locator("#session-extra").fill("not-an-address");
  await submit.click();
  await sessionError.waitFor();
  check("malformed additional URL has clear localized validation", /[А-Яа-яЁё]/.test(await sessionError.textContent()) &&
    /адрес|ссылк|http/i.test(await sessionError.textContent()), await sessionError.textContent());
  check("malformed additional URL does not create session", postCount === beforeInvalid && (await currentSession(base)).session_id === first.session_id);
  await page.locator("#session-extra").fill("https://login.example.invalid/*");
  const replacedResponse = page.waitForResponse((r) => r.request().method() === "POST" && new URL(r.url()).pathname === "/api/teacher/session");
  await submit.click();
  check("confirmed replacement reaches actual C1", (await replacedResponse).status() === 201);
  await dialog.waitFor({ state: "hidden" });
  const second = await currentSession(base);
  check("replacement creates a distinct session with additional address", second.session_id !== first.session_id &&
    second.title === "Проверка замены · группа 13" && second.exam.allowed_urls.includes("https://login.example.invalid/*"));

  // Exercise a genuine 401 by removing only this isolated browser context's test cookie.
  await create.click();
  check("replacement confirmation resets on reopening", !await confirm.isChecked());
  await confirm.check();
  await context.clearCookies();
  const unauthorizedResponse = page.waitForResponse((r) => r.request().method() === "POST" && new URL(r.url()).pathname === "/api/teacher/session");
  await submit.click();
  check("expired browser authentication receives real 401", (await unauthorizedResponse).status() === 401);
  await page.waitForFunction(() => /Войдите.*заново/.test(document.querySelector(".session-error")?.textContent ?? ""));
  check("401 explains how to sign in again", await sessionError.isVisible() && await submit.isEnabled());
  await snapshot("401-sign-in-needed");
  await login(base);
  check("401 did not replace the classroom session", (await currentSession(base)).session_id === second.session_id);
  check("no uncaught browser errors before connection-loss case", pageErrors.length === 0, pageErrors.join(" | "));

  // Real connection loss, not a fulfilled/aborted route: stop C1 by its stdin EOF protocol.
  await create.click();
  await confirm.check();
  await page.locator("#session-name").fill("Проверка потери связи");
  await stopServer();
  check("C1 exits cleanly on stdin EOF", graceful && !receivedUnexpectedServerExit);
  check("C1 leaves no loopback listener", !await isListening());
  await submit.click();
  await sessionError.waitFor();
  check("connection loss has understandable localized error", /[А-Яа-яЁё]/.test(await sessionError.textContent()) &&
    /связ|сервер|подключ|повтор|запрос|попроб/i.test(await sessionError.textContent()), await sessionError.textContent());
  check("connection error leaves cancel and submit usable", await submit.isEnabled() && await dialog.getByRole("button", { name: "Отмена", exact: true }).isEnabled());
  await snapshot("server-stopped-error");
}

try {
  await main();
} catch (error) {
  check("browser run completes without exception", false, redact(error?.message ?? error).slice(0, 900));
  if (page && !page.isClosed()) await snapshot("failure").catch(() => {});
} finally {
  await browser?.close().catch(() => {});
  await stopServer();
  // work was created by this process under tmpdir; never remove the evidence directory or repository.
  if (under(resolve(tmpdir()), work) && !under(root, work) && !under(ownRoot, work)) {
    rmSync(work, { recursive: true, force: true, maxRetries: 3, retryDelay: 150 });
    runtimeRemoved = true;
  }
}
check("temporary runtime data removed", runtimeRemoved);
const failed = results.filter((r) => !r.ok);
writeFileSync(join(out, "results.json"), JSON.stringify({ kind: "actual-c1-browser-session", channel,
  server: "loopback only", mocks: false, devices: false, screenshot_codes_masked: true, results }, null, 2) + "\n");
console.log(`session e2e: ${results.length - failed.length}/${results.length} PASS; evidence: ${out}`);
process.exitCode = failed.length ? 1 : 0;
