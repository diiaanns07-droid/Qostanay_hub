// A11 ISOLATED REPRO (not an integration run): A07's real renderer build (@3fef6fb, unchanged, vite build of a copy)
// in headless Chromium, talking to a REAL backend process built from A01r2 @29cadde + A05 @8f763a1 + A08 @5509950
// (combo tree, SYNTHETIC scripted source, no camera). window.qorgau is a thin test stand-in for A06's preload/main:
// every bridge call is forwarded 1:1 to the same /v1 route A06 main/src/ipc/api.ts uses; /v1/stream envelopes are
// forwarded unchanged (A06 main.ts:121-125 pushes every envelope). No Electron, no OS restrictions.
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { chromium } = require("playwright");
const WebSocket = require(path.join(__dirname, "node_modules", "ws"));

const W = __dirname;
const DIST = path.join(W, "ui/proctoring/desktop/dist/renderer");
const OUT = path.join(W, "out_ui");
fs.mkdirSync(OUT, { recursive: true });
const PY = "/home/user/Qostanay_hub/proctoring/.venv/bin/python";
const log = (...a) => console.log(...a);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function startBackend() {
  const token = crypto.randomBytes(32).toString("hex");
  const data = path.join(W, "tmp", `data-ui-${Date.now()}`);
  fs.mkdirSync(data, { recursive: true });
  const env = {
    ...process.env,
    PYTHONPATH: `${W}/combo/backend:${W}/combo/contracts/python`,
    PYTHONDONTWRITEBYTECODE: "1",
    QORGAU_DATA_DIR: data,
    QORGAU_MODELS_DIR: path.join(W, "tmp/models-empty"),
    QORGAU_LOG_LEVEL: "WARNING",
  };
  const proc = spawn(PY, ["-m", "proctor", "serve", "--token-stdin", "--port", "0"], { env, cwd: path.join(W, "tmp") });
  proc.stdin.write(token + "\n");
  const port = await new Promise((resolve, reject) => {
    let buf = "";
    proc.stdout.on("data", (d) => {
      buf += d;
      const m = buf.match(/QORGAU_READY (\{.*\})/);
      if (m) resolve(JSON.parse(m[1]).port);
    });
    proc.on("exit", (c) => reject(new Error("backend exited " + c)));
    setTimeout(() => reject(new Error("no READY")), 60000);
  });
  return { proc, port, token };
}

(async () => {
  const be = await startBackend();
  const base = `http://127.0.0.1:${be.port}/v1`;
  const H = { Authorization: `Bearer ${be.token}`, "Content-Type": "application/json" };
  const api = async (method, p, body) => {
    const r = await fetch(base + p, { method, headers: H, body: body === undefined ? undefined : JSON.stringify(body) });
    const ct = r.headers.get("content-type") || "";
    if (ct.startsWith("application/json")) {
      const j = await r.json();
      return r.ok ? { ok: true, data: j } : { ok: false, error: j.error };
    }
    const buf = Buffer.from(await r.arrayBuffer());
    return r.ok ? { ok: true, data: { media_type: ct.split(";")[0], bytes: [...buf] } } : { ok: false, error: { code: "INTERNAL", message: String(r.status), retryable: false, details: {} } };
  };
  // ---- drive a synthetic session up to RUNNING (what the student/teacher flow does before the console)
  const consent = { accepted: true, text_version: "consent-ru-1", accepted_at: "2026-10-08T09:00:00Z" };
  const s = await api("POST", "/sessions", { source: { mode: "synthetic" }, exam_id: "demo-exam-1", student_label: "a11", retain_media: true, consent });
  const sid = s.data.session_id;
  await api("POST", `/sessions/${sid}/preflight`);
  await api("POST", `/sessions/${sid}/calibration/start`);
  for (const t of ["center", "left", "right", "up", "down"]) {
    await api("POST", `/sessions/${sid}/calibration/target`, { target: t });
    for (let k = 0; k < 100; k++) {
      const c = await api("GET", `/sessions/${sid}/calibration`);
      if (c.data.targets.find((x) => x.target === t).state === "ok") break;
      await sleep(100);
    }
  }
  await api("POST", `/sessions/${sid}/calibration/finish`);
  await api("POST", `/sessions/${sid}/start`);
  log("session running:", sid);

  // ---- renderer
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1366, height: 900 }, locale: "ru-RU" });
  const consoleErrors = [];
  page.on("pageerror", (e) => consoleErrors.push(String(e)));
  await page.route("http://a11.renderer/**", (route) => {
    const u = new URL(route.request().url());
    const f = path.join(DIST, u.pathname === "/" ? "index.html" : u.pathname);
    const ext = path.extname(f);
    const type = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".map": "application/json" }[ext] || "application/octet-stream";
    route.fulfill({ status: 200, contentType: type, body: fs.readFileSync(f) });
  });
  const routes = {
    health: () => api("GET", "/health"),
    getSession: (a) => api("GET", `/sessions/${a[0]}`),
    listIncidents: (a) => api("GET", `/sessions/${a[0]}/incidents`),
    getIncident: (a) => api("GET", `/sessions/${a[0]}/incidents/${a[1]}`),
    addReview: (a) => api("POST", `/sessions/${a[0]}/incidents/${a[1]}/reviews`, a[2]),
    getEvidence: (a) => api("GET", `/sessions/${a[0]}/evidence/${a[1]}`),
    getSummary: (a) => api("GET", `/sessions/${a[0]}/summary`),
    finishExam: (a) => api("POST", `/sessions/${a[0]}/finish`),
    listAnswers: (a) => api("GET", `/sessions/${a[0]}/answers`),
    getExam: (a) => api("GET", `/sessions/${a[0]}/exam`),
    saveAnswer: (a) => api("PUT", `/sessions/${a[0]}/answers/${a[1]}`, a[2]),
    getEnvironmentCapabilities: async () => ({ ok: false, error: { code: "NOT_IMPLEMENTED", message: "a11 stub", retryable: false, details: {} } }),
  };
  await page.exposeBinding("__a11call", async (_src, name, args) => {
    const fn = routes[name];
    if (!fn) return { ok: false, error: { code: "NOT_IMPLEMENTED", message: `a11 stand-in: ${name}`, retryable: false, details: {} } };
    return fn(args);
  });
  const shell = { mode: "exam", backend: "ready", exam_mode_active: true, operator_unlocked: true, session_id: sid, last_error: null, shell_version: "a11-stub", platform: "linux" };
  await page.addInitScript((shellState) => {
    const listeners = new Set();
    window.__a11push = (env) => listeners.forEach((l) => l(env));
    const call = (name) => (...args) => window.__a11call(name, args);
    window.qorgau = new Proxy(
      {
        bridgeVersion: "1.0.0",
        getShellState: async () => shellState,
        onShellState: () => () => {},
        operatorLock: async () => shellState,
        subscribeEvents: (l) => { listeners.add(l); return () => listeners.delete(l); },
        subscribePreview: () => () => {},
      },
      { get: (t, k) => (k in t ? t[k] : typeof k === "string" ? call(k) : undefined) },
    );
  }, shell);
  await page.goto("http://a11.renderer/");
  // stream: forward every envelope unchanged (like A06 main.ts:121-125)
  const ws = new WebSocket(`ws://127.0.0.1:${be.port}/v1/stream`, { headers: { Authorization: `Bearer ${be.token}` } });
  const envs = [];
  ws.on("message", (d) => {
    const env = JSON.parse(String(d));
    envs.push(env);
    page.evaluate((e) => window.__a11push && window.__a11push(e), env).catch(() => {});
  });

  // ---- teacher opens the console (operator_unlocked already true in the shell stand-in)
  await page.getByRole("button", { name: "Режим преподавателя" }).click();
  await page.getByRole("heading", { name: "Наблюдение за сессией" }).waitFor({ timeout: 15000 });
  const phoneItem = page.locator("button.inc-item", { hasText: "Телефон в кадре" }).first();
  await phoneItem.waitFor({ timeout: 40000 });
  await phoneItem.click();
  await page.getByRole("radio", { name: "Подтвердить" }).click();
  await page.getByPlaceholder("инициалы или роль").fill("T1");
  await page.getByRole("button", { name: /Записать решение/ }).click();
  await page.locator("article.incident .badge", { hasText: "подтверждено" }).first().waitFor({ timeout: 10000 }).catch(() => {});
  await sleep(500);
  const read = async (label) => {
    const items = await page.locator("button.inc-item").evaluateAll((els) =>
      els.map((e) => `${e.querySelector(".inc-title")?.textContent?.trim()} => ${e.querySelector(".rs")?.textContent?.trim()}`),
    );
    const card = await page.locator("article.incident .incident-badges").first().textContent().catch(() => null);
    const aside = await page.locator(".card", { hasText: "Таймлайн" }).locator(".small.muted").first().textContent().catch(() => null);
    log(`[${label}] list: ${JSON.stringify(items)} | open card badges: ${card} | header: ${aside}`);
    return { items, card, aside };
  };
  const r1 = await read("live console, right after review");
  await page.screenshot({ path: `${OUT}/1-live-after-review.png` });
  // wait for the phone incident to close and the gaze one to appear
  await page.locator("button.inc-item", { hasText: "Долгий взгляд вниз" }).first().waitFor({ timeout: 40000 });
  await sleep(1500);
  const r2 = await read("live console, after WS closed change");
  await page.screenshot({ path: `${OUT}/2-live-after-close.png` });
  // finish (teacher action through the same route the shell uses)
  await api("POST", `/sessions/${sid}/finish`);
  await page.getByRole("heading", { name: "Проверка эпизодов" }).waitFor({ timeout: 15000 });
  await sleep(1500);
  const r3 = await read("post-exam review screen");
  await page.screenshot({ path: `${OUT}/3-review.png` });
  const restInc = (await api("GET", `/sessions/${sid}/incidents`)).data.map((i) => `${i.rule_id}#${i.update_seq}:${i.review_status}:ev${i.evidence_ids.length}`);
  log("REST /incidents (A08 truth):", JSON.stringify(restInc));
  await page.getByRole("button", { name: "К итогу →" }).click();
  await page.getByRole("heading", { name: "Итог сессии" }).waitFor({ timeout: 15000 });
  await sleep(800);
  const decisions = await page.locator(".card", { hasText: "Решения преподавателя" }).locator("tr").evaluateAll((rows) => rows.map((r) => r.textContent.trim()));
  const banner = await page.getByText(/Не проверено эпизодов/).count();
  const summary = (await api("GET", `/sessions/${sid}/summary`)).data;
  log(`[summary screen] decisions table: ${JSON.stringify(decisions)} | banner "Не проверено эпизодов" visible: ${banner > 0}`);
  log("REST /summary (A08 truth):", JSON.stringify({ incidents_total: summary.incidents_total, reviews_by_decision: summary.reviews_by_decision }));
  await page.screenshot({ path: `${OUT}/4-summary.png` });
  log("page errors:", consoleErrors.length);
  fs.writeFileSync(`${OUT}/stream_incidents.json`, JSON.stringify(envs.filter((e) => e.message.type === "incident"), null, 1));
  ws.close();
  await browser.close();
  be.proc.stdin.end();
  await sleep(1000);
  be.proc.kill();
})().catch((e) => {
  console.error("HARNESS ERROR", e);
  process.exit(1);
});
