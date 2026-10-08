// End-to-end test of the T04 teacher interface in real Chromium against the DEV server with SIMULATED
// students (proctor_classctl.devserver). No npm install: uses the globally installed Playwright.
//
//   NODE_PATH=$(npm root -g) node proctoring/class-control-ui/tests/e2e.cjs
//
// Env: T04_PYTHON (default proctoring/.venv/bin/python), T04_SHOTS (screenshots dir, default under /tmp).
"use strict";
const { chromium } = require("playwright");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const net = require("node:net");
const path = require("node:path");

const UI_DIR = path.resolve(__dirname, "..");
const PROCTORING = path.resolve(UI_DIR, "..");
const PY = process.env.T04_PYTHON || path.join(PROCTORING, ".venv", "bin", "python");
const SHOTS = process.env.T04_SHOTS || "/tmp/claude-0/-home-user-Qostanay-hub/7926ccfe-e122-595f-921f-1f2fd7866585/scratchpad/t04/ui-shots";
const TEACHER = "Айгерим Сейтова (преподаватель)";

const results = [];
let failed = 0;
let server = null;
let browser = null;

function log(...a) {
  console.log(...a);
}
function assert(cond, msg) {
  if (!cond) throw new Error(`assertion failed: ${msg}`);
}

async function freePort() {
  return new Promise((resolve, reject) => {
    const s = net.createServer();
    s.unref();
    s.on("error", reject);
    s.listen(0, "127.0.0.1", () => {
      const { port } = s.address();
      s.close(() => resolve(port));
    });
  });
}

async function waitHttp(url, timeoutMs) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    try {
      const r = await fetch(url);
      if (r.ok) return r.json();
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error(`server did not answer ${url} in ${timeoutMs} ms`);
}

async function step(name, fn) {
  const t0 = Date.now();
  try {
    await fn();
    results.push({ ok: true, name, ms: Date.now() - t0 });
    log(`ok   ${name} (${Date.now() - t0} ms)`);
  } catch (err) {
    failed += 1;
    results.push({ ok: false, name, error: String(err && err.message) });
    log(`FAIL ${name}: ${err && err.stack}`);
  }
}

function stopServer() {
  if (server && server.exitCode === null) {
    try {
      server.kill("SIGTERM");
    } catch {
      /* already gone */
    }
  }
}
process.on("exit", stopServer);
process.on("SIGINT", () => {
  stopServer();
  process.exit(130);
});

(async () => {
  fs.mkdirSync(SHOTS, { recursive: true });
  const port = await freePort();
  const base = `http://127.0.0.1:${port}`;
  server = spawn(PY, ["-m", "proctor_classctl.devserver", "--port", String(port)], { cwd: PROCTORING, stdio: ["ignore", "pipe", "pipe"] });
  let serverOut = "";
  server.stdout.on("data", (d) => (serverOut += d));
  server.stderr.on("data", (d) => (serverOut += d));
  const sim = await waitHttp(`${base}/sim/info`, 20000);
  const demoExam = sim.exam_id;
  log(`dev server pid ${server.pid} on ${base}, demo exam ${demoExam}`);

  browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: "ru-RU" });
  const page = await context.newPage();
  const pageErrors = [];
  page.on("pageerror", (e) => pageErrors.push(`pageerror: ${e.message}`));
  page.on("console", (m) => {
    if (m.type() === "error" && !/Failed to load resource|net::ERR_FAILED/.test(m.text())) pageErrors.push(`console: ${m.text()}`);
  });
  const shot = (name) => page.screenshot({ path: path.join(SHOTS, name), fullPage: true });

  const row = (sid) => page.locator(`tr[data-student="${sid}"]`);
  const groupOf = (sid) =>
    page.evaluate((s) => document.querySelector(`tr[data-student="${s}"] [data-testid="last"] [data-testid="cmd-chip"]`)?.getAttribute("data-group") ?? "none", sid);
  const waitGroup = (sid, group, timeout = 10000) =>
    page.waitForFunction(
      ([s, g]) => document.querySelector(`tr[data-student="${s}"] [data-testid="last"] [data-testid="cmd-chip"]`)?.getAttribute("data-group") === g,
      [sid, group],
      { timeout },
    );
  const lockLabel = (sid) => row(sid).locator('[data-testid="lock-label"]').textContent();
  const cmdLabel = (sid) => row(sid).locator('[data-testid="last"] [data-testid="cmd-label"]').textContent();
  const tab = (id) => page.click(`[role="tab"][data-tab="${id}"]`);
  const apiAs = (teacher, method, url, body) =>
    page.evaluate(
      async ([t, m, u, b]) => {
        const r = await fetch(u, { method: m, headers: { "X-Qorgau-Dev-Teacher": t, "Content-Type": "application/json" }, body: b === null ? undefined : JSON.stringify(b) });
        return { status: r.status, body: await r.json().catch(() => null) };
      },
      [teacher, method, url, body === undefined ? null : body],
    );
  async function lockViaRow(sid, reason) {
    await row(sid).locator('[data-action="lock"]').click();
    await page.fill('[data-testid="lock-reason"]', reason);
    await page.click('[data-testid="dialog-send"]');
    await page.waitForSelector('[data-testid="action-dialog"]', { state: "hidden" });
  }
  async function setSim(sid, behaviour) {
    await tab("sim");
    await page.selectOption(`[data-sim-behaviour="${sid}"]`, behaviour);
    await page.waitForFunction(([s, b]) => document.querySelector('[data-testid="sim-status"]')?.textContent?.includes(s), [sid, behaviour]);
    await tab("students");
  }
  async function setOnline(sid, on) {
    await tab("sim");
    const box = page.locator(`[data-sim-online="${sid}"]`);
    if ((await box.isChecked()) !== on) await box.click();
    await page.waitForFunction(([s, o]) => document.querySelector('[data-testid="sim-status"]')?.textContent?.includes(`${s} ${o ? "подключён" : "отключён"}`), [sid, on]);
    await tab("students");
  }

  await page.goto(`${base}/?poll_ms=250&teacher=t-aigerim`);
  await page.waitForSelector('tr[data-student="sim-01"]');

  await step("SIMULATOR banner and simulated rows are labelled", async () => {
    const banner = await page.locator('[data-testid="sim-banner"]').textContent();
    assert(banner.includes("СИМУЛЯТОР") && banner.includes("не реальные компьютеры"), `banner: ${banner}`);
    assert(await page.locator('[data-testid="sim-banner"]').isVisible(), "banner visible");
    assert(await row("sim-01").locator(".tag-sim").isVisible(), "row tag симулятор");
    await shot("01-start-simulator.png");
  });

  let newExamId = null;
  await step("create exam in url mode: field errors, sign-in explanation, suggestion chip adds (never auto-added)", async () => {
    await page.click('[data-testid="new-exam"]');
    await page.waitForSelector('[data-testid="exam-form"]');
    assert((await page.locator(".exam-tab h2").textContent()) === "Новый экзамен", "create heading");
    await page.fill('[data-testid="exam-title"]', 'Экзамен <img src=x onerror="window.__xss=1"> №2');
    await page.click('[data-testid="exam-save"]');
    const startErr = page.locator('[data-testid="exam-policy"] [data-testid="start_url-error"]');
    await startErr.waitFor({ state: "visible" });
    assert((await startErr.textContent()).includes("Укажите адрес экзамена"), "client-side required start_url");
    await page.fill('[data-testid="exam-policy"] [data-testid="start-url"]', "https://quiz.test.kz/exam/7");
    await page.fill('[data-testid="exam-policy"] [data-testid="allowed-urls-input"]', "ftp://files.test.kz");
    await page.click('[data-testid="exam-policy"] [data-testid="allowed-urls-add"]');
    await page.click('[data-testid="exam-save"]');
    const urlErr = page.locator('[data-testid="exam-policy"] [data-testid="allowed-urls-error"]');
    await urlErr.waitFor({ state: "visible" });
    const urlErrText = await urlErr.textContent();
    assert(/http\(s\)/.test(urlErrText) && urlErrText.includes("ftp://files.test.kz"), `server error next to the field: ${urlErrText}`);
    await page.click('[data-testid="exam-policy"] button[aria-label="Убрать ftp://files.test.kz"]');
    await page.fill('[data-testid="exam-policy"] [data-testid="allowed-urls-input"]', "https://cdn.test.kz/*");
    await page.press('[data-testid="exam-policy"] [data-testid="allowed-urls-input"]', "Enter");
    const note = await page.locator('[data-testid="exam-policy"] [data-testid="auth-note"]').textContent();
    assert(note.includes("Вход на сайт может требовать дополнительных доменов") && note.includes("перенаправляет на другие домены"), `auth note: ${note}`);
    const before = await page.locator('[data-testid="exam-policy"] [data-testid="auth-domains-items"]').textContent();
    assert(!before.includes("accounts.google.com"), "suggestion is not added automatically");
    await page.click('[data-testid="exam-policy"] [data-suggest="https://accounts.google.com/*"]');
    const after = await page.locator('[data-testid="exam-policy"] [data-testid="auth-domains-items"]').textContent();
    assert(after.includes("https://accounts.google.com/*"), "chip added the sign-in domain");
    assert((await page.getAttribute('[data-testid="exam-policy"] [data-suggest="https://accounts.google.com/*"]', "aria-pressed")) === "true", "chip shows it is in the list");
    await shot("02-exam-form-url-mode.png");
    await page.click('[data-testid="exam-save"]');
    await page.waitForSelector('[data-testid="saved-box"]');
    const saved = await page.locator('[data-testid="saved-box"]').textContent();
    assert(saved.includes("Экзамен создан") && saved.includes("добавлен в разрешённые адреса автоматически"), `server warnings shown: ${saved}`);
    newExamId = await page.inputValue("#exam-select");
    assert(newExamId && newExamId !== demoExam, "new exam selected");
    const xss = await page.evaluate(() => [window.__xss, document.querySelectorAll("img").length]);
    assert(xss[0] === undefined && xss[1] === 0, "title from the server rendered as text, no <img>");
    const opt = await page.locator(`#exam-select option[value="${newExamId}"]`).textContent();
    assert(opt.includes('<img src=x onerror="window.__xss=1">'), `option text literal: ${opt}`);
    await tab("policies");
    const card = page.locator(".policy-card").first();
    await card.waitFor();
    const cardText = await card.textContent();
    assert(cardText.includes("версия 1") && cardText.includes("https://accounts.google.com/*") && cardText.includes("https://quiz.test.kz/*"), `policy card: ${cardText}`);
    await shot("03-exam-created-policies.png");
  });

  await step("extra policy (app mode) and policy edit -> new version", async () => {
    await page.click('[data-testid="policy-new"]');
    await page.fill('[data-testid="policy-name"]', "Только программа");
    await page.check('[data-testid="policy-fields"] [data-testid="mode-app"]');
    await page.fill('[data-testid="policy-fields"] [data-testid="allowed-apps-input"]', "Test Client.exe");
    await page.click('[data-testid="policy-fields"] [data-testid="allowed-apps-add"]');
    await page.click('[data-testid="policy-save"]');
    await page.waitForSelector('[data-testid="policy-status"] [data-testid="saved-box"]');
    await page.waitForFunction(() => document.querySelectorAll(".policy-card").length === 2);
    const appCard = await page.locator(".policy-card", { hasText: "Только программа" }).textContent();
    assert(appCard.includes("test client.exe") && appCard.includes("версия 1") && appCard.includes("Отдельная программа"), `app card: ${appCard}`);
    await page.fill('[data-testid="policy-fields"] [data-testid="allowed-apps-input"]', "calc.exe");
    await page.click('[data-testid="policy-fields"] [data-testid="allowed-apps-add"]');
    await page.click('[data-testid="policy-save"]');
    await page.waitForFunction(() => document.querySelector('[data-testid="policy-status"]')?.textContent?.includes("версия 2"));
    await page.waitForFunction(() => [...document.querySelectorAll(".policy-card")].some((c) => c.textContent.includes("Только программа") && c.textContent.includes("версия 2")));
    // another tab edits the same policy -> our save is refused with 409, nothing is overwritten
    const ex = await apiAs("t-aigerim", "GET", `/api/teacher/control/exams/${newExamId}`);
    const pol = ex.body.policies.find((p) => p.name === "Только программа");
    const bg = await apiAs("t-aigerim", "PATCH", `/api/teacher/control/exams/${newExamId}/policies/${pol.policy_id}`, {
      version: pol.version,
      policy: { mode: "app", allowed_apps: ["other.exe"], instructions_ru: "" },
    });
    assert(bg.status === 200 && bg.body.version === 3, "background policy edit");
    await page.fill('[data-testid="policy-fields"] [data-testid="instructions"]', "Моя инструкция");
    await page.click('[data-testid="policy-save"]');
    await page.locator('[data-testid="policy-status"] [data-testid="stale-box"]').waitFor();
    await page.click('[data-testid="policy-status"] [data-testid="stale-reload"]');
    await page.waitForFunction(() => document.querySelector('[data-testid="policy-editor"] h3')?.textContent?.includes("v3"));
    assert((await page.locator('[data-testid="policy-fields"] [data-testid="allowed-apps-items"]').textContent()).includes("other.exe"), "fresh version loaded");
  });

  await step("stale edit (409) is shown as 'изменено другим действием — обновите' and nothing is overwritten", async () => {
    await tab("exam");
    await page.fill('[data-testid="exam-title"]', "Моя правка названия");
    const cur = await apiAs("t-aigerim", "GET", `/api/teacher/control/exams/${newExamId}`);
    const other = await apiAs("t-aigerim", "PATCH", `/api/teacher/control/exams/${newExamId}`, { revision: cur.body.revision, title: "Изменено другим действием" });
    assert(other.status === 200, `background edit ${other.status}`);
    await page.click('[data-testid="exam-save"]');
    const box = page.locator('[data-testid="exam-status"] [data-testid="stale-box"]');
    await box.waitFor();
    assert((await box.textContent()).includes("Изменено другим действием — обновите."), "stale message");
    await shot("04-stale-edit.png");
    const still = await apiAs("t-aigerim", "GET", `/api/teacher/control/exams/${newExamId}`);
    assert(still.body.title === "Изменено другим действием", "other edit not overwritten");
    await page.click('[data-testid="stale-reload"]');
    await page.waitForFunction(() => document.querySelector('[data-testid="exam-title"]')?.value === "Изменено другим действием");
  });

  await page.selectOption("#exam-select", demoExam);
  await tab("students");
  await page.waitForSelector('tr[data-student="sim-08"]');

  await step("unsupported action: lock button disabled AND the reason is shown", async () => {
    const lockBtn = row("sim-08").locator('[data-action="lock"]');
    assert(await lockBtn.isDisabled(), "sim-08 lock disabled");
    assert(await row("sim-08").locator('[data-action="unlock"]').isDisabled(), "sim-08 unlock disabled");
    assert(!(await row("sim-08").locator('[data-action="start_exam"]').isDisabled()), "sim-08 start enabled");
    const why = await row("sim-08").locator('[data-testid="row-why"]').textContent();
    assert(why.includes("Заблокировать: недоступно — Клиент студента не поддерживает действие «Заблокировать»"), `why: ${why}`);
    assert(await page.locator('[data-testid="site-timer-btn"]').isDisabled(), "site timer pause is explicitly unavailable");
    assert((await page.locator('[data-testid="site-timer-why"]').textContent()).includes("интеграции с сайтом нет"), "and says why");
  });

  await step("lock 'success' student: dialog lists unavailable students, timer note, expiry; badge pending -> executing -> done; 'Заблокирован' only after done", async () => {
    await row("sim-01").locator('input[type="checkbox"]').check();
    await row("sim-08").locator('input[type="checkbox"]').check();
    await page.click('[data-bulk="lock"]');
    const dlg = page.locator('[data-testid="action-dialog"]');
    await dlg.waitFor();
    assert((await dlg.locator('[data-testid="will-send"]').textContent()).includes("Студент 1"), "will send to sim-01");
    const skip = await dlg.locator('[data-testid="will-skip"]').textContent();
    assert(skip.includes("Студент 8") && skip.includes("не поддерживает действие «Заблокировать»"), `skip: ${skip}`);
    const timer = await dlg.locator('[data-testid="timer-note"]').textContent();
    assert(timer.includes("Таймер внешнего сайта") && timer.includes("НЕ останавливается"), `timer note: ${timer}`);
    assert((await dlg.locator('[data-testid="expiry"]').textContent()).includes("2 мин"), "expiry from meta ttl_default_s.lock");
    await page.click('[data-testid="dialog-send"]');
    assert((await dlg.locator(".field-error").textContent()).includes("Укажите причину"), "reason required");
    await page.fill('[data-testid="lock-reason"]', "Телефон на столе");
    assert((await dlg.locator(".counter").textContent()).startsWith("16 / 200"), "counter");
    await shot("05-lock-dialog.png");
    await page.evaluate(() => {
      window.__log = [];
      const rec = () => {
        const tr = document.querySelector('tr[data-student="sim-01"]');
        if (!tr) return;
        const g = tr.querySelector('[data-testid="last"] [data-testid="cmd-chip"]')?.getAttribute("data-group") ?? "none";
        const lock = tr.querySelector('[data-testid="lock-label"]')?.textContent ?? "";
        const last = window.__log[window.__log.length - 1];
        if (!last || last.g !== g || last.lock !== lock) window.__log.push({ g, lock });
      };
      new MutationObserver(rec).observe(document.querySelector('[data-testid="students-table"] tbody'), { subtree: true, childList: true, characterData: true, attributes: true });
      rec();
    });
    await page.click('[data-testid="dialog-send"]');
    const res = page.locator('[data-testid="cmd-results"]');
    await res.locator('li[data-outcome="created"]').waitFor();
    assert((await res.locator('li[data-outcome="unavailable"]').textContent()).includes("Студент 8"), "server's unavailable answer for sim-08 shown");
    const resText = await res.textContent();
    assert(resText.includes("Сервер принял запрос — это ещё не выполнение"), "results note");
    assert(!/Заблокирован/.test(resText), "no 'locked' claim from the POST answer");
    await waitGroup("sim-01", "done", 10000);
    await page.waitForFunction(() => document.querySelector('tr[data-student="sim-01"] [data-testid="lock-label"]')?.textContent === "Заблокирован (по статусу клиента)", null, { timeout: 5000 });
    const seq = await page.evaluate(() => window.__log);
    const groups = seq.map((x) => x.g).filter((g, i, a) => g !== a[i - 1]);
    log("     sim-01 badge sequence:", JSON.stringify(groups));
    const iPending = groups.indexOf("pending");
    const iExec = groups.indexOf("executing");
    const iDone = groups.indexOf("done");
    assert(iPending >= 0 && iExec > iPending && iDone > iExec, `pending -> executing -> done, got ${groups.join(" > ")}`);
    const firstLocked = seq.findIndex((x) => x.lock === "Заблокирован (по статусу клиента)");
    const firstDone = seq.findIndex((x) => x.g === "done");
    assert(firstLocked >= firstDone && firstDone >= 0, `locked label (${firstLocked}) not before done (${firstDone})`);
    assert(seq.slice(0, firstDone).every((x) => !x.lock.startsWith("Заблокирован")), "never 'Заблокирован' before the client confirmed");
    assert(seq.some((x) => (x.g === "pending" || x.g === "executing") && x.lock.startsWith("Блокировка:")), "pending lock label shown while in flight");
    await shot("06-lock-done.png");
    await row("sim-01").locator('[data-testid="details"]').click();
    const drawer = page.locator('[data-testid="student-drawer"]');
    await drawer.locator('li.cmd[data-group="done"]').first().waitFor();
    const states = await drawer.locator("li.cmd").first().locator(".history li").evaluateAll((els) => els.map((e) => e.getAttribute("data-state")));
    assert(JSON.stringify(states) === JSON.stringify(["queued", "sent", "executing", "succeeded"]), `history ${states}`);
    assert((await drawer.textContent()).includes("причина: «Телефон на столе»"), "reason in history");
    await shot("07-student-details.png");
    await page.keyboard.press("Escape");
    await drawer.waitFor({ state: "hidden" });
    await row("sim-01").locator('input[type="checkbox"]').uncheck();
    await row("sim-08").locator('input[type="checkbox"]').uncheck();
  });

  await step("lock 'delay' student shows 'выполняется' while the client works", async () => {
    await lockViaRow("sim-02", "Проверка задержки");
    await waitGroup("sim-02", "executing", 5000);
    assert((await cmdLabel("sim-02")) === "Выполняется", "label Выполняется");
    assert((await lockLabel("sim-02")).startsWith("Блокировка:"), "not claimed locked while executing");
  });

  await step("lock 'error' student -> ошибка with the client's error", async () => {
    await lockViaRow("sim-03", "Проверка ошибки");
    await waitGroup("sim-03", "error", 8000);
    const label = await cmdLabel("sim-03");
    assert(label.startsWith("Ошибка:") && label.includes("СИМУЛЯТОР"), `label ${label}`);
    assert(!(await lockLabel("sim-03")).startsWith("Заблокирован"), "error -> not locked");
    await shot("08-lock-error.png");
  });

  await step("lock offline student -> нет связи (queued until reconnect, not 'locked')", async () => {
    await lockViaRow("sim-06", "Проверка без связи");
    await waitGroup("sim-06", "no_connection", 5000);
    const label = await cmdLabel("sim-06");
    assert(label.startsWith("Нет связи — будет доставлена при подключении"), `label ${label}`);
    assert(!(await lockLabel("sim-06")).startsWith("Заблокирован"), "offline -> not locked");
    await shot("09-lock-offline.png");
  });

  await step("timeout: client that never answers -> 'нет ответа клиента за 10 с'; drop after execution -> late confirmation after reconnect", async () => {
    await setSim("sim-07", "no_ack");
    await page.waitForFunction(() => !document.querySelector('tr[data-student="sim-07"] [data-action="lock"]')?.disabled);
    await lockViaRow("sim-07", "Проверка таймаута");
    await lockViaRow("sim-05", "Проверка обрыва после выполнения");
    await waitGroup("sim-07", "pending", 3000);
    await waitGroup("sim-07", "no_connection", 15000);
    assert((await cmdLabel("sim-07")).startsWith("Нет ответа клиента за 10 с — результат неизвестен"), `sim-07 ${await cmdLabel("sim-07")}`);
    await waitGroup("sim-05", "done", 15000);
    assert((await cmdLabel("sim-05")) === "Выполнено (подтверждено после восстановления связи)", `sim-05 ${await cmdLabel("sim-05")}`);
    await shot("10-timeout-and-late-ack.png");
  });

  await step("reconnect: queued lock of the offline student is delivered and confirmed after it comes online", async () => {
    await setOnline("sim-06", true);
    await waitGroup("sim-06", "done", 8000);
    await page.waitForFunction(() => document.querySelector('tr[data-student="sim-06"] [data-testid="lock-label"]')?.textContent === "Заблокирован (по статусу клиента)", null, { timeout: 5000 });
  });

  await step("expired lock is NOT executed after a long disconnect", async () => {
    await setOnline("sim-04", false);
    const r = await apiAs("t-aigerim", "POST", `/api/teacher/control/exams/${demoExam}/commands`, {
      kind: "lock",
      student_ids: ["sim-04"],
      payload: { reason_ru: "Короткий срок" },
      idempotency_key: "e2e-expiry-0001",
      ttl_s: 10,
    });
    assert(r.status === 202 && r.body.results[0].created, "short-ttl lock accepted");
    await waitGroup("sim-04", "no_connection", 4000);
    await waitGroup("sim-04", "error", 15000);
    const label = await cmdLabel("sim-04");
    assert(label.includes("срок действия истёк") && label.includes("не будет выполнена"), `expired label ${label}`);
    await setOnline("sim-04", true);
    await page.waitForTimeout(1500);
    assert((await groupOf("sim-04")) === "error", "still expired after reconnect");
    assert((await lockLabel("sim-04")) === "Не заблокирован (по статусу клиента)", `sim-04 lock ${await lockLabel("sim-04")}`);
    const info = await (await fetch(`${base}/sim/info`)).json();
    assert(info.students.find((s) => s.student_id === "sim-04").locked === false, "simulated client did not lock");
    await shot("11-expired-not-executed.png");
  });

  await step("cancel a queued command before delivery", async () => {
    await setOnline("sim-06", false);
    await row("sim-06").locator('[data-action="unlock"]').click();
    await page.click('[data-testid="dialog-send"]');
    await waitGroup("sim-06", "no_connection", 4000);
    await row("sim-06").locator('[data-testid="details"]').click();
    const drawer = page.locator('[data-testid="student-drawer"]');
    const cancel = drawer.locator("button[data-cancel]").first();
    await cancel.waitFor();
    await cancel.click();
    await waitGroup("sim-06", "cancelled", 5000);
    assert((await cmdLabel("sim-06")) === "Отменена преподавателем до доставки", "cancelled label");
    await page.keyboard.press("Escape");
  });

  await step("unlock works (confirmed by the client)", async () => {
    await row("sim-01").locator('[data-action="unlock"]').click();
    await page.click('[data-testid="dialog-send"]');
    await page.waitForFunction(() => {
      const last = document.querySelector('tr[data-student="sim-01"] [data-testid="last"]');
      return last?.querySelector(".sub")?.textContent?.startsWith("Разблокировать") && last.querySelector('[data-testid="cmd-chip"]')?.getAttribute("data-group") === "done";
    }, null, { timeout: 8000 });
    await page.waitForFunction(() => document.querySelector('tr[data-student="sim-01"] [data-testid="lock-label"]')?.textContent === "Не заблокирован (по статусу клиента)", null, { timeout: 5000 });
  });

  await step("retry after a lost response reuses the idempotency key -> server answers 'repeat', no second command", async () => {
    const bodies = [];
    let first = true;
    await page.route("**/api/teacher/control/exams/*/commands", async (route) => {
      if (route.request().method() !== "POST") return route.continue();
      bodies.push(route.request().postDataJSON());
      if (first) {
        first = false;
        await route.fetch(); // reaches the server...
        return route.abort("failed"); // ...but the answer is lost
      }
      return route.continue();
    });
    await row("sim-01").locator('[data-action="start_exam"]').click();
    await page.click('[data-testid="dialog-send"]');
    const retry = page.locator('[data-testid="cmd-results"] [data-testid="retry-same"]');
    await retry.waitFor();
    assert((await page.locator('[data-testid="cmd-results"]').textContent()).includes("Нет связи с сервером"), "network error shown");
    await retry.click();
    await page.locator('[data-testid="cmd-results"] li[data-outcome="repeat"]').waitFor();
    await page.unroute("**/api/teacher/control/exams/*/commands");
    assert(bodies.length === 2 && bodies[0].idempotency_key === bodies[1].idempotency_key, `same key: ${JSON.stringify(bodies.map((b) => b.idempotency_key))}`);
    const list = await apiAs("t-aigerim", "GET", `/api/teacher/control/exams/${demoExam}/commands?student_id=sim-01`);
    assert(list.body.filter((c) => c.kind === "start_exam").length === 1, "exactly one start_exam command");
    await shot("12-retry-same-key.png");
  });

  await step("server unreachable -> banner, commands disabled; retry restores", async () => {
    await page.route("**/api/teacher/control/**", (route) => route.abort("failed"));
    const banner = page.locator('[data-testid="net-banner"]');
    await banner.waitFor({ state: "visible", timeout: 5000 });
    assert((await banner.textContent()).includes("Нет связи с сервером класса"), "banner text");
    await row("sim-02").locator('input[type="checkbox"]').check();
    assert(await page.locator('[data-bulk="lock"]').isDisabled(), "bulk disabled while offline");
    await page.waitForFunction(() => document.querySelectorAll("tr[data-student] [data-action]:not([disabled])").length === 0);
    assert((await row("sim-01").locator('[data-testid="row-why"]').textContent()).includes("Нет связи с сервером класса"), "row explains why");
    await shot("13-server-unreachable.png");
    await page.unroute("**/api/teacher/control/**");
    await banner.locator("button").click();
    await banner.waitFor({ state: "hidden", timeout: 5000 });
    await row("sim-02").locator('input[type="checkbox"]').uncheck();
  });

  await step("T02 slot module in a real browser (fake ctx, real API): status, actions, DEMO mode", async () => {
    const p2 = await context.newPage();
    p2.on("pageerror", (e) => pageErrors.push(`t02 pageerror: ${e.message}`));
    await p2.goto(`${base}/ui/tests/t02-harness.html?student=sim-03&teacher=t-aigerim`);
    await p2.waitForFunction(() => document.querySelector('[data-testid="t04-lock"]')?.textContent?.startsWith("Экран: "));
    assert((await p2.locator('[data-testid="t04-last"]').textContent()).includes("ошибка"), "last command of sim-03 is the failed lock");
    await p2.click('[data-t04-action="unlock"]');
    await p2.waitForFunction(() => document.querySelector('[data-testid="t04-result"]')?.textContent?.includes("принята сервером"));
    await p2.waitForFunction(() => document.querySelector('[data-testid="t04-last"] [data-group]')?.getAttribute("data-group") === "error" && document.querySelector('[data-testid="t04-last"]')?.textContent?.includes("Разблокировать"), null, { timeout: 8000 });
    await p2.screenshot({ path: path.join(SHOTS, "18-t02-module-harness.png"), fullPage: true });
    await p2.goto(`${base}/ui/tests/t02-harness.html?student=sim-08&teacher=t-aigerim`);
    await p2.waitForFunction(() => document.querySelector('[data-testid="t04-why"]')?.textContent?.includes("Заблокировать: недоступно"));
    assert(await p2.locator('[data-t04-action="lock"]').isDisabled(), "unsupported lock disabled in the module");
    await p2.goto(`${base}/ui/tests/t02-harness.html?mode=demo`);
    await p2.waitForSelector('[data-testid="t04-demo"]');
    assert((await p2.locator('[data-testid="t04-demo"]').textContent()).includes("Недоступно в DEMO-режиме"), "demo text");
    await p2.close();
  });

  await step("journal: who, to whom, when, what (+reason), result; filter by student", async () => {
    await tab("journal");
    await page.waitForFunction(() => document.querySelectorAll('[data-testid="journal-table"] tbody tr').length > 5);
    const rows = await page.locator('[data-testid="journal-table"] tbody tr').evaluateAll((trs) => trs.map((tr) => [...tr.children].map((td) => td.textContent)));
    const lockRow = rows.find((r) => r[1] === "Айгерим Сейтова (преподаватель)" && r[3].includes("«Заблокировать»") && r[3].includes("причина: «Телефон на столе»"));
    assert(lockRow && lockRow[2].includes("Студент 1") && /\d\d:\d\d:\d\d/.test(lockRow[0]) && lockRow[4].includes("срок действия до"), `lock row ${JSON.stringify(lockRow)}`);
    assert(rows.some((r) => r[1] === "клиент/система" && r[4].startsWith("выполнено (подтверждено клиентом)")), "client confirmation row");
    assert(rows.some((r) => r[3].includes("Команда недоступна") && r[2].includes("Студент 8")), "unavailable attempt journaled");
    assert(rows.some((r) => r[3].includes("Повтор запроса")), "duplicate request journaled");
    assert(rows.some((r) => r[3].includes("Команда отменена")), "cancel journaled");
    await page.selectOption('[data-testid="journal-filter"]', "sim-03");
    await page.waitForFunction(() => [...document.querySelectorAll('[data-testid="journal-table"] tbody tr')].every((tr) => tr.children[2].textContent.includes("Студент 3")));
    await shot("14-journal.png");
    await page.selectOption('[data-testid="journal-filter"]', "");
  });

  await step("observer: no enabled command buttons, edit controls disabled, server refuses edits and commands", async () => {
    await page.selectOption("#dev-teacher", "t-observer");
    await tab("students");
    await page.waitForFunction(() => document.querySelector('[data-testid="role"]')?.textContent?.includes("наблюдатель"));
    await page.waitForSelector('tr[data-student="sim-01"]');
    await page.waitForFunction(() => document.querySelector('tr[data-student="sim-01"] [data-testid="row-why"]')?.textContent?.includes("Только просмотр"));
    const enabled = await page.locator("[data-action]:not([disabled]), [data-bulk]:not([disabled]), [data-testid='assign-btn']:not([disabled])").count();
    assert(enabled === 0, `enabled command buttons for observer: ${enabled}`);
    await shot("15-observer-students.png");
    await tab("exam");
    assert(await page.locator('[data-testid="exam-role-note"]').isVisible(), "role note");
    assert(await page.locator('[data-testid="exam-save"]').isDisabled(), "save disabled");
    assert(await page.locator('[data-testid="exam-title"]').isDisabled(), "title disabled");
    await tab("policies");
    assert(await page.locator('[data-testid="policy-new"]').isDisabled(), "create policy disabled");
    const patch = await apiAs("t-observer", "PATCH", `/api/teacher/control/exams/${demoExam}`, { revision: 1, title: "x" });
    assert(patch.status === 403 && patch.body.error.message_ru === "Недостаточно прав для этого действия", `observer edit ${JSON.stringify(patch)}`);
    const cmd = await apiAs("t-observer", "POST", `/api/teacher/control/exams/${demoExam}/commands`, { kind: "unlock", student_ids: ["sim-01"], payload: {} });
    assert(cmd.status === 403, "observer command refused");
    await tab("exam");
    await shot("16-observer-exam.png");
  });

  await step("other teacher (t-bolat): 'нет доступа'", async () => {
    await page.selectOption("#dev-teacher", "t-bolat");
    const panel = page.locator('[data-testid="access-panel"]');
    await panel.waitFor({ state: "visible" });
    const text = await panel.textContent();
    assert(text.includes("Нет доступа к этому экзамену") && text.includes("Болат"), `access panel ${text}`);
    await tab("students");
    assert(!(await page.locator('[data-testid="students-table"]').isVisible()), "no student data for an outsider");
    assert((await page.locator("#exam-select option:checked").textContent()).includes("нет доступа"), "exam select explains");
    await shot("17-other-teacher-no-access.png");
    await page.selectOption("#dev-teacher", "t-aigerim");
    await page.waitForSelector('tr[data-student="sim-01"]');
  });

  await step("delayed client eventually confirms (выполнено after ~12 s)", async () => {
    await waitGroup("sim-02", "done", 20000);
  });

  await step("no JavaScript errors in the page", async () => {
    assert(pageErrors.length === 0, pageErrors.join("\n"));
  });

  await browser.close();
  browser = null;
  stopServer();
  await new Promise((r) => (server.exitCode !== null ? r() : server.once("exit", r)));
  log(`dev server stopped (exit ${server.exitCode ?? server.signalCode})`);
  log(`\n${results.filter((r) => r.ok).length} passed, ${failed} failed; screenshots: ${SHOTS}`);
  if (failed) {
    log("server output:\n" + serverOut.slice(-3000));
    process.exitCode = 1;
  }
})().catch(async (err) => {
  console.error(err);
  if (browser) await browser.close().catch(() => {});
  stopServer();
  process.exitCode = 1;
});
