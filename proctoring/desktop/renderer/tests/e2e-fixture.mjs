// A07 renderer regression on the labelled FixtureBridge (no backend, no camera). NOT an integration test.
// The FixtureBridge mirrors the shell's gating (A06) and the stream/storage split (A05/A08), so the UI paths
// for "journal closed during exam", "late pending from the stream", save races and failures are exercised.
// Usage (from proctoring/desktop):
//   VITE_QORGAU_FIXTURE=1 npx vite build --outDir "$PWD/dist/renderer-fixture"
//   npx vite preview --outDir "$PWD/dist/renderer-fixture" &   then   node renderer/tests/e2e-fixture.mjs [outDir]
// Requires a Playwright install (not a project dependency; see handoffs/A07/DEPENDENCIES.txt).
import { mkdirSync } from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE ?? "playwright");

const BASE = process.env.QORGAU_RENDERER_URL ?? "http://127.0.0.1:4173/";
const OUT = process.argv[2] ?? "e2e-shots";
mkdirSync(OUT, { recursive: true });

const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? ` — ${detail}` : ""}`);
};

async function noOverflow(page, label) {
  const o = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
  check(`no horizontal overflow: ${label}`, o.sw <= o.cw + 1, `${o.sw}/${o.cw}`);
}

async function run(viewport) {
  const tag = `${viewport.width}x${viewport.height}`;
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
  const ctx = await browser.newContext({ viewport, acceptDownloads: true, locale: "ru-RU", reducedMotion: "reduce" });
  const page = await ctx.newPage();
  const consoleErrors = [];
  page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
  page.on("pageerror", (e) => consoleErrors.push(String(e)));
  await page.addInitScript(() => {
    window.__gum = 0;
    const md = navigator.mediaDevices;
    if (md) md.getUserMedia = async () => { window.__gum += 1; throw new Error("blocked by test"); };
  });
  const shot = (n) => page.screenshot({ path: `${OUT}/${tag}-${n}.png` });
  try {
    await flow(page, tag, shot, consoleErrors);
  } catch (e) {
    await shot("zz-failure").catch(() => {});
    throw e;
  } finally {
    await browser.close();
  }
}

async function unlock(page, pin) {
  await page.getByRole("button", { name: "Режим преподавателя", exact: true }).click();
  const hint = await page.getByText(/одноразовый PIN этой вкладки — \d+/).textContent();
  const real = /(\d{6})/.exec(hint ?? "")?.[1];
  await page.getByLabel("PIN").fill(pin ?? real);
  await page.getByRole("button", { name: "Открыть" }).click();
  return real;
}

async function toggleFault(page, label) {
  await page.getByRole("button", { name: "FIXTURE · сбои" }).click();
  await page.getByText(label).click();
  await page.getByRole("button", { name: "FIXTURE · сбои" }).click();
}

async function flow(page, tag, shot, consoleErrors) {
  await page.goto(`${BASE}?bridge=fixture`);
  await page.getByText("FIXTURE-режим").waitFor();
  check(`[${tag}] fixture label visible`, true);
  await page.getByText("Оболочка ещё измеряет возможности защиты").waitFor();
  check(`[${tag}] capabilities "not measured yet" shown, then retried`, true);
  await page.getByText("Защита частичная").waitFor({ timeout: 10000 });
  await shot("01-preflight-form");
  await noOverflow(page, `${tag} preflight`);

  // LIVE must fail preflight on the fixture bridge (no silent substitution).
  await page.getByText("камера (live)").click();
  await page.getByText("Студент ознакомлен").click();
  await page.getByRole("button", { name: "Создать сессию и проверить" }).click();
  await page.getByText("Начать нельзя").waitFor();
  check(`[${tag}] LIVE preflight blocked`, await page.getByRole("button", { name: "К калибровке" }).isDisabled());
  await shot("02-preflight-live-blocked");
  await page.getByRole("button", { name: "Отменить сессию" }).click();
  await page.getByRole("button", { name: "Режим преподавателя…" }).waitFor();

  // Wrong PIN → readable shell reason; correct PIN → teacher.
  await unlock(page, "1234");
  await page.getByText("Неверный PIN.").waitFor();
  check(`[${tag}] wrong PIN rejected with readable reason`, true);
  await page.getByRole("button", { name: "Отмена" }).click();
  await unlock(page);
  await page.getByRole("button", { name: "К итогу →" }).click();
  await page.getByRole("button", { name: "Новая сессия" }).click();

  // SYNTHETIC session.
  await page.getByText("синтетический тест").click();
  await page.getByText("Студент ознакомлен").click();
  await page.getByRole("button", { name: "Создать сессию и проверить" }).click();
  await page.getByText("Обязательные проверки пройдены").waitFor();
  await shot("03-preflight-ready");
  await page.getByRole("button", { name: "К калибровке" }).click();
  await page.getByText("Калибровка взгляда").waitFor();
  // A07-student: full-screen layer, big dots at the screen edges, hint, panel never covers a dot
  const geo = await page.evaluate(() => {
    const r = (el) => { const b = el.getBoundingClientRect(); return { x: b.x, y: b.y, w: b.width, h: b.height, cx: b.x + b.width / 2, cy: b.y + b.height / 2 }; };
    const layer = document.querySelector(".calfs");
    const dots = Object.fromEntries([...document.querySelectorAll(".calfs-dot")].map((d) => [d.dataset.target, r(d)]));
    const panel = document.querySelector(".calfs-panel");
    return { vw: innerWidth, vh: innerHeight, layer: layer && r(layer), dots, panel: panel && r(panel) };
  });
  check(`[${tag}] calibration layer covers the window`, !!geo.layer && geo.layer.w >= geo.vw - 1 && geo.layer.h >= geo.vh - 1, JSON.stringify(geo.layer));
  const d = geo.dots;
  const edgesOk = d.left.cx <= 60 && d.right.cx >= geo.vw - 60 && d.up.cy <= 60 && d.down.cy >= geo.vh - 60 && Math.abs(d.center.cx - geo.vw / 2) < 2 && Math.abs(d.center.cy - geo.vh / 2) < 2;
  check(`[${tag}] calibration dots at the screen edges (centre, left, right, up, down)`, edgesOk, JSON.stringify(Object.fromEntries(Object.entries(d).map(([k, v]) => [k, [Math.round(v.cx), Math.round(v.cy)]]))));
  const hit = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
  const bar = await page.evaluate(() => { const b = document.querySelector(".calfs-bar").getBoundingClientRect(); return { x: b.x, y: b.y, w: b.width, h: b.height }; });
  check(`[${tag}] status panel and buttons cover no calibration dot`, ![geo.panel, bar].some((box) => Object.values(d).some((dot) => hit(box, { x: dot.cx - 40, y: dot.cy - 40, w: 80, h: 80 }))));
  check(`[${tag}] calibration hint shown`, await page.getByText("Смотрите на точку глазами, голову держите прямо.").isVisible());
  check(`[${tag}] no target requested before the student starts`, (await page.locator(".calfs-dot-active").count()) === 0);
  await shot("04a-calibration-intro");
  await page.getByRole("button", { name: "Начать калибровку" }).click();
  await page.locator(".calfs-dot-active").first().waitFor({ timeout: 10000 });
  await shot("04-calibration-collecting");
  const retry = page.getByRole("button", { name: /^Повторить «/ });
  await retry.first().waitFor({ timeout: 20000 });
  check(`[${tag}] calibration failure surfaced`, await page.getByText("Качество кадров недостаточно").isVisible());
  check(`[${tag}] failed dot shown with text`, (await page.locator('.calfs-dot[data-state="failed"]').count()) === 1 && (await page.getByText("не собрано").count()) >= 1);
  await shot("05-calibration-failed-target");
  await retry.first().click();
  await page.waitForFunction(() => {
    const b = [...document.querySelectorAll("button")].find((x) => x.textContent?.includes("Завершить калибровку"));
    return b && !b.disabled;
  }, null, { timeout: 20000 });
  await page.getByRole("button", { name: "Завершить калибровку" }).click();
  await page.getByText("Всё готово к началу").waitFor();
  await shot("06-ready");
  await page.getByRole("button", { name: "Начать экзамен" }).click();

  // Student exam: autosave.
  await page.getByText("Демонстрационный тест").first().waitFor();
  await page.getByText("1024").click();
  await page.getByText("✓ Сохранено").waitFor();
  check(`[${tag}] answer autosaved`, true);
  await page.getByRole("button", { name: "Далее →" }).click();

  // Request race: slow saves in random order while typing → the newest value must win and be "saved".
  await toggleFault(page, "Медленное сохранение");
  const box = page.getByLabel("Ваш ответ");
  for (const ch of "HTTPS") {
    await box.press(ch === ch.toUpperCase() ? `Shift+${ch}` : ch);
    await page.waitForTimeout(550);
  }
  await page.getByText("✓ Сохранено").waitFor({ timeout: 15000 });
  await toggleFault(page, "Медленное сохранение");
  check(`[${tag}] save race settled on latest value`, (await box.inputValue()) === "HTTPS");
  await shot("07-exam-student");
  await noOverflow(page, `${tag} exam`);

  // Disk/storage failure → visible, retried automatically after recovery.
  await toggleFault(page, "Сбой сохранения ответов");
  await page.getByRole("button", { name: "Далее →" }).click();
  await page.getByText("Клавиатура").click();
  await page.locator(".save-state .danger-text").waitFor();
  await shot("08-exam-save-error");
  await toggleFault(page, "Сбой сохранения ответов");
  await page.getByText("✓ Сохранено").waitFor({ timeout: 20000 });
  check(`[${tag}] failed save recovered by retry`, true);

  // Connection loss while answering → value kept, sent after reconnect.
  await toggleFault(page, "Потеря связи с сервисом");
  await page.getByText("Сервис перезапускается").waitFor();
  await page.getByText("Мышь").click();
  await page.getByText("будет отправлен, когда связь восстановится").waitFor();
  await shot("09-exam-offline");
  await toggleFault(page, "Потеря связи с сервисом");
  await page.getByText("✓ Сохранено").waitFor({ timeout: 20000 });
  check(`[${tag}] offline answer sent after reconnect`, true);

  // Teacher console during the exam: journal closed by the shell, live stream only.
  await unlock(page);
  await page.getByText("Наблюдение за сессией").waitFor();
  await page.locator(".preview-box img").waitFor();
  await page.getByText("Идёт экзамен: показан только поток событий").waitFor();
  await page.locator(".inc-item").first().waitFor({ timeout: 20000 });
  await page.waitForTimeout(6000);
  await page.locator(".inc-item").first().click();
  await page.getByText("Решение сейчас записать нельзя").waitFor();
  check(`[${tag}] review not offered while exam mode is active`, (await page.getByRole("button", { name: "Записать решение" }).count()) === 0);
  await shot("10-operator-live-stream-only");
  await noOverflow(page, `${tag} operator`);

  // Camera loss → technical episode.
  await toggleFault(page, "Потеря источника кадров");
  await page.getByText("Наблюдение ухудшено").first().waitFor({ timeout: 10000 });
  await shot("11-camera-lost");
  await toggleFault(page, "Потеря источника кадров");

  // Pause → shell releases exam mode → storage readable, review possible.
  await page.getByRole("button", { name: "Пауза" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Пауза" }).click();
  await page.getByRole("button", { name: "Продолжить" }).waitFor();
  await page.locator(".inc-item").first().click();
  await page.getByRole("radio", { name: "Отклонить" }).waitFor();
  await page.getByRole("radio", { name: "Отклонить" }).click();
  await page.getByLabel("Комментарий").fill("Телефон лежал экраном вниз (тест)");
  await page.getByLabel("Проверяющий").fill("T-1");
  await page.getByRole("button", { name: "Записать решение" }).click();
  await page.getByText("записано в хранилище").waitFor();
  await page.waitForTimeout(1500); // the stream re-sends this incident with review_status "pending" (A05)
  const status = await page.locator(".inc-item").first().locator(".rs").textContent();
  check(`[${tag}] late "pending" from the stream does not roll back the review`, status === "отклонено", status ?? "");
  await shot("12-review-at-pause");
  await page.getByRole("button", { name: "Продолжить" }).click();
  await page.getByText("Идёт экзамен: показан только поток событий").waitFor();

  // Back to the student, finish with an unsaved answer → explicit choice, nothing silently lost.
  await page.getByRole("button", { name: /Преподаватель ✕/ }).click();
  await page.getByText("Демонстрационный тест").first().waitFor();
  await toggleFault(page, "Сбой сохранения ответов");
  await page.locator(".qpill").nth(3).click();
  await page.getByText(".py").click();
  await page.getByRole("button", { name: "Завершить экзамен" }).last().click();
  await page.getByRole("dialog").getByRole("button", { name: "Завершить" }).click();
  await page.getByText(/Не сохранены ответы: №4/).waitFor({ timeout: 15000 });
  check(`[${tag}] finish blocked by an unsaved answer until the user decides`, true);
  await shot("13-finish-unsaved");
  await toggleFault(page, "Сбой сохранения ответов");
  await page.getByRole("button", { name: "Повторить сохранение" }).click();
  await page.getByText("Экзамен завершён").waitFor({ timeout: 15000 });
  check(`[${tag}] finished after the answer was saved`, true);

  // Teacher review after finish → summary → exports (downloads only after the bridge confirmed).
  await page.getByRole("button", { name: "Режим преподавателя…" }).click();
  const pinHint = await page.getByText(/одноразовый PIN этой вкладки — \d+/).count();
  if (pinHint) {
    const t = await page.getByText(/одноразовый PIN этой вкладки — \d+/).textContent();
    await page.getByLabel("PIN").fill(/(\d{6})/.exec(t ?? "")?.[1] ?? "");
    await page.getByRole("button", { name: "Открыть" }).click();
  }
  await page.getByText("Проверка эпизодов").first().waitFor();
  await page.getByText("Решение преподавателя").waitFor();
  await shot("14-review");
  await page.getByRole("button", { name: "К итогу →" }).click();
  await page.getByText("Покрытие наблюдением").waitFor();
  await shot("15-summary");
  await noOverflow(page, `${tag} summary`);
  const [h] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Сохранить отчёт HTML" }).click()]);
  await page.getByText("Файл записан").waitFor();
  check(`[${tag}] HTML export downloaded + confirmed`, /fixture\.html$/.test(h.suggestedFilename()), h.suggestedFilename());
  const [j] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Экспорт JSON" }).click()]);
  check(`[${tag}] JSON export downloaded`, /fixture\.json$/.test(j.suggestedFilename()), j.suggestedFilename());

  // Keyboard: dialogs trap focus and close on Escape, focus returns to the opener.
  await page.getByRole("button", { name: "Удалить данные сессии…" }).focus();
  await page.keyboard.press("Enter");
  await page.getByRole("dialog").waitFor();
  for (let k = 0; k < 6; k++) await page.keyboard.press("Tab");
  const inDialog = await page.evaluate(() => !!document.activeElement?.closest("[role=dialog]"));
  await page.keyboard.press("Escape");
  const back = await page.evaluate(() => document.activeElement?.textContent ?? "");
  check(`[${tag}] dialog focus trap + Escape + focus restore`, inDialog && back.includes("Удалить данные"), back);

  const gum = await page.evaluate(() => window.__gum);
  check(`[${tag}] getUserMedia never called`, gum === 0, String(gum));
  check(`[${tag}] no console errors`, consoleErrors.length === 0, consoleErrors.slice(0, 3).join(" | "));
}

for (const vp of [{ width: 1366, height: 768 }, { width: 1920, height: 1080 }]) {
  try {
    await run(vp);
  } catch (e) {
    check(`flow ${vp.width}x${vp.height} completed`, false, String(e).slice(0, 400));
  }
}
const failed = results.filter((r) => !r.ok).length;
console.log(`\n${results.length - failed}/${results.length} checks passed`);
process.exit(failed ? 1 : 0);
