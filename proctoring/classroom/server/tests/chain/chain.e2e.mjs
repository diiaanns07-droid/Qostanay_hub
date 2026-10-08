// Minimal C1 chain in a real browser (owner: T01):
//   real C2 uplink code (codex/class-C2, synthetic data source) -> T01 class server -> T02 class panel, REAL adapter.
// Nothing here is a camera or a real class network: the student's data is SYNTHETIC and labelled «тест, синтетика».
//
//   C2_BACKEND=<checkout of codex/class-C2>/proctoring/backend node proctoring/classroom/server/tests/chain/chain.e2e.mjs [outDir]
//   PYTHON=proctoring/.venv/bin/python (default)   PLAYWRIGHT_MODULE=/opt/node22/lib/node_modules/playwright (default)
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE ?? "/opt/node22/lib/node_modules/playwright");
const here = dirname(fileURLToPath(import.meta.url));
const proctoring = resolve(here, "..", "..", "..", "..");
const PY = process.env.PYTHON ?? resolve(proctoring, ".venv", "bin", "python");
const C2 = process.env.C2_BACKEND;
const OUT = resolve(process.argv[2] ?? "c1-chain-e2e");
mkdirSync(OUT, { recursive: true });
if (!C2 || !existsSync(join(C2, "proctor", "uplink", "client.py"))) {
  console.log("SKIP  C2_BACKEND must point to <codex/class-C2 checkout>/proctoring/backend (proctor/uplink not found)");
  process.exit(2);
}

const PIN = "246810";
const LABEL = "C2-ЦЕПОЧКА";
const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok: !!ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? ` — ${detail}` : ""}`);
};
const work = mkdtempSync(join(tmpdir(), "c1-chain-"));
const children = [];
const cleanup = () => children.forEach((c) => c.exitCode === null && c.kill("SIGTERM"));
process.on("exit", cleanup);

function run(args, env, onLine) {
  const child = spawn(PY, args, { cwd: proctoring, env: { ...process.env, ...env }, stdio: ["pipe", "pipe", "pipe"] });
  children.push(child);
  let buf = "";
  child.stdout.on("data", (d) => {
    buf += d;
    let i;
    while ((i = buf.indexOf("\n")) >= 0) {
      onLine?.(buf.slice(0, i));
      buf = buf.slice(i + 1);
    }
  });
  const err = [];
  child.stderr.on("data", (d) => err.push(String(d)));
  child.stderrText = () => err.join("");
  return child;
}

// 1. class server serving the T02 panel
let port = null;
const server = run(["-m", "classroom.server", "--port", "0", "--data-dir", join(work, "server"), "--ui", "class-panel", "--exit-on-stdin-eof"],
  { PYTHONPATH: ".", QORGAU_CLASS_TEACHER_PIN: PIN }, (line) => {
    if (line.startsWith("QORGAU_CLASS_READY ")) port = JSON.parse(line.slice(19)).port;
  });
for (let i = 0; i < 100 && port === null; i++) await new Promise((r) => setTimeout(r, 100));
if (port === null) {
  console.log(server.stderrText());
  throw new Error("server did not start");
}
const BASE = `http://127.0.0.1:${port}/`;
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1366, height: 768 }, locale: "ru-RU" });
const page = await ctx.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
let summary = null;
try {
  // 2. teacher login through the server's page
  await page.goto(BASE);
  check("unauthenticated / redirects to the login page", page.url().endsWith("/login"), page.url());
  await page.fill("#pin", "000000");
  await page.click("button[type=submit]");
  check("wrong PIN is refused", (await page.locator(".err").textContent())?.includes("Неверный PIN"));
  errors.length = 0;  // the deliberate wrong PIN above logs "401" in the console; count errors from here on
  await page.fill("#pin", PIN);
  await Promise.all([page.waitForURL(BASE), page.click("button[type=submit]")]);
  await page.waitForSelector(".grid", { state: "attached", timeout: 10000 });  // hidden while the class is empty
  check("T02 panel opens in REAL mode (no DEMO strip)", (await page.evaluate(() => document.documentElement.dataset.mode)) === "real" && (await page.locator(".demo-strip").count()) === 0);

  // 3. session + join code (same cookie jar as the page)
  const created = await (await ctx.request.post(`${BASE}api/teacher/session`, { data: { title: "Проверка цепочки C1 (тест)", mode: "url" } })).json();
  check("session created, join code issued", /^\d{6}$/.test(created.join_code ?? ""), created.join_code);

  // 4. the real C2 uplink joins with the code
  const c2 = run([resolve(here, "c2_student.py"), "--server", `127.0.0.1:${port}`, "--code", created.join_code, "--state-dir", join(work, "c2"), "--duration", "14", "--incident-after", "3"],
    { PYTHONPATH: C2 }, (line) => {
      const msg = JSON.parse(line);
      if (msg.event === "summary") summary = msg;
    });
  const card = page.locator(".grid > li.card", { hasText: LABEL });
  await card.waitFor({ timeout: 15000 });
  check("C2 student appears in the REAL panel", true, (await card.innerText()).replace(/\s+/g, " ").slice(0, 160));
  await page.waitForFunction((label) => {
    const el = [...document.querySelectorAll(".grid > li.card")].find((e) => e.textContent.includes(label));
    return el && el.querySelector(".link")?.getAttribute("data-link") === "online";
  }, LABEL, { timeout: 10000 }).then(() => check("card shows the student online", true), () => check("card shows the student online", false));
  await page.waitForFunction((label) => {
    const el = [...document.querySelectorAll(".grid > li.card")].find((e) => e.textContent.includes(label));
    return el && /1/.test(el.querySelector(".episodes")?.textContent ?? "");
  }, LABEL, { timeout: 15000 }).then(() => check("incident from C2 counted on the card", true), () => check("incident from C2 counted on the card", false, "episodes counter did not show 1"));
  const previewSrc = await card.locator("img").getAttribute("src").catch(() => null);
  check("preview from C2 shown on the card", (previewSrc ?? "").startsWith("data:image/jpeg"), (previewSrc ?? "none").slice(0, 30));
  await page.screenshot({ path: join(OUT, "c1-chain-panel.png") });

  // 5. the episode itself in the student's drawer (GET /api/teacher/students/{id}/incidents)
  await card.locator(".card-hit").click({ force: true });
  const ep = page.locator(".drawer .ep-text", { hasText: "ТЕСТ: синтетический эпизод" });
  await ep.first().waitFor({ timeout: 10000 }).then(() => check("episode text in the student drawer", true), () => check("episode text in the student drawer", false));
  await page.screenshot({ path: join(OUT, "c1-chain-drawer.png") });

  // 6. server side: one event with event_id, event_time, received_at
  const cards = await (await ctx.request.get(`${BASE}api/teacher/students`)).json();
  const sid = cards.find((c) => c.student_label.includes(LABEL))?.student_id;
  const events = await (await ctx.request.get(`${BASE}api/teacher/students/${sid}/events`)).json();
  check("event stored once with event_id, event_time, received_at", events.length >= 1 && events.every((e) => e.event_id && e.event_time && e.received_at), JSON.stringify(events.map((e) => [e.event_id, e.payload.state])));
  await new Promise((r) => c2.exitCode !== null ? r() : c2.on("exit", r));
  check("C2 uplink ran without errors", c2.exitCode === 0 && summary !== null, JSON.stringify(summary));
  check("no browser errors", errors.length === 0, errors.slice(0, 3).join(" | "));
} catch (e) {
  check("chain completed", false, String(e).slice(0, 300));
} finally {
  await browser.close();
  server.stdin.end();
  cleanup();
}
const failed = results.filter((r) => !r.ok);
writeFileSync(join(OUT, "c1-chain-results.json"), JSON.stringify({ results, summary, note: "synthetic student data via the real C2 uplink code; not a camera, not a real LAN" }, null, 2));
console.log(`${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
