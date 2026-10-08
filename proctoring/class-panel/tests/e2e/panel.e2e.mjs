// T02 panel checks in headless Chromium (Playwright, not a project dependency).
//   node proctoring/class-panel/tests/e2e/panel.e2e.mjs [outDir]
//   PLAYWRIGHT_MODULE=/path/to/playwright  (default: global install)
// Starts serve.mjs on a free loopback port. DEMO checks verify the PANEL (rendering, states, keyboard,
// stability) on simulated data; the REAL-adapter check uses Playwright route mocks of the contract endpoints
// (test doubles, not a C1 server). Nothing here measures real cameras or a real class network.
import { spawn } from "node:child_process";
import { mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import { createServer } from "node:net";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE ?? "/opt/node22/lib/node_modules/playwright");
const here = dirname(fileURLToPath(import.meta.url));
const panelDir = resolve(here, "..", "..");
const OUT = resolve(process.argv[2] ?? "class-panel-e2e");
mkdirSync(OUT, { recursive: true });

const results = [];
const measured = {};
const check = (name, ok, detail = "") => {
  results.push({ name, ok: !!ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? ` — ${detail}` : ""}`);
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const port = await new Promise((res) => {
  const s = createServer();
  s.listen(0, "127.0.0.1", () => {
    const p = s.address().port;
    s.close(() => res(p));
  });
});
const server = spawn(process.execPath, [resolve(panelDir, "serve.mjs"), "--port", String(port)], { stdio: "ignore" });
await sleep(400);
const BASE = `http://127.0.0.1:${port}/`;

const browser = await chromium.launch(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {});

async function newPage(opts = {}) {
  const ctx = await browser.newContext({ viewport: { width: 1366, height: 768 }, locale: "ru-RU", ...opts });
  const page = await ctx.newPage();
  const errors = [];
  page.on("console", async (m) => {
    if (m.type() !== "error") return;
    // Some Windows antivirus installs rewrite page CSP. Record that separately only when the
    // injected origin is visible in the actual DOM; never discard application errors wholesale.
    if (m.text().includes("Content-Security-Policy directive 'child-src'") && m.text().includes("'none' alongside")) {
      const csp = await page.locator('meta[http-equiv="Content-Security-Policy"]').getAttribute("content").catch(() => "");
      if (csp?.includes("kaspersky-labs.com")) {
        measured.antivirusCspWarnings = (measured.antivirusCspWarnings ?? 0) + 1;
        return;
      }
    }
    errors.push(m.text());
  });
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.addInitScript(() => {
    window.__lt = [];
    try {
      new PerformanceObserver((l) => window.__lt.push(...l.getEntries().map((e) => e.duration))).observe({ type: "longtask", buffered: true });
    } catch {}
  });
  return { ctx, page, errors };
}

const visibleCards = (page) => page.evaluate(() => [...document.querySelectorAll(".grid > li.card")].filter((e) => !e.hidden).length);
const noOverflow = async (page, label) => {
  const o = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
  check(`no horizontal overflow: ${label}`, o.sw <= o.cw + 1, `${o.sw}/${o.cw}`);
};
const truncatedZones = (page) =>
  page.evaluate(() => [...document.querySelectorAll(".grid > li.card:not([hidden]) .zone-text")].filter((e) => e.scrollWidth > e.clientWidth + 1).length);

async function demoChecks() {
  for (const n of [2, 30, 100]) {
    const { ctx, page, errors } = await newPage();
    await page.goto(`${BASE}?students=${n}&debug=1`);
    await page.waitForFunction((k) => window.__classPanel?.store.loaded && window.__classPanel.metrics().cards === k, n, { timeout: 10000 });
    await sleep(2500);
    check(`[${n}] all cards rendered`, (await visibleCards(page)) === n, `${await visibleCards(page)}`);
    const total = await page.locator(".counters .ctr").first().textContent();
    check(`[${n}] counter "Студентов" = ${n}`, total?.includes(String(n)), total ?? "");
    check(`[${n}] DEMO is labelled (strip + badge)`, (await page.locator(".demo-strip").isVisible()) && (await page.locator(".mode-demo").isVisible()));
    check(`[${n}] zone labels not truncated at 1366×768`, (await truncatedZones(page)) === 0);
    await noOverflow(page, `${n} students`);
    await page.screenshot({ path: `${OUT}/demo-${n}.png` });
    if (n === 100) {
      // Measured on the machine running this script with synthetic DEMO data.
      await page.evaluate(() => (window.__lt = []));
      const samples = [];
      const flush0 = await page.evaluate(() => window.__classPanel.store.flushCount);
      for (let i = 0; i < 50; i++) {
        samples.push(await page.evaluate(() => window.__classPanel.store.lastFlushMs));
        await sleep(200);
      }
      const flushes = (await page.evaluate(() => window.__classPanel.store.flushCount)) - flush0;
      const lt = await page.evaluate(() => window.__lt);
      const sorted = [...samples].sort((a, b) => a - b);
      measured.flush100 = {
        flushes_in_10s: flushes,
        render_ms_avg: +(samples.reduce((a, b) => a + b, 0) / samples.length).toFixed(2),
        render_ms_p95: +sorted[Math.floor(sorted.length * 0.95)].toFixed(2),
        render_ms_max: +sorted[sorted.length - 1].toFixed(2),
        long_tasks_over_50ms: lt.length,
        dom_nodes: (await page.evaluate(() => window.__classPanel.metrics())).domNodes,
      };
      check("[100] render per flush p95 < 16 ms (measured, headless)", measured.flush100.render_ms_p95 < 16, JSON.stringify(measured.flush100));
      await page.getByText("Вид и порядок", { exact: true }).click();
      await page.getByRole("button", { name: "Превью", exact: true }).click();
      await sleep(600);
      await page.screenshot({ path: `${OUT}/demo-100-compact.png` });
      check("[100] compact view (no previews) keeps all cards", (await visibleCards(page)) === 100);
    }
    check(`[${n}] no console errors`, errors.length === 0, errors.slice(0, 2).join(" | "));
    await ctx.close();
  }

  // ------------------------------------------------------------ stability under many events
  {
    const { ctx, page } = await newPage();
    await page.goto(`${BASE}?students=100&rate=busy&debug=1`);
    await page.waitForFunction(() => window.__classPanel?.store.loaded);
    await sleep(1500);
    const order = () => page.evaluate(() => [...document.querySelectorAll(".grid > li.card")].map((e) => e.dataset.id).join(","));
    let prev = await order();
    let changes = 0;
    const f0 = await page.evaluate(() => window.__classPanel.store.flushCount);
    for (let i = 0; i < 50; i++) {
      await sleep(200);
      const cur = await order();
      if (cur !== prev) changes += 1;
      prev = cur;
    }
    const flushes = (await page.evaluate(() => window.__classPanel.store.flushCount)) - f0;
    measured.stability = { order_changes_in_10s: changes, ui_updates_in_10s: flushes };
    check("cards do not jump on every event (≤ 7 order changes in 10 s at 100 students, busy)", changes <= 7 && flushes > 20, JSON.stringify(measured.stability));
    await page.getByText("Вид и порядок", { exact: true }).click();
    await page.getByRole("button", { name: /Закрепить порядок/ }).click();
    prev = await order();
    let pinnedChanges = 0;
    for (let i = 0; i < 25; i++) {
      await sleep(200);
      const cur = await order();
      if (cur !== prev) pinnedChanges += 1;
      prev = cur;
    }
    check("pinned order: no moves at all", pinnedChanges === 0, `${pinnedChanges}`);
    await ctx.close();
  }

  // ------------------------------------------------------------ events, disconnect, server loss, errors, empty
  {
    const { ctx, page, errors } = await newPage();
    await page.goto(`${BASE}?students=30&debug=1`);
    await page.waitForFunction(() => window.__classPanel?.store.loaded);
    await sleep(800);
    await page.getByRole("button", { name: "DEMO · управление" }).click();
    const firstId = await page.evaluate(() => [...window.__classPanel.store.students.keys()].sort()[0]);
    const before = await page.evaluate((id) => window.__classPanel.store.students.get(id).view.incidentsTotal, firstId);
    await page.getByRole("button", { name: "Событие у первого студента" }).click();
    await sleep(300);
    const flashed = await page.evaluate((id) => document.querySelector(`.grid > li.card[data-id="${id}"]`)?.classList.contains("flash"), firstId);
    check("new event highlights the card in place", flashed === true);
    await page.waitForFunction(([id, b]) => window.__classPanel.store.students.get(id).view.incidentsTotal > b, [firstId, before], { timeout: 4000 });
    check("episode counter updates from the next status", true);

    const offlineBefore = await page.evaluate(() => [...document.querySelectorAll('.grid > li.card .link[data-link="offline"]')].map((l) => l.closest("li")?.querySelector(".name")?.textContent));
    await page.getByRole("button", { name: "Отключить случайного студента" }).click();
    const who = await page
      .waitForFunction(() => /DEMO: (.+) отключён/.exec(document.querySelector(".sr-only[role=status]")?.textContent ?? "")?.[1], null, { timeout: 5000 })
      .then((h) => h.jsonValue());
    check("DEMO disconnect announced (trailing throttle never drops the latest message)", !!who && !offlineBefore.includes(who), who ?? "");
    const t0 = Date.now();
    await page.waitForFunction(
      (name) => {
        const card = [...document.querySelectorAll(".grid > li.card")].find((c) => c.querySelector(".name")?.textContent === name);
        return card && card.classList.contains("z-grey") && card.querySelector(".link")?.getAttribute("data-link") === "offline";
      },
      who,
      { timeout: 25000 },
    );
    check("silent student → grey + 'нет связи' (server rule)", true, `${Math.round((Date.now() - t0) / 1000)} с после отключения`);
    const inQueue = await page.locator(".q-item", { hasText: who }).locator(".q-why").textContent();
    check("disconnected student is in the attention queue with the reason", /Нет связи со студентом/.test(inQueue ?? ""), inQueue ?? "");
    const offCtr = await page.locator(".ctr-offline strong").textContent();
    check("counter 'без связи' ≥ 1", Number(offCtr) >= 1, offCtr ?? "");
    await page.screenshot({ path: `${OUT}/demo-30-disconnect.png` });

    await page.getByRole("button", { name: "Потеря связи с сервером" }).click();
    await sleep(400);
    const banner = await page.locator(".banner").textContent();
    check("server loss → visible banner, data marked old", /Нет связи с сервером класса/.test(banner ?? ""), (banner ?? "").slice(0, 80));
    const onlineDuring = await page.locator(".ctr-online strong").textContent();
    check("server loss → no 'на связи' numbers are claimed", onlineDuring === "—", onlineDuring ?? "");
    const staleAll = await page.evaluate(() => [...document.querySelectorAll(".grid > li.card:not([hidden])")].every((c) => c.classList.contains("stale") && c.classList.contains("z-grey")));
    check("server loss → every card grey/stale (no current colours)", staleAll);
    const camTexts = await page.evaluate(() => [...new Set([...document.querySelectorAll(".grid > li.card:not([hidden]) .cam")].map((c) => c.textContent))]);
    check("server loss → no card claims 'Камера работает'", !camTexts.includes("Камера работает"), camTexts.join(" | "));
    check("server loss → attention queue says it is unavailable (not flooded)", (await page.locator(".q-item").count()) === 0 && /очередь недоступна/.test((await page.locator(".q-empty").textContent()) ?? ""));
    await page.screenshot({ path: `${OUT}/demo-30-server-lost.png` });
    await page.getByRole("button", { name: "Потеря связи с сервером" }).click();
    await page.waitForFunction(() => document.querySelector(".banner")?.hidden === true, null, { timeout: 4000 });
    check("server back → banner gone, live again", true);

    await page.getByRole("button", { name: "Ошибка при следующей загрузке" }).click();
    await page.getByRole("button", { name: "30", exact: true }).click();
    await sleep(300);
    const err = await page.locator(".banner").textContent();
    check("load error → error banner with retry", /Не удалось загрузить класс/.test(err ?? "") && (await page.getByRole("button", { name: "Повторить сейчас" }).isVisible()));
    await page.getByRole("button", { name: "Повторить сейчас" }).click();
    await page.waitForFunction(() => document.querySelector(".banner")?.hidden === true, null, { timeout: 4000 });
    check("retry recovers", (await visibleCards(page)) === 30);

    await page.getByRole("button", { name: "пустой" }).click();
    await sleep(300);
    check("empty class → explanation, counters 0", (await page.locator(".grid-empty").textContent())?.includes("В классе пока нет студентов") && (await page.locator(".counters .ctr").first().textContent())?.includes("0"));
    await page.screenshot({ path: `${OUT}/demo-empty.png` });
    check("no console errors (events/disconnect/server loss)", errors.length === 0, errors.slice(0, 2).join(" | "));
    await ctx.close();
  }

  // ------------------------------------------------------------ keyboard, dialog, filters, queue
  {
    const { ctx, page, errors } = await newPage();
    await page.goto(`${BASE}?students=30&debug=1`);
    await page.waitForFunction(() => window.__classPanel?.store.loaded);
    await sleep(800);
    await page.focus('.card-hit[tabindex="0"]');
    const a = await page.evaluate(() => document.activeElement?.dataset.id);
    await page.keyboard.press("ArrowRight");
    const b = await page.evaluate(() => document.activeElement?.dataset.id);
    const domOrder = await page.evaluate(() => [...document.querySelectorAll(".grid > li.card")].map((e) => e.dataset.id));
    check("arrow keys move focus across cards (roving tabindex)", a !== b && domOrder.indexOf(b) === domOrder.indexOf(a) + 1);
    const columns = await page.locator(".grid").evaluate(el => getComputedStyle(el).gridTemplateColumns.split(" ").length);
    await page.keyboard.press("ArrowDown");
    const c = await page.evaluate(() => document.activeElement?.dataset.id);
    check("ArrowDown moves one row", domOrder.indexOf(c) === domOrder.indexOf(b) + columns, `${domOrder.indexOf(b)}→${domOrder.indexOf(c)}; ${columns} columns`);
    await page.keyboard.press("Enter");
    await page.locator(".drawer").waitFor();
    check("Enter opens the student dialog, focus on 'Закрыть'", (await page.evaluate(() => document.activeElement?.getAttribute("aria-label"))) === "Закрыть карточку");
    for (let i = 0; i < 12; i++) await page.keyboard.press("Tab");
    check("Tab stays inside the dialog", await page.evaluate(() => !!document.activeElement?.closest(".drawer")));
    const slots = await page.locator(".dr-slot h3").allTextContents();
    check("dialog shows history and omits uninstalled command/audio controls", slots.length === 1 && slots[0] === "История событий", slots.join(" | "));
    check("dialog makes background controls inert", await page.locator("#app").evaluate(el => [...el.children].filter(child => !child.classList.contains("drawer-overlay")).every(child => child.inert)));
    await page.locator(".ep-list li").first().waitFor({ timeout: 3000 }).catch(() => {});
    await page.screenshot({ path: `${OUT}/demo-dialog.png` });
    await page.keyboard.press("Escape");
    check("Escape closes and returns focus to the card", (await page.locator(".drawer-overlay").isHidden()) && (await page.evaluate(() => document.activeElement?.dataset.id)) === c);

    await page.keyboard.press("/");
    check("'/' focuses search", await page.evaluate(() => document.activeElement?.id === "q"));
    await page.keyboard.type("ПК-07");
    await sleep(400);
    check("search by computer name", (await visibleCards(page)) === 1);
    await page.locator("#q").fill("");
    await sleep(300);
    await page.locator(".chip.z-red").click();
    await sleep(300);
    const onlyRed = await page.evaluate(() => [...document.querySelectorAll(".grid > li.card:not([hidden])")].every((c) => c.classList.contains("z-red")));
    check("status filter shows only that status", onlyRed && (await visibleCards(page)) > 0);
    check("status chip reflects state for assistive tech", (await page.locator(".chip.z-red").getAttribute("aria-pressed")) === "true");
    await page.getByRole("button", { name: "Сбросить фильтры" }).click();
    await sleep(300);
    const qBefore = await page.locator(".q-item").count();
    await page.locator(".q-ack").first().click();
    await sleep(300);
    check("'Просмотрено' removes the entry from the queue", (await page.locator(".q-item").count()) === qBefore - 1, `${qBefore}→${await page.locator(".q-item").count()}`);
    await page.getByText("Вид и порядок", { exact: true }).click();
    await page.selectOption("#sort", "computer");
    await sleep(300);
    const names = await page.evaluate(() => [...document.querySelectorAll(".grid > li.card .computer")].slice(0, 3).map((e) => e.textContent));
    check("sort by computer number", names.join() === "ПК-01,ПК-02,ПК-03", names.join());
    check("no console errors (keyboard/filters)", errors.length === 0, errors.slice(0, 2).join(" | "));
    await ctx.close();
  }

  // 1920 wide
  {
    const { ctx, page } = await newPage({ viewport: { width: 1920, height: 1080 } });
    await page.goto(`${BASE}?students=30`);
    await sleep(1500);
    await page.screenshot({ path: `${OUT}/demo-30-1920.png` });
    check("[1920] zone labels not truncated", (await truncatedZones(page)) === 0);
    await ctx.close();
  }
}

async function reducedMotionCheck() {
  const { ctx, page } = await newPage({ reducedMotion: "reduce" });
  await page.goto(`${BASE}?students=30&debug=1`);
  await page.waitForFunction(() => window.__classPanel?.store.loaded);
  await sleep(600);
  await page.getByRole("button", { name: "DEMO · управление" }).click();
  await page.getByRole("button", { name: "Событие у первого студента" }).click();
  await sleep(200);
  const anim = await page.evaluate(() => {
    const c = document.querySelector(".grid > li.card.flash");
    return c ? getComputedStyle(c).animationName : "no-flash";
  });
  check("reduced motion: no animation on event highlight", anim === "none", anim);
  await ctx.close();
}

async function realAdapterChecks() {
  // contract endpoints answered by Playwright route mocks (test doubles of C1)
  {
    const { ctx, page, errors } = await newPage();
    const cards = [
      { student_id: "st-1", computer_name: "ПК-01", student_label: "Студент 01", exam_state: "running", camera: "ok", monitoring: "ok", zone: "green", zone_reasons_ru: [], incidents_total: 0, incidents_by_priority: { low: 0, medium: 0, high: 0 }, locked: false, mic_active: false },
      { student_id: "st-2", computer_name: "ПК-02", student_label: "Студент 02", exam_state: "running", camera: "unknown", monitoring: "degraded", zone: "yellow", zone_reasons_ru: ["Телефон в кадре — 10:01"], incidents_total: 1, incidents_by_priority: { low: 0, medium: 1, high: 0 }, locked: false, mic_active: false },
    ];
    await page.route("**/api/teacher/students", (r) => r.fulfill({ json: cards }));
    await page.route("**/api/teacher/session", (r) => r.fulfill({ json: null }));
    await page.route("**/api/teacher/students/*/incidents", (r) => r.fulfill({ json: [] }));
    let wsRef = null;
    await page.routeWebSocket(/\/ws\/teacher$/, (ws) => {
      wsRef = ws;
    });
    await page.goto(`${BASE}?adapter=real&debug=1`);
    await page.waitForFunction(() => window.__classPanel?.store.connection.status === "live", null, { timeout: 8000 });
    check("REAL (mock C1): list loaded via GET /api/teacher/students", (await visibleCards(page)) === 2);
    check("REAL: no DEMO strip, mode badge says server", !(await page.locator(".demo-strip").count()) && (await page.locator(".mode-real").isVisible()));
    const st2 = await page.locator('.grid > li.card[data-id="st-2"]').getAttribute("class");
    check("REAL: camera 'unknown' → grey (server rule), not 'работает'", /z-grey/.test(st2 ?? "") && (await page.locator('.grid > li.card[data-id="st-2"] .cam').textContent())?.includes("неизвестно"));
    wsRef?.send(JSON.stringify({ type: "student_update", v: 1, student: { student_id: "st-1", zone: "red", zone_reasons_ru: ["Второе лицо в кадре — 10:05"], incidents_total: 1, exam_state: "running", camera: "ok" } }));
    wsRef?.send(JSON.stringify({ type: "incident", v: 1, student_id: "st-1", incident_id: "inc-1", rule_id: "multiple_faces", priority: "high", state: "open", explanation_ru: "Второе лицо в кадре" }));
    await page.waitForFunction(() => document.querySelector('.grid > li.card[data-id="st-1"]')?.classList.contains("z-red"), null, { timeout: 3000 });
    check("REAL: student_update over /ws/teacher changes the card", true);
    const unrev = await page.locator('.grid > li.card[data-id="st-1"] .episodes').textContent();
    check("REAL: 'без решения' not invented when the server does not send it", !/без решения/.test(unrev ?? ""), unrev ?? "");
    await page.screenshot({ path: `${OUT}/real-mock.png` });
    check("REAL: no console errors", errors.length === 0, errors.slice(0, 2).join(" | "));
    await ctx.close();
  }
  {
    const { ctx, page } = await newPage();
    await page.route("**/api/teacher/students", (r) => r.fulfill({ status: 401, json: {} }));
    await page.routeWebSocket(/\/ws\/teacher$/, () => {});
    await page.goto(`${BASE}?adapter=real&debug=1`);
    await page.waitForFunction(() => window.__classPanel?.store.connection.status === "auth", null, { timeout: 8000 });
    check("REAL: 401 → login-needed message, no data invented", (await page.locator(".banner").textContent())?.includes("Нужен вход преподавателя") && (await visibleCards(page)) === 0);
    await ctx.close();
  }
}

try {
  await demoChecks();
  await reducedMotionCheck();
  await realAdapterChecks();
} catch (e) {
  check("e2e run completed without exception", false, String(e?.stack ?? e).slice(0, 400));
} finally {
  await browser.close();
  server.kill();
}

const failed = results.filter((r) => !r.ok);
console.log(`\nMEASURED (headless Chromium, ${process.platform}/${process.arch}, DEMO data): ${JSON.stringify(measured)}`);
console.log(`class-panel e2e: ${results.length - failed.length}/${results.length} PASS — screenshots in ${OUT}`);
process.exit(failed.length ? 1 : 0);
